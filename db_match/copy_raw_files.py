#!/usr/bin/env python3
r"""
copy_raw_files.py: copies finished .raw acquisitions to the server
=================================================================

Windows Task Scheduler runs this script every 15 minutes and at boot (see
install_copy_task.ps1). Each run does ONE pass and then exits:

    1. Take the run lock (CACHE_DIR\copy_service.lock) so two runs never overlap.
    2. Open the matching DB (MATCHING_DB_PATH). It is created on the first run.
       This small SQLite database records every .raw file the script has seen,
       what happened to it, and a history of events.
    3. Read requests the sample database left in CACHE_DIR\requests
       ("retry this file", "ignore this file", "stop ignoring it").
    4. Read every (project code, experiment code) pair from the sample database
       (ACQUISITIONS_DB_PATH). Only files whose name contains one of these pairs
       are copied.
    5. Finish files from earlier runs that were copied but not completely
       finished (source not yet renamed to C_..., or not yet inserted into the
       sample database).
    6. Scan SOURCE_DIRS for *.raw files and decide what to do with each one:

         ignored   the name does not follow the pattern, the instrument letter is
                   unknown, the project or experiment is not in the sample
                   database, or the file is smaller than MIN_SIZE_BYTES.
                   Most of these are checked again on every run, so a file is
                   picked up once someone creates its experiment.
         waiting   the file changed less than MIN_UNCHANGED_MINUTES ago
                   (the instrument may still be writing it).
         locked    another program still has the file open (Xcalibur, a
                   viewer, an antivirus scan, ...). It is tried again next run.
         copying   the copy is in progress.
         copied    the copy is verified on the server.
         failed    the copy or the verification failed. It is retried next
                   run, up to MAX_ATTEMPTS times.

    7. Copy each eligible file (see copy_one_file below for the details):

         a. Open the source with a Windows "deny write" share mode. If that
            fails, someone is writing the file, so it is "locked". While we
            hold this handle nobody can change, rename or delete the file, so
            what we hash is exactly what rsync copies.
         b. Compute the SHA-256 of the source.
         c. rsync it to   <DESTINATION_ROOT>\<instrument folder>\<YYYY>\<YYMM>\<YYMMDD>\<name>.partial
         d. Read the copy back from the server and compute its SHA-256.
         e. The hashes differ, so the copy is corrupt: delete the .partial
            file and mark the file "failed" (it is retried next run).
            The hashes match: rename .partial to the real name. People on the
            server therefore only ever see complete, verified files.
         f. Rename the source on the instrument PC to C_<name> so everyone
            can see that it is safely copied (the copy on the server keeps
            the original name, without C_).
         g. Insert an acquired_file row into the sample database (project,
            experiment, server location, size, instrument, date, user).

    8. Write CACHE_DIR\status.json (heartbeat, live progress, counts of this
       run). The "Copy status" page of the sample database reads it.

Why rsync, plus our own SHA-256?
    rsync writes into a temporary file, checks every transfer with its own
    checksum, and resumes cleanly after network drops (--partial-dir). We also
    compare SHA-256 hashes of the source and of the copy read back from the
    server. That catches corruption that happens after the transfer (a bad
    share, a disk problem, or ransomware touching the file). The hash is stored
    in the matching DB so the copy can be checked again later.

Requirements on the instrument PC
    * Python 3.9+ (standard library only, nothing to pip install).
    * rsync for Windows, e.g. cwRsync (https://itefix.net/cwrsync). Set
      RSYNC_EXE below. cwRsync is a Cygwin build, so Windows paths must be
      converted (C:\x becomes /cygdrive/c/x). RSYNC_PATH_STYLE does that.
    * The account that runs the task needs read and rename (modify)
      permission on SOURCE_DIRS and write permission on DESTINATION_ROOT,
      MATCHING_DB_PATH, CACHE_DIR and ACQUISITIONS_DB_PATH.

Usage
    python copy_raw_files.py            one run (this is what the task does)
    python copy_raw_files.py --dry-run  only show what would be done (copies,
                                        renames and inserts nothing)
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import logging
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

# ════════════════════════════════════════════════════════════════════════════
# 1. SETTINGS: change these for your installation
#    Every value can also be overridden with an environment variable of the
#    same name (useful for testing). Normally you just edit the default.
# ════════════════════════════════════════════════════════════════════════════
def _setting(name: str, default: str) -> str:
    return os.environ.get(name, default)


# Where the copies go (the server share). The script creates sub folders:
#   <DESTINATION_ROOT>\<instrument folder>\<YYYY>\<YYMM>\<YYMMDD>\<file name>
DESTINATION_ROOT = Path(_setting("DESTINATION_ROOT", r"\\ms-server\RawData"))

# Folders on the instrument PC where the acquisition software writes the .raw
# files. They are searched recursively. Separate several with ";".
SOURCE_DIRS = [Path(p) for p in _setting("SOURCE_DIRS", r"D:\Data").split(";") if p.strip()]

# The sample database (the web app's database). Read for project/experiment
# codes, and copied files are inserted into its acquired_file table.
ACQUISITIONS_DB_PATH = Path(_setting("ACQUISITIONS_DB_PATH", r"/home/lutfia95/sample-tracker-annotation/sample-tracker/acquisitions.db"))

# Set this to "astral", "zoom" or "raptor" on the corresponding instrument PC.
# Each PC runs one service and writes ONLY its own matching DB and cache.
COPY_INSTRUMENT = _setting("COPY_INSTRUMENT", "astral").strip().lower()
MATCHING_DB_PATHS = {
    "astral": r"/data/new_storage/Ahmad/sample_database_inter/matching_db_astral.sqlite",
    "zoom": r"/data/new_storage/Ahmad/sample_database_inter/matching_db_zoom.sqlite",
    "raptor": r"/data/new_storage/Ahmad/sample_database_inter/matching_db_raptor.sqlite",
}
CACHE_DIRS = {
    "astral": r"/data/new_storage/Ahmad/sample_database_inter/cache_astral",
    "zoom": r"/data/new_storage/Ahmad/sample_database_inter/cache_zoom",
    "raptor": r"/data/new_storage/Ahmad/sample_database_inter/cache_raptor",
}
if COPY_INSTRUMENT not in MATCHING_DB_PATHS:
    raise ValueError("COPY_INSTRUMENT must be astral, zoom or raptor")

# Created on first run. The web app reads the same file (COPY_MATCHING_DB_PATHS).
MATCHING_DB_PATH = Path(_setting("MATCHING_DB_PATH", MATCHING_DB_PATHS[COPY_INSTRUMENT]))

# Separate per instrument: status.json (heartbeat/progress), requests/ (web
# actions), logs/ and copy_service.lock. Match COPY_CACHE_DIRS in app.py.
CACHE_DIR = Path(_setting("CACHE_DIR", CACHE_DIRS[COPY_INSTRUMENT]))

# rsync executable and how it wants paths:
#   "cygwin"  cwRsync / Cygwin / MSYS2:  C:\x   -> /cygdrive/c/x,  \\srv\s -> //srv/s
#   "native"  pass paths unchanged (Linux/macOS, or for testing)
RSYNC_EXE = _setting("RSYNC_EXE", r"C:\Program Files\cwRsync\bin\rsync.exe")
RSYNC_PATH_STYLE = _setting("RSYNC_PATH_STYLE", "cygwin" if os.name == "nt" else "native")
# rsync gives up if no data moves for this many seconds (network stalled).
RSYNC_IO_TIMEOUT_SECONDS = int(_setting("RSYNC_IO_TIMEOUT_SECONDS", "600"))

# First part of the file name -> folder on the server.
INSTRUMENT_FOLDERS = {
    "A": "Astral1",
    "Z": "Astral2-ZOOM",
    "R": "Raptor",
}

# Copy rules.
COPY_EXTENSION = ".raw"                                   # only these files
MIN_SIZE_BYTES = int(float(_setting("MIN_SIZE_MB", "300")) * 1024 * 1024)  # 300 MB (as Windows Explorer counts)
MIN_UNCHANGED_MINUTES = float(_setting("MIN_UNCHANGED_MINUTES", "15"))     # file must be untouched this long
MAX_ATTEMPTS = int(_setting("MAX_ATTEMPTS", "5"))         # failed copies are retried this often
COPIED_PREFIX = "C_"                                      # source gets renamed to C_<name> after the copy
PARTIAL_SUFFIX = ".partial"                               # copy name on the server until verified
RUN_INTERVAL_MINUTES = 15                                 # only used to show "next run" in the web app

# Hashing reads files in chunks of this size.
HASH_CHUNK_BYTES = 8 * 1024 * 1024


# ════════════════════════════════════════════════════════════════════════════
# 2. FILE NAME PARSING
#    A_20260721-001_GMB_HKI_NatGel_Pool.raw
#    | |        |   |   |   |      +-- optional variable-condition parts (ignored)
#    | |        |   |   |   +--------- experiment code   (NatGel)
#    | |        |   |   +------------- user initials     (HKI)
#    | |        |   +----------------- project code      (GMB)
#    | |        +--------------------- run number        (001)
#    | +------------------------------ acquisition date  (2026-07-21) -> folder 2026\2607\260721
#    +-------------------------------- instrument letter (A -> Astral1)
# ════════════════════════════════════════════════════════════════════════════
_NAME_RE = re.compile(
    r"^(?P<instrument>[A-Za-z0-9]+)_(?P<date>\d{8})-(?P<run>\d+)_"
    r"(?P<project>[A-Za-z0-9]+)_(?P<user>[^_]+)_(?P<experiment>[A-Za-z0-9]+)"
    r"(?:_.*)?$"
)


@dataclass(frozen=True)
class ParsedName:
    instrument: str
    acq_date: date
    run: str
    project: str
    user: str
    experiment: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.project, self.experiment)


def parse_file_name(file_name: str) -> ParsedName | None:
    """Split a file name into its parts. Returns None if it does not fit."""
    stem = file_name[: -len(COPY_EXTENSION)] if file_name.lower().endswith(COPY_EXTENSION) else file_name
    match = _NAME_RE.match(stem)
    if not match:
        return None
    try:
        acq_date = datetime.strptime(match["date"], "%Y%m%d").date()
    except ValueError:  # e.g. 20261345
        return None
    return ParsedName(match["instrument"], acq_date, match["run"], match["project"],
                      match["user"], match["experiment"])


def destination_for(parsed: ParsedName, file_name: str) -> Path:
    """<DESTINATION_ROOT>\\Astral1\\2026\\2607\\260721\\A_20260721-001_....raw"""
    return (DESTINATION_ROOT / INSTRUMENT_FOLDERS[parsed.instrument]
            / f"{parsed.acq_date:%Y}" / f"{parsed.acq_date:%y%m}"
            / f"{parsed.acq_date:%y%m%d}" / file_name)


# ════════════════════════════════════════════════════════════════════════════
# 3. MATCHING DB: our own record of every file
#    copy_file   one row per source file (current state)
#    copy_event  append-only history ("what happened when"), shown in the web app
#    copy_run    one row per script run
#    The web app only reads these tables. It never writes them. Changes it
#    wants go through CACHE_DIR\requests (see section 8).
# ════════════════════════════════════════════════════════════════════════════
MATCHING_SCHEMA = """
CREATE TABLE IF NOT EXISTS copy_file (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path      TEXT NOT NULL UNIQUE,     -- original path on the instrument PC (without C_)
    file_name        TEXT NOT NULL,            -- original name (= name on the server)
    instrument       TEXT,                     -- letter from the name, e.g. A
    instrument_folder TEXT,                    -- e.g. Astral1
    project_code     TEXT,
    experiment_code  TEXT,
    user_initials    TEXT,                     -- from the file name (who acquired it)
    contact          TEXT,                     -- experiment.user_initials in the sample database
    acq_date         TEXT,                     -- YYYY-MM-DD from the file name
    size_bytes       INTEGER,
    mtime            REAL,                     -- last-modified time (unix seconds) when last seen
    status           TEXT NOT NULL,            -- waiting|locked|copying|copied|failed|ignored
    message          TEXT,                     -- human readable reason / detail
    attempts         INTEGER NOT NULL DEFAULT 0,
    manual_ignore    INTEGER NOT NULL DEFAULT 0, -- 1 = a user clicked "Ignore" in the web app
    destination_path TEXT,
    sha256           TEXT,
    first_seen       TEXT NOT NULL,
    last_seen        TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    copied_at        TEXT,
    renamed_path     TEXT,                     -- C_... path after the source was renamed
    renamed_at       TEXT,
    acquired_file_id INTEGER,                  -- row id in the sample database
    CHECK (status IN ('waiting','locked','copying','copied','failed','ignored'))
);
CREATE INDEX IF NOT EXISTS ix_copy_file_status ON copy_file(status);
CREATE TABLE IF NOT EXISTS copy_event (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id     INTEGER REFERENCES copy_file(id),
    at          TEXT NOT NULL,
    event       TEXT NOT NULL,
    message     TEXT
);
CREATE INDEX IF NOT EXISTS ix_copy_event_file ON copy_event(file_id);
CREATE TABLE IF NOT EXISTS copy_run (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    host        TEXT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    result      TEXT,
    summary     TEXT                           -- JSON counts
);
"""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class MatchingDB:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # The database may sit on a network share and the web app reads it at
        # the same time. Keep the default rollback journal (WAL does not work
        # over SMB) and wait up to 30 s if the web app holds a read lock.
        self.conn = sqlite3.connect(str(path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(MATCHING_SCHEMA)
        self.conn.commit()

    def get(self, source_path: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM copy_file WHERE source_path = ?", (source_path,)).fetchone()

    def get_by_id(self, file_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM copy_file WHERE id = ?", (file_id,)).fetchone()

    def upsert(self, source_path: str, **fields) -> sqlite3.Row:
        """Create or update the row for source_path. Logs an event when the status changes."""
        ts = now_iso()
        row = self.get(source_path)
        fields["last_seen"] = fields["updated_at"] = ts
        if row is None:
            fields.setdefault("file_name", Path(source_path).name)
            fields.setdefault("status", "waiting")
            fields["first_seen"] = ts
            cols = ["source_path", *fields]
            self.conn.execute(
                f"INSERT INTO copy_file ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                [source_path, *fields.values()],
            )
            new = self.get(source_path)
            self.event(new["id"], new["status"], new["message"])
        else:
            changed = {k: v for k, v in fields.items() if k in ("last_seen", "updated_at") or row[k] != v}
            self.conn.execute(
                f"UPDATE copy_file SET {', '.join(f'{k} = ?' for k in changed)} WHERE id = ?",
                [*changed.values(), row["id"]],
            )
            new = self.get(source_path)
            # Only log real changes, so the history does not fill up with
            # "still waiting" every 15 minutes.
            if new["status"] != row["status"] or new["message"] != row["message"]:
                self.event(new["id"], new["status"], new["message"])
        self.conn.commit()
        return new

    def event(self, file_id: int | None, event: str, message: str | None = None) -> None:
        self.conn.execute("INSERT INTO copy_event (file_id, at, event, message) VALUES (?, ?, ?, ?)",
                          (file_id, now_iso(), event, message))
        self.conn.commit()


# ════════════════════════════════════════════════════════════════════════════
# 4. SAMPLE DATABASE (acquisitions.db, the web app's database)
# ════════════════════════════════════════════════════════════════════════════
class SampleDB:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        if not self.path.is_file():
            # Never let sqlite create an empty database at a wrong path.
            raise FileNotFoundError(f"Sample database not found: {self.path}")
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def experiments(self) -> dict[tuple[str, str], str]:
        """{(project_code, experiment_code): contact initials} for every experiment."""
        with contextlib.closing(self._connect()) as conn:
            rows = conn.execute("SELECT project_code, code, user_initials FROM experiment").fetchall()
        return {(p, e): (u or "") for p, e, u in rows}

    def insert_acquired_file(self, row: sqlite3.Row) -> int:
        """Register a copied file. If it is already registered, return its existing id."""
        with contextlib.closing(self._connect()) as conn:
            existing = conn.execute("SELECT id FROM acquired_file WHERE location = ?",
                                    (row["destination_path"],)).fetchone()
            if existing:
                return existing[0]
            meta = {
                "source": "copy_service",
                "original_path": row["source_path"],
                "sha256": row["sha256"],
                "copied_at": row["copied_at"],
                "host": socket.gethostname(),
            }
            cur = conn.execute(
                "INSERT INTO acquired_file (project_code, experiment_code, location, filename, size_bytes,"
                " instrument_initial, file_date, user_initials, meta) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (row["project_code"], row["experiment_code"], row["destination_path"], row["file_name"],
                 row["size_bytes"], row["instrument"], row["acq_date"], row["user_initials"], json.dumps(meta)),
            )
            conn.commit()
            return cur.lastrowid


# ════════════════════════════════════════════════════════════════════════════
# 5. FILE HANDLES: "is someone still using this file?"
#    On Windows we open the file with share mode FILE_SHARE_READ:
#      * Another program has it open for writing: Windows refuses with
#        ERROR_SHARING_VIOLATION, so the file is "locked" and we try next run.
#      * The open works: while we keep the handle, nobody else can open the
#        file for writing, rename or delete it. Others (rsync, viewers) can
#        still READ it. The file cannot change while we copy and verify it.
#    On Linux/macOS (testing only) a normal open is used and nothing is locked.
# ════════════════════════════════════════════════════════════════════════════
class FileLockedError(Exception):
    """The file is open in another program."""


@contextlib.contextmanager
def guarded_open(path: Path):
    """Open path for reading and block writers until the with-block ends."""
    if os.name != "nt":
        with open(path, "rb") as fh:
            yield fh
        return

    import ctypes
    import msvcrt
    from ctypes import wintypes

    GENERIC_READ = 0x80000000
    FILE_SHARE_READ = 0x00000001
    OPEN_EXISTING = 3
    FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000
    INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
    ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION = 32, 33

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    handle = kernel32.CreateFileW(str(path), GENERIC_READ, FILE_SHARE_READ, None,
                                  OPEN_EXISTING, FILE_FLAG_SEQUENTIAL_SCAN, None)
    if handle == INVALID_HANDLE_VALUE:
        err = ctypes.get_last_error()
        if err in (ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION):
            raise FileLockedError(f"{path.name} is open in another program")
        raise ctypes.WinError(err)
    # Turn the Windows handle into a normal Python file object. Closing it
    # closes the handle and releases the guard.
    fd = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    with os.fdopen(fd, "rb") as fh:
        yield fh


def sha256_of(fh, size: int, progress=None) -> str:
    """SHA-256 of an open binary file object, from the beginning."""
    fh.seek(0)
    digest, done = hashlib.sha256(), 0
    while chunk := fh.read(HASH_CHUNK_BYTES):
        digest.update(chunk)
        done += len(chunk)
        if progress:
            progress(done, size)
    return digest.hexdigest()


# ════════════════════════════════════════════════════════════════════════════
# 6. RSYNC
# ════════════════════════════════════════════════════════════════════════════
def to_rsync_path(path: Path) -> str:
    r"""Convert a Windows path into the form a Cygwin rsync understands.
    C:\Data\x.raw -> /cygdrive/c/Data/x.raw    \\server\share\x -> //server/share/x"""
    text = str(path)
    if RSYNC_PATH_STYLE != "cygwin":
        return text
    text = text.replace("\\", "/")
    if re.match(r"^[A-Za-z]:", text):
        text = f"/cygdrive/{text[0].lower()}{text[2:]}"
    return text


