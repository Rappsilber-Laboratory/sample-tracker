import importlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
_database = tempfile.TemporaryDirectory()
os.environ.setdefault("DATABASE_URL", "sqlite:///" + _database.name + "/copy-tests.db")
os.environ.setdefault("SECRET_KEY", "copy-tests")
sys.path.insert(0, str(ROOT / "db_match"))

NAME = "A_20260721-001_GMB_HKI_NatGel_Pool.raw"


def make_file(path, size=2_000_000, age_minutes=30):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(os.urandom(size))
    stamp = time.time() - age_minutes * 60
    os.utime(path, (stamp, stamp))


class CopyScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        acq = self.tmp / "acq.db"
        with sqlite3.connect(acq) as conn:
            conn.executescript((ROOT / "schema.sql").read_text())
            conn.execute("INSERT INTO project VALUES ('GMB', 'g', 'd', 'HKI', 1)")
            conn.execute("INSERT INTO experiment (project_code, code, name, description, user_initials,"
                         " crosslinking_experiment) VALUES ('GMB', 'NatGel', 'n', 'd', 'HKI', 0)")
        env = {"DESTINATION_ROOT": str(self.tmp / "dest"), "SOURCE_DIRS": str(self.tmp / "src"),
               "ACQUISITIONS_DB_PATH": str(acq), "MATCHING_DB_PATH": str(self.tmp / "match.db"),
               "CACHE_DIR": str(self.tmp / "cache"), "MIN_SIZE_MB": "1", "RSYNC_PATH_STYLE": "native",
               "RSYNC_EXE": shutil.which("rsync") or "rsync"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        import copy_raw_files
        self.mod = importlib.reload(copy_raw_files)
        self.source = self.tmp / "src" / NAME
        self.final = self.tmp / "dest" / "Astral1" / "2026" / "2607" / "260721" / NAME

    def run_once(self):
        return self.mod.CopyRun().run()

    def row(self, name=NAME):
        conn = sqlite3.connect(self.tmp / "match.db")
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM copy_file WHERE file_name = ?", (name,)).fetchone()

    def acquired(self):
        with sqlite3.connect(self.tmp / "acq.db") as conn:
            return conn.execute("SELECT project_code, experiment_code, location, filename, instrument_initial,"
                                " file_date, user_initials FROM acquired_file").fetchall()

    def test_parse_and_destination(self):
        parsed = self.mod.parse_file_name(NAME)
        self.assertEqual((parsed.instrument, parsed.project, parsed.user, parsed.experiment),
                         ("A", "GMB", "HKI", "NatGel"))
        self.assertEqual(self.mod.destination_for(parsed, NAME), self.final)
        self.assertIsNone(self.mod.parse_file_name("A_20261399-001_GMB_HKI_NatGel.raw"))
        self.assertIsNone(self.mod.parse_file_name("notes.raw"))

    def test_instrument_database_and_cache_defaults(self):
        for instrument in ("astral", "zoom", "raptor"):
            env = dict(os.environ, COPY_INSTRUMENT=instrument)
            env.pop("MATCHING_DB_PATH", None)
            env.pop("CACHE_DIR", None)
            with mock.patch.dict(os.environ, env, clear=True):
                mod = importlib.reload(self.mod)
                self.assertEqual(str(mod.MATCHING_DB_PATH),
                                 mod.MATCHING_DB_PATHS[instrument])
                self.assertTrue(str(mod.MATCHING_DB_PATH).endswith("matching_db_" + instrument + ".sqlite"))
                self.assertTrue(str(mod.CACHE_DIR).endswith("cache_" + instrument))
        importlib.reload(self.mod)

    def test_cygwin_paths(self):
        with mock.patch.object(self.mod, "RSYNC_PATH_STYLE", "cygwin"):
            self.assertEqual(self.mod.to_rsync_path(Path("C:\\Data\\x.raw")), "/cygdrive/c/Data/x.raw")
            self.assertEqual(self.mod.to_rsync_path(Path("\\\\srv\\share\\x.raw")), "//srv/share/x.raw")

    @unittest.skipUnless(shutil.which("rsync"), "rsync not installed")
    def test_copy_rename_and_register(self):
        make_file(self.source)
        data = self.source.read_bytes()
        self.assertEqual(self.run_once(), 0)
        self.assertEqual(self.final.read_bytes(), data)
        self.assertFalse(self.source.exists())
        self.assertTrue((self.tmp / "src" / ("C_" + NAME)).exists())
        self.assertFalse(self.final.with_name(NAME + ".partial").exists())
        row = self.row()
        self.assertEqual(row["status"], "copied")
        self.assertIsNotNone(row["acquired_file_id"])
        self.assertEqual(self.acquired(), [("GMB", "NatGel", str(self.final), NAME, "A", "2026-07-21", "HKI")])
        # Second run: nothing new, nothing duplicated.
        self.assertEqual(self.run_once(), 0)
        self.assertEqual(len(self.acquired()), 1)

    def test_fresh_small_and_unknown_files_are_not_copied(self):
        make_file(self.source, age_minutes=2)
        make_file(self.tmp / "src" / "A_20260721-002_GMB_HKI_NatGel.raw", size=1000)
        make_file(self.tmp / "src" / "A_20260721-003_NOPE_HKI_NatGel.raw")
        self.run_once()
        self.assertEqual(self.row()["status"], "waiting")
        self.assertIn("Too small", self.row("A_20260721-002_GMB_HKI_NatGel.raw")["message"])
        self.assertIn("Project NOPE", self.row("A_20260721-003_NOPE_HKI_NatGel.raw")["message"])
        self.assertFalse((self.tmp / "dest").exists())

    def test_locked_file_is_retried_without_counting_an_attempt(self):
        make_file(self.source)

        def locked(_path):
            raise self.mod.FileLockedError("open")
        with mock.patch.object(self.mod, "guarded_open", locked):
            self.run_once()
        self.assertEqual((self.row()["status"], self.row()["attempts"]), ("locked", 0))
        self.assertTrue(self.source.exists())

    def test_corrupt_copy_is_deleted_and_marked_failed(self):
        make_file(self.source)

        def bad_rsync(src, target, progress=None):
            target.write_bytes(b"corrupt")
        with mock.patch.object(self.mod, "rsync_copy", bad_rsync):
            self.run_once()
        row = self.row()
        self.assertEqual((row["status"], row["attempts"]), ("failed", 1))
        self.assertIn("Checksum mismatch", row["message"])
        self.assertFalse(self.final.exists())
        self.assertFalse(self.final.with_name(NAME + ".partial").exists())
        self.assertTrue(self.source.exists())  # NOT renamed to C_
        self.assertEqual(self.acquired(), [])

    def test_existing_different_destination_is_never_overwritten(self):
        make_file(self.source)
        self.final.parent.mkdir(parents=True)
        self.final.write_bytes(b"someone else's file")
        self.run_once()
        self.assertEqual(self.row()["status"], "failed")
        self.assertEqual(self.final.read_bytes(), b"someone else's file")

    def test_existing_identical_destination_is_accepted(self):
        make_file(self.source)
        self.final.parent.mkdir(parents=True)
        shutil.copyfile(self.source, self.final)
        with mock.patch.object(self.mod, "rsync_copy", side_effect=AssertionError("must not copy")):
            self.run_once()
        self.assertEqual(self.row()["status"], "copied")

    def test_rename_pending_is_finished_next_run(self):
        make_file(self.source)
        real_rename = os.rename

        def fail_source_rename(a, b):
            if Path(b).name.startswith("C_"):
                raise PermissionError(13, "in use")
            return real_rename(a, b)
        with mock.patch.object(self.mod, "rsync_copy", lambda s, t, progress=None: shutil.copyfile(s, t)), \
                mock.patch.object(self.mod.os, "rename", fail_source_rename):
            self.run_once()
        self.assertIn("rename to C_", self.row()["message"])
        self.assertTrue(self.source.exists())
        self.run_once()
        self.assertIsNotNone(self.row()["renamed_at"])
        self.assertFalse(self.source.exists())

    def test_sample_db_unreachable_marks_nothing(self):
        make_file(self.source)
        with mock.patch.object(self.mod, "ACQUISITIONS_DB_PATH", self.tmp / "missing.db"):
            run = self.mod.CopyRun()
            run.sample = self.mod.SampleDB(self.tmp / "missing.db")
            self.assertEqual(run.run(), 1)
        self.assertIsNone(self.row())
        status = json.loads((self.tmp / "cache" / "status.json").read_text())
        self.assertEqual(status["state"], "error")

    def test_requests_from_web_app(self):
        make_file(self.source, age_minutes=2)
        self.run_once()
        file_id = self.row()["id"]
        requests = self.tmp / "cache" / "requests"
        requests.mkdir(parents=True)
        (requests / "1.json").write_text(json.dumps({"action": "ignore", "file_id": file_id, "by": "HKI"}))
        self.run_once()
        self.assertEqual((self.row()["status"], self.row()["manual_ignore"]), ("ignored", 1))
        self.assertEqual(list(requests.iterdir()), [])
        os.utime(self.source, (time.time() - 3600,) * 2)
        self.run_once()
        self.assertEqual(self.row()["status"], "ignored")  # manual ignore is sticky
        (requests / "2.json").write_text(json.dumps({"action": "unignore", "file_id": file_id}))
        with mock.patch.object(self.mod, "rsync_copy", lambda s, t, progress=None: shutil.copyfile(s, t)):
            self.run_once()
        self.assertEqual(self.row()["status"], "copied")


class CopyStatusPageTests(unittest.TestCase):
    def setUp(self):
        from app import app
        from models import db
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app = app
        self.original = {k: app.config[k] for k in ("COPY_MATCHING_DB_PATHS", "COPY_CACHE_DIRS")}
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False,
                          COPY_MATCHING_DB_PATHS={key: str(self.tmp / ("match.db" if key == "astral" else key + ".db"))
                                                  for key in ("astral", "zoom", "raptor")},
                          COPY_CACHE_DIRS={key: str(self.tmp / ("cache" if key == "astral" else "cache_" + key))
                                          for key in ("astral", "zoom", "raptor")})
        self.addCleanup(app.config.update, self.original)
        with app.app_context():
            db.create_all()
        self.client = app.test_client()

    def test_missing_database_is_explained(self):
        response = self.client.get("/copy-status/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"was not found", response.data)

    def test_services_are_isolated_and_missing_services_stay_visible(self):
        import copy_raw_files
        for machine, filename in (("astral", "astral.raw"), ("zoom", "zoom.raw")):
            path = self.app.config["COPY_MATCHING_DB_PATHS"][machine]
            mdb = copy_raw_files.MatchingDB(Path(path))
            mdb.upsert(filename, file_name=filename, status="failed")
            mdb.conn.close()
            cache = Path(self.app.config["COPY_CACHE_DIRS"][machine])
            cache.mkdir()
            (cache / "status.json").write_text(json.dumps({
                "host": machine + "-pc", "state": "running",
                "run_started": "2026-11-08T10:00:00+00:00"}))
        page = self.client.get("/copy-status/?machine=zoom").data
        self.assertIn(b"zoom.raw", page)
        self.assertNotIn(b"astral.raw", page)
        self.assertIn(b"Raptor: copy database was not found", page)
        self.assertIn(b"/copy-status/1?machine=zoom", page)
        self.assertIn(b'name="machine" value="zoom"', page)
        live = self.client.get("/copy-status/live?machine=zoom").get_json()
        self.assertEqual(live["service"]["host"], "zoom-pc")
        self.assertEqual(live["availability"], {"astral": True, "zoom": True, "raptor": False})
        self.assertIn(b"zoom.raw", self.client.get("/copy-status/1?machine=zoom").data)
        self.client.post("/copy-status/1/request?machine=zoom", data={"action": "retry"})
        self.assertTrue(list((self.tmp / "cache_zoom" / "requests").glob("*.json")))
        self.assertFalse((self.tmp / "cache" / "requests").exists())
        self.assertIn(b"retry requested", self.client.get("/copy-status/?machine=zoom").data)
        self.assertNotIn(b"retry requested", self.client.get("/copy-status/?machine=astral").data)
        self.assertEqual(self.client.get("/copy-status/?machine=raptor").status_code, 200)
        self.assertEqual(self.client.get("/copy-status/1?machine=raptor").status_code, 404)
        self.assertEqual(self.client.post("/copy-status/1/request?machine=raptor",
                                          data={"action": "retry"}).status_code, 404)
        self.assertEqual(self.client.get("/copy-status/?machine=invalid").status_code, 400)

    def test_offline_database_does_not_hide_available_service(self):
        import copy_raw_files
        # An unreadable/non-SQLite DB is treated like an offline service.
        (self.tmp / "match.db").write_text("unreadable database")
        mdb = copy_raw_files.MatchingDB(self.tmp / "zoom.db")
        mdb.upsert("zoom.raw", file_name="zoom.raw", status="waiting")
        mdb.conn.close()
        page = self.client.get("/copy-status/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"zoom.raw", page.data)
        self.assertIn(b"Astral: copy database was not found", page.data)
        self.assertFalse((self.tmp / "raptor.db").exists())

    def test_list_detail_and_request(self):
        import copy_raw_files
        mdb = copy_raw_files.MatchingDB(self.tmp / "match.db")
        mdb.upsert("D:\\Data\\" + NAME, file_name=NAME, status="failed", message="Attempt 5/5: boom",
                   project_code="GMB", experiment_code="NatGel", size_bytes=2 * 1024**3, attempts=5)
        mdb.upsert("D:\\Data\\x.raw", file_name="x.raw", status="ignored", message="Name does not follow")
        (self.tmp / "cache").mkdir()
        (self.tmp / "cache" / "status.json").write_text(json.dumps({
            "host": "PC1", "state": "running", "run_started": "2026-11-08T10:00:00+00:00",
            "current": {"file": NAME, "file_id": 1, "phase": "copying", "percent": 42, "speed": "100MB/s"}}))
        page = self.client.get("/copy-status/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(NAME.encode(), page.data)
        self.assertNotIn(b"x.raw</strong>", page.data)  # ignored is in its own tab
        self.assertIn(b"x.raw", self.client.get("/copy-status/?view=ignored").data)
        self.assertIn(b"2.00 GB", page.data)
        live = self.client.get("/copy-status/live").get_json()
        self.assertEqual(live["service"]["current"]["percent"], 42)
        self.assertEqual(live["counts"]["active"], 1)
        self.assertIn(b"History", self.client.get("/copy-status/1").data)

        response = self.client.post("/copy-status/1/request", data={"action": "retry", "next": "//evil.example"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/copy-status/?machine=astral"))
        written = list((self.tmp / "cache" / "requests").glob("*.json"))
        self.assertEqual(json.loads(written[0].read_text())["action"], "retry")
        self.assertIn(b"retry requested", self.client.get("/copy-status/").data)
        self.assertEqual(self.client.post("/copy-status/1/request", data={"action": "drop"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
