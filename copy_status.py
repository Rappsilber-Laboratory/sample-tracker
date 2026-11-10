"""Copy status: shows what db_match/copy_raw_files.py is doing.

The copy script owns the matching DB and writes it. This blueprint only READS it
(COPY_MATCHING_DB_PATHS). To change anything (retry, ignore), the web app drops a
small JSON request into the selected cache folder under requests/. The copy
script applies it at the start of its next run (at most ~15 minutes later).
Live progress and the heartbeat come from that cache folder's status.json.
"""
import contextlib
import json
import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for

from models import User

copy_status = Blueprint("copy_status", __name__, url_prefix="/copy-status")

PAGE_SIZE = 100
# Tabs on the page -> statuses they include.
VIEWS = {
    "active": ("copying", "waiting", "locked", "failed"),
    "copied": ("copied",),
    "ignored": ("ignored",),
    "all": ("copying", "waiting", "locked", "failed", "copied", "ignored"),
}
# If the copy script has not finished a run for this long, show a warning.
STALE_AFTER_MINUTES = 35


INSTRUMENTS = {"astral": "Astral", "zoom": "Zoom", "raptor": "Raptor"}


def _connect(machine=None):
    """Open an existing service DB read-only; a missing/offline PC is allowed."""
    machine = machine or g.copy_machine
    path = Path(current_app.config["COPY_MATCHING_DB_PATHS"][machine])
    conn = None
    try:
        # Encode the native path, including UNC backslashes on Windows, as a
        # URI path rather than treating the server name as a URI authority.
        uri = "file:" + quote(str(path.absolute()), safe="") + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        conn.execute("SELECT status FROM copy_file LIMIT 0")
        return conn
    except (OSError, sqlite3.Error):
        if conn is not None:
            conn.close()
        return None


@copy_status.before_request
def select_machine():
    g.copy_machines = []
    for key, label in INSTRUMENTS.items():
        conn = _connect(key)
        available = conn is not None
        if conn is not None:
            conn.close()
        g.copy_machines.append({"key": key, "label": label, "available": available,
                                "path": current_app.config["COPY_MATCHING_DB_PATHS"][key]})
    default = next((m["key"] for m in g.copy_machines if m["available"]), "astral")
    g.copy_machine = request.args.get("machine", default)
    if g.copy_machine not in INSTRUMENTS:
        abort(400)


@copy_status.url_defaults
def machine_urls(endpoint, values):
    if hasattr(g, "copy_machine"):
        values.setdefault("machine", g.copy_machine)


@copy_status.context_processor
def machine_context():
    return {"copy_machines": g.copy_machines, "copy_machine": g.copy_machine,
            "copy_machine_label": INSTRUMENTS[g.copy_machine]}


def _cache_dir():
    return Path(current_app.config["COPY_CACHE_DIRS"][g.copy_machine])