_PROGRESS_RE = re.compile(rb"([\d,.]+)\s+(\d{1,3})%\s+(\S+/s)")


def rsync_copy(source: Path, target: Path, progress=None) -> None:
    """Copy one file with rsync. Raises RuntimeError with rsync's output on failure.

    Options:
      --times                keep the original modification time
      --no-perms/owner/group do not copy Unix permissions (they make no sense on
      --chmod=ugo=rwX        Windows shares). The server's defaults apply instead.
      --partial-dir          if the network drops, keep what arrived in a hidden
                             folder so the next attempt resumes and does not start over
      --timeout              give up if no data moves for N seconds
      --info=progress2       print progress, shown live in the web app
    """
    cmd = [
        RSYNC_EXE, "--times", "--no-perms", "--no-owner", "--no-group", "--chmod=ugo=rwX",
        "--partial-dir=.rsync-partial", f"--timeout={RSYNC_IO_TIMEOUT_SECONDS}",
        "--info=progress2", to_rsync_path(source), to_rsync_path(target),
    ]
    logging.debug("rsync command: %s", cmd)
    tail = b""
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as proc:
        # rsync redraws its progress line with \r. Read whatever is available
        # and pick the latest "  123,456,789  42%  110.5MB/s" from it.
        while chunk := proc.stdout.read1(65536):
            tail = (tail + chunk)[-4000:]
            found = _PROGRESS_RE.findall(chunk)
            if found and progress:
                _done, percent, speed = found[-1]
                progress(int(percent), speed.decode(errors="replace"))
        code = proc.wait()
    if code != 0:
        text = tail.decode(errors="replace").replace("\r", "\n").strip().splitlines()
        raise RuntimeError(f"rsync exit code {code}: " + " | ".join(text[-3:]))


# ════════════════════════════════════════════════════════════════════════════
# 7. STATUS FILE (CACHE_DIR\status.json) and RUN LOCK
# ════════════════════════════════════════════════════════════════════════════
def write_json_atomic(path: Path, data: dict) -> None:
    """Write to a temp file, then swap it in. Readers never see half a file.
    On Windows the swap fails while the web app has the file open for a few
    milliseconds, so retry a little. Status is best-effort; never crash on it."""
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        for _ in range(10):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                time.sleep(0.1)
    except OSError as exc:
        logging.warning("Could not write %s: %s", path, exc)
    with contextlib.suppress(OSError):
        tmp.unlink()


class StatusFile:
    """Heartbeat + live progress for the web app's Copy status page."""

    def __init__(self, path: Path, dry_run: bool) -> None:
        self.path = path
        self.dry_run = dry_run
        self.data = {
            "host": socket.gethostname(), "pid": os.getpid(), "state": "running",
            "run_started": now_iso(), "run_finished": None, "next_run_expected": None,
            "run_interval_minutes": RUN_INTERVAL_MINUTES, "message": "", "current": None, "summary": {},
        }
        self._last_write = 0.0

    def save(self, force: bool = True) -> None:
        # Progress callbacks fire very often. Write at most every 2 s unless forced.
        if self.dry_run or (not force and time.monotonic() - self._last_write < 2):
            return
        self._last_write = time.monotonic()
        write_json_atomic(self.path, self.data)

    def current(self, **info) -> None:
        self.data["current"] = info or None
        self.save()

    def progress(self, phase: str, percent: int, speed: str = "") -> None:
        if self.data["current"] is not None:
            self.data["current"].update(phase=phase, percent=percent, speed=speed)
            self.save(force=False)