def _service_status():
    """Contents of status.json plus derived fields for the heartbeat panel."""
    path = _cache_dir() / "status.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "unknown", "health": "unknown"}
    now = datetime.now().astimezone()
    try:
        last = datetime.fromisoformat(data.get("run_finished") or data.get("run_started"))
        data["minutes_ago"] = max(0, int((now - last).total_seconds() // 60))
    except (TypeError, ValueError):
        data["minutes_ago"] = None
    try:
        nxt = datetime.fromisoformat(data["next_run_expected"])
        data["next_in_minutes"] = max(0, int((nxt - now).total_seconds() // 60))
    except (KeyError, TypeError, ValueError):
        data["next_in_minutes"] = None
    if data.get("state") == "running":
        data["health"] = "running"
    elif data.get("state") == "error":
        data["health"] = "error"
    elif data["minutes_ago"] is not None and data["minutes_ago"] > STALE_AFTER_MINUTES:
        data["health"] = "stale"
    else:
        data["health"] = "ok"
    return data


def _pending_requests():
    """{file_id: action} for requests the copy script has not picked up yet."""
    folder = _cache_dir() / "requests"
    pending = {}
    with contextlib.suppress(OSError):
        for item in folder.glob("*.json"):
            with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
                req = json.loads(item.read_text(encoding="utf-8"))
                pending[int(req["file_id"])] = req["action"]
    return pending


def _counts(conn):
    counts = dict(conn.execute("SELECT status, COUNT(*) FROM copy_file GROUP BY status").fetchall())
    counts["active"] = sum(counts.get(s, 0) for s in VIEWS["active"])
    counts["all"] = sum(counts.get(s, 0) for s in VIEWS["all"])
    return counts


@copy_status.route("/")
def overview():
    view = request.args.get("view", "active")
    if view not in VIEWS:
        abort(400)
    status_filter = request.args.get("status", "")
    q = request.args.get("q", "").strip()
    service = _service_status()
    conn = _connect()
    if conn is None:
        return render_template("copy_status/list.html", missing=True, service=service, view=view,
                               db_path=current_app.config["COPY_MATCHING_DB_PATHS"][g.copy_machine])
    with contextlib.closing(conn):
        statuses = (status_filter,) if status_filter in VIEWS[view] else VIEWS[view]
        where = [f"status IN ({', '.join('?' * len(statuses))})"]
        params = list(statuses)
        if q:
            where.append("(file_name LIKE ? OR project_code LIKE ? OR experiment_code LIKE ? OR source_path LIKE ?)")
            params += [f"%{q}%"] * 4
        sql_where = " AND ".join(where)
        matched = conn.execute(f"SELECT COUNT(*) FROM copy_file WHERE {sql_where}", params).fetchone()[0]
        pages = max(1, (matched + PAGE_SIZE - 1) // PAGE_SIZE)
        try:
            page = min(max(1, int(request.args.get("page", "1"))), pages)
        except ValueError:
            abort(400)
        # Most urgent first: copying, then problems, then the rest by acquisition date.
        rows = conn.execute(
            f"SELECT * FROM copy_file WHERE {sql_where} ORDER BY "
            "CASE status WHEN 'copying' THEN 0 WHEN 'failed' THEN 1 WHEN 'locked' THEN 2 "
            "WHEN 'waiting' THEN 3 ELSE 4 END, acq_date DESC, file_name DESC LIMIT ? OFFSET ?",
            [*params, PAGE_SIZE, (page - 1) * PAGE_SIZE],
        ).fetchall()
        counts = _counts(conn)
    users = {u.initials: u.name for u in User.query.all()}

    def page_url(number):
        return url_for("copy_status.overview", view=view, status=status_filter, q=q, page=number)

    return render_template(
        "copy_status/list.html", missing=False, service=service, rows=rows, counts=counts, view=view,
        status_filter=status_filter, q=q, page=page, pages=pages, matched=matched, page_url=page_url,
        users=users, pending=_pending_requests(),
    )


@copy_status.route("/<int:file_id>")
def detail(file_id):
    conn = _connect()
    if conn is None:
        abort(404)
    with contextlib.closing(conn):
        row = conn.execute("SELECT * FROM copy_file WHERE id = ?", (file_id,)).fetchone()
        if row is None:
            abort(404)
        events = conn.execute("SELECT * FROM copy_event WHERE file_id = ? ORDER BY id DESC LIMIT 200",
                              (file_id,)).fetchall()
    users = {u.initials: u.name for u in User.query.all()}
    return render_template("copy_status/detail.html", row=row, events=events, users=users,
                           service=_service_status(), pending=_pending_requests())


@copy_status.route("/live")
def live():
    """Polled by the page every few seconds: heartbeat, current copy, counts."""
    conn = _connect()
    counts = {}
    if conn is not None:
        with contextlib.closing(conn):
            counts = _counts(conn)
    return jsonify(service=_service_status(), counts=counts,
                   availability={m["key"]: m["available"] for m in g.copy_machines})


@copy_status.route("/<int:file_id>/request", methods=["POST"])
def make_request(file_id):
    action = request.form.get("action")
    if action not in ("retry", "ignore", "unignore"):
        abort(400)
    conn = _connect()
    if conn is None:
        abort(404)
    with contextlib.closing(conn):
        row = conn.execute("SELECT file_name FROM copy_file WHERE id = ?", (file_id,)).fetchone()
    if row is None:
        abort(404)
    folder = _cache_dir() / "requests"
    payload = {"action": action, "file_id": file_id, "file_name": row["file_name"],
               "by": (request.form.get("by") or "").strip()[:60] or "web app",
               "requested_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    try:
        folder.mkdir(parents=True, exist_ok=True)
        # Write under a temp name and then rename, so the copy script never reads half a file.
        name = f"{datetime.now():%Y%m%d%H%M%S}_{uuid.uuid4().hex[:8]}"
        tmp = folder / f"{name}.tmp"
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, folder / f"{name}.json")
    except OSError as exc:
        current_app.logger.warning("Cannot write copy request: %s", exc)
        flash("Could not reach the copy service's cache folder. The request was not saved.", "error")
    else:
        label = {"retry": "Retry", "ignore": "Ignore", "unignore": "Stop ignoring"}[action]
        flash(f"{label} requested for {row['file_name']}. The copy service applies it on its next run "
              "(within ~15 minutes).", "success")
    target = request.form.get("next") or ""
    if not target.startswith("/") or target.startswith("//"):  # only local pages, no open redirect
        target = url_for("copy_status.overview")
    return redirect(target)