@contextlib.contextmanager
def run_lock(path: Path):
    """Make sure only one copy run is active. The OS releases the lock if the
    process dies, so a crash or power cut can never leave a stale lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+")
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        raise RuntimeError("Another copy run is still active; this run exits.") from None
    try:
        yield
    finally:
        fh.close()


# ════════════════════════════════════════════════════════════════════════════
# 8. REQUESTS FROM THE WEB APP (CACHE_DIR\requests\*.json)
#    {"action": "retry" | "ignore" | "unignore", "file_id": 12, "by": "...", ...}
#    retry     reset the attempt counter so a failed file is tried again
#    ignore    never copy this file (it stays "ignored" until "unignore")
#    unignore  remove the manual ignore. The file is evaluated normally next run.
# ════════════════════════════════════════════════════════════════════════════
def process_requests(mdb: MatchingDB, dry_run: bool) -> None:
    folder = CACHE_DIR / "requests"
    if not folder.is_dir():
        return
    for req_path in sorted(folder.glob("*.json")):
        try:
            req = json.loads(req_path.read_text(encoding="utf-8"))
            row = mdb.get_by_id(int(req["file_id"]))
            action = req["action"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logging.warning("Bad request file %s: %s", req_path.name, exc)
            row = action = None
        if row is not None and not dry_run:
            who = req.get("by") or "web app"
            if action == "retry" and row["status"] in ("failed", "locked", "waiting", "ignored"):
                mdb.upsert(row["source_path"], attempts=0, manual_ignore=0, status="waiting",
                           message=f"Retry requested by {who}")
            elif action == "ignore" and row["status"] != "copied":
                mdb.upsert(row["source_path"], manual_ignore=1, status="ignored",
                           message=f"Ignored by {who}")
            elif action == "unignore" and row["manual_ignore"]:
                mdb.upsert(row["source_path"], manual_ignore=0, status="waiting",
                           message=f"Ignore removed by {who}")
            logging.info("Request %s for file %s applied", action, row["file_name"])
        if not dry_run:
            with contextlib.suppress(OSError):
                req_path.unlink()


# ════════════════════════════════════════════════════════════════════════════
# 9. ONE RUN
# ════════════════════════════════════════════════════════════════════════════
class CopyRun:
    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self.mdb = MatchingDB(MATCHING_DB_PATH)
        self.sample = SampleDB(ACQUISITIONS_DB_PATH)
        self.status = StatusFile(CACHE_DIR / "status.json", dry_run)
        self.summary = {"seen": 0, "copied": 0, "failed": 0, "locked": 0, "waiting": 0, "ignored": 0}
        self.experiments: dict[tuple[str, str], str] = {}

    # ── entry point ────────────────────────────────────────────────────────
    def run(self) -> int:
        cur = self.mdb.conn.execute("INSERT INTO copy_run (host, started_at) VALUES (?, ?)",
                                    (socket.gethostname(), now_iso()))
        self.mdb.conn.commit()
        run_id, result = cur.lastrowid, "ok"
        self.status.save()
        try:
            process_requests(self.mdb, self.dry_run)
            # If the sample database cannot be read we STOP. We do not mark
            # files "ignored" just because the server was briefly unreachable.
            self.experiments = self.sample.experiments()
            self.finish_incomplete()
            for path in self.scan():
                self.handle(path)
        except Exception as exc:  # noqa: BLE001  (keep a record of ANY failure)
            logging.exception("Run failed")
            result = f"error: {exc}"
            self.status.data["message"] = str(exc)
        finished = datetime.now().astimezone()
        self.status.data.update(
            state="idle" if result == "ok" else "error", current=None, summary=self.summary,
            run_finished=finished.isoformat(timespec="seconds"),
            next_run_expected=(finished + timedelta(minutes=RUN_INTERVAL_MINUTES)).isoformat(timespec="seconds"),
        )
        self.status.save()
        self.mdb.conn.execute("UPDATE copy_run SET finished_at = ?, result = ?, summary = ? WHERE id = ?",
                              (now_iso(), result, json.dumps(self.summary), run_id))
        self.mdb.conn.commit()
        logging.info("Run finished: %s %s", result, self.summary)
        return 0 if result == "ok" else 1

    # ── scanning ───────────────────────────────────────────────────────────
    def scan(self):
        """Yield every *.raw file in SOURCE_DIRS that does not start with C_."""
        for root in SOURCE_DIRS:
            if not root.is_dir():
                logging.error("Source folder not found: %s", root)
                continue
            for dirpath, _dirs, files in os.walk(root):
                for name in files:
                    if name.lower().endswith(COPY_EXTENSION) and not name.startswith(COPIED_PREFIX):
                        yield Path(dirpath) / name

    # ── decide what to do with one file ────────────────────────────────────
    def handle(self, path: Path) -> None:
        self.summary["seen"] += 1
        key = str(path)
        row = self.mdb.get(key)
        if row is not None and row["status"] == "copied":
            return  # already copied; finish_incomplete() handles a pending rename
        if row is not None and row["manual_ignore"]:
            self.mdb.upsert(key)  # just refresh last_seen
            self.summary["ignored"] += 1
            return

        parsed = parse_file_name(path.name)
        try:
            st = path.stat()
        except OSError as exc:  # vanished between scan and stat
            logging.warning("Cannot stat %s: %s", path, exc)
            return
        info = dict(file_name=path.name, size_bytes=st.st_size, mtime=st.st_mtime)
        if parsed:
            info.update(instrument=parsed.instrument, project_code=parsed.project,
                        experiment_code=parsed.experiment, user_initials=parsed.user,
                        acq_date=parsed.acq_date.isoformat(),
                        instrument_folder=INSTRUMENT_FOLDERS.get(parsed.instrument))

        # Rule 1: name must follow the pattern
        if parsed is None:
            return self.set(key, info, "ignored",
                            "Name does not follow INSTRUMENT_YYYYMMDD-NNN_PROJECT_USER_EXPERIMENT[_...].raw")
        # Rule 2: known instrument letter
        if parsed.instrument not in INSTRUMENT_FOLDERS:
            return self.set(key, info, "ignored",
                            f"Unknown instrument '{parsed.instrument}' (known: {', '.join(INSTRUMENT_FOLDERS)})")
        # Rule 3: project + experiment must exist in the sample database
        if parsed.key not in self.experiments:
            known_project = any(p == parsed.project for p, _ in self.experiments)
            what = (f"Experiment {parsed.experiment} not found in project {parsed.project}" if known_project
                    else f"Project {parsed.project} not found in the sample database")
            return self.set(key, info, "ignored", what + " (checked again every run)")
        info["contact"] = self.experiments[parsed.key]
        # Rule 4: untouched for MIN_UNCHANGED_MINUTES
        age_min = (time.time() - st.st_mtime) / 60
        if age_min < MIN_UNCHANGED_MINUTES:
            ready = datetime.fromtimestamp(st.st_mtime) + timedelta(minutes=MIN_UNCHANGED_MINUTES)
            return self.set(key, info, "waiting",
                            f"Changed {age_min:.0f} min ago; can be copied from {ready:%H:%M}")
        # Rule 5: minimum size (checked after rule 4, so a file that is still
        # growing shows as "waiting" and not as "too small")
        if st.st_size < MIN_SIZE_BYTES:
            return self.set(key, info, "ignored",
                            f"Too small: {st.st_size / 1024**2:.0f} MB < {MIN_SIZE_BYTES / 1024**2:.0f} MB")
        # Rule 6: do not hammer a file that keeps failing
        if row is not None and row["status"] == "failed" and row["attempts"] >= MAX_ATTEMPTS:
            self.summary["failed"] += 1
            return  # stays failed until a user clicks "Retry"

        self.copy_one_file(path, parsed, info)

    def set(self, key: str, info: dict, status: str, message: str) -> None:
        self.summary[status] = self.summary.get(status, 0) + 1
        if self.dry_run:
            logging.info("[dry-run] %-8s %s (%s)", status, Path(key).name, message)
            return
        self.mdb.upsert(key, status=status, message=message, **info)

    # ── the copy itself ────────────────────────────────────────────────────
    def copy_one_file(self, path: Path, parsed: ParsedName, info: dict) -> None:
        key = str(path)
        final = destination_for(parsed, path.name)
        partial = final.with_name(final.name + PARTIAL_SUFFIX)
        if self.dry_run:
            logging.info("[dry-run] would copy %s -> %s", path, final)
            self.summary["copied"] += 1
            return

        row = self.mdb.upsert(key, status="copying", message=f"Copying to {final}",
                              destination_path=str(final), **info)
        attempt = row["attempts"] + 1
        self.mdb.upsert(key, attempts=attempt)
        self.status.current(file=path.name, file_id=row["id"], destination=str(final),
                            size_bytes=info["size_bytes"], phase="checking", percent=0,
                            started=now_iso(), attempt=attempt)
        logging.info("Copying %s -> %s (attempt %d)", path, final, attempt)
        try:
            # (a) Guard the source: fails at once if someone is writing it.
            with guarded_open(path) as src:
                # Was the file changed between the scan and now?
                st = os.fstat(src.fileno())
                if st.st_size != info["size_bytes"] or st.st_mtime != info["mtime"]:
                    self.mdb.upsert(key, attempts=attempt - 1)
                    return self.set(key, {**info, "size_bytes": st.st_size, "mtime": st.st_mtime},
                                    "waiting", "File changed just before copying; waiting again")
                # (b) Hash the source.
                src_hash = sha256_of(src, st.st_size,
                                     lambda d, t: self.status.progress("hashing source", int(100 * d / max(t, 1))))
                final.parent.mkdir(parents=True, exist_ok=True)

                if final.exists():
                    # Already on the server (for example the previous run copied it
                    # and crashed before updating the database). Accept it only if
                    # it is identical. NEVER overwrite a different file.
                    with open(final, "rb") as dst:
                        dst_hash = sha256_of(dst, st.st_size, lambda d, t: self.status.progress(
                            "verifying existing copy", int(100 * d / max(t, 1))))
                    if dst_hash != src_hash:
                        raise RuntimeError(f"A DIFFERENT file with this name already exists at {final}; "
                                           "it was not overwritten. Please check manually.")
                    logging.info("Identical copy already on server: %s", final)
                else:
                    # (c) rsync into NAME.raw.partial
                    self.status.progress("copying", 0)
                    rsync_copy(path, partial, lambda pct, speed: self.status.progress("copying", pct, speed))
                    # (d) Read the copy back from the server and hash it.
                    with open(partial, "rb") as dst:
                        dst_hash = sha256_of(dst, st.st_size, lambda d, t: self.status.progress(
                            "verifying copy", int(100 * d / max(t, 1))))
                    # (e) Compare.
                    if dst_hash != src_hash:
                        with contextlib.suppress(OSError):
                            partial.unlink()
                        raise RuntimeError("Checksum mismatch after copy: the copy is corrupt and was deleted")
                    os.rename(partial, final)  # verified: give it its real name
        except FileLockedError:
            self.mdb.upsert(key, attempts=attempt - 1)  # a locked file is not a failed attempt
            self.status.current()
            return self.set(key, info, "locked", "File is open in another program; will retry next run")
        except Exception as exc:  # noqa: BLE001
            logging.error("Copy failed for %s: %s", path, exc)
            self.status.current()
            give_up = " (giving up; use Retry in the sample database)" if attempt >= MAX_ATTEMPTS else ""
            return self.set(key, info, "failed", f"Attempt {attempt}/{MAX_ATTEMPTS}: {exc}{give_up}")

        # Handle is closed here, so the source can now be renamed.
        self.mdb.upsert(key, status="copied", message="Copied and verified (SHA-256)",
                        sha256=src_hash, copied_at=now_iso(), destination_path=str(final))
        self.summary["copied"] += 1
        self.status.current()
        self.finish(self.mdb.get(key))

    # ── after the copy: rename source to C_..., register in sample database ─
    def finish_incomplete(self) -> None:
        """Retry the follow-up steps that failed in an earlier run."""
        rows = self.mdb.conn.execute(
            "SELECT * FROM copy_file WHERE status = 'copied' AND (renamed_at IS NULL OR acquired_file_id IS NULL)"
        ).fetchall()
        for row in rows:
            if not self.dry_run:
                self.finish(row)

    def finish(self, row: sqlite3.Row) -> None:
        key, notes = row["source_path"], []
        src = Path(key)
        renamed = src.with_name(COPIED_PREFIX + src.name)

        # (f) Rename the source to C_<name>.
        if row["renamed_at"] is None:
            try:
                if src.exists():
                    st = src.stat()
                    if st.st_size != row["size_bytes"] or st.st_mtime != row["mtime"]:
                        # Someone changed the file after we copied it. Do not hide
                        # the new version behind C_. A person must decide.
                        self.mdb.upsert(key, status="failed", attempts=MAX_ATTEMPTS,
                                        message="Source changed after it was copied; the server copy is the OLD "
                                                "version. Check manually, then use Retry.")
                        return
                    os.rename(src, renamed)
                    self.mdb.upsert(key, renamed_path=str(renamed), renamed_at=now_iso())
                elif renamed.exists():
                    self.mdb.upsert(key, renamed_path=str(renamed), renamed_at=now_iso())
                else:
                    self.mdb.upsert(key, renamed_at=now_iso())
                    notes.append("source file no longer exists")
            except OSError as exc:  # usually: file open in a viewer
                notes.append(f"rename to {COPIED_PREFIX}… pending ({exc.strerror or exc}); retried next run")

        # (g) Register the copy in the sample database.
        if row["acquired_file_id"] is None:
            try:
                file_id = self.sample.insert_acquired_file(self.mdb.get(key))
                self.mdb.upsert(key, acquired_file_id=file_id)
            except (sqlite3.Error, OSError) as exc:
                notes.append(f"not yet in sample database ({exc}); retried next run")

        msg = "Copied and verified (SHA-256)" + (": " + "; ".join(notes) if notes else "")
        self.mdb.upsert(key, message=msg)


# ════════════════════════════════════════════════════════════════════════════
# 10. MAIN
# ════════════════════════════════════════════════════════════════════════════
def setup_logging(verbose: bool) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        log_dir = CACHE_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_dir / f"copy_{datetime.now():%Y-%m}.log", encoding="utf-8"))
    except OSError as exc:
        print(f"Cannot write log file in {CACHE_DIR}: {exc}", file=sys.stderr)
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)-7s %(message)s")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Copy finished .raw acquisitions to the server (one run).")
    parser.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
    parser.add_argument("--verbose", action="store_true", help="more detailed log output")
    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    try:
        with run_lock(CACHE_DIR / "copy_service.lock"):
            return CopyRun(dry_run=args.dry_run).run()
    except RuntimeError as exc:  # another run is active
        logging.info("%s", exc)
        return 0


if __name__ == "__main__":
    sys.exit(main())
