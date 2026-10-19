import csv
import json
import os
from pathlib import Path
import tempfile
import unittest

_database = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = "sqlite:///" + _database.name + "/legacy-tests.db"
os.environ["SECRET_KEY"] = "legacy-tests"

from app import app
from legacy_files import _inventory
from models import AcquiredFile, IdentificationExperiment, Instrument, Project, User, db


class LegacyFilesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.source = Path(self.directory.name) / "scan.tsv"
        with self.source.open("w", newline="") as stream:
            writer = csv.writer(stream, delimiter="\t")
            writer.writerow(["file_name", "path", "user", "instrument", "project_code"])
            writer.writerow(["known.raw", "/archive/known.raw", "AB", "MS", "P1"])
            writer.writerow(["unknown.raw", "/archive/unknown.raw", "nan", "NaN", "nan"])
            writer.writerow(["other.raw", "/archive/other.raw", "ZZ", "OLD", "OLD"])
        self.original_path = app.config["LEGACY_SCAN_PATH"]
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, LEGACY_SCAN_PATH=str(self.source))
        self.context = app.app_context()
        self.context.push()
        db.create_all()
        db.session.add_all([
            Project(code="P1", name="One", description="", user_initials="AB"),
            Project(code="P2", name="Two", description="", user_initials="AB"),
            User(initials="AB", name="Alice"), Instrument(initial="MS", name="Mass spec"),
        ])
        db.session.flush()
        db.session.add_all([
            IdentificationExperiment(project_code="P1", code="E1", name="First", description="", user_initials="AB"),
            IdentificationExperiment(project_code="P2", code="E2", name="Other", description="", user_initials="AB"),
        ])
        db.session.commit()
        self.client = app.test_client()
        self.rows = list(_inventory()[0].values())

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()
        app.config["LEGACY_SCAN_PATH"] = self.original_path
        self.directory.cleanup()

    def apply(self, indexes=(0, 1), **overrides):
        data = dict(selected_ids=json.dumps([self.rows[index]["id"] for index in indexes]),
                    project="P1", experiment="E1", user="__keep__", instrument="__keep__", existing="skip")
        data.update(overrides)
        return self.client.post("/annotate-old/apply", data=data)

    def test_inventory_filters_unknowns_and_navigation(self):
        page = self.client.get("/annotate-old")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"3 scanned files", page.data)
        self.assertLess(page.data.index(b">Issues</a>"), page.data.index(b">Annotate old</a>"))
        page = self.client.get("/annotate-old?user=__missing__&instrument=__missing__&project_code=__missing__")
        self.assertIn(b"unknown.raw", page.data)
        self.assertNotIn(b"<strong>known.raw</strong>", page.data)
        page = self.client.get("/annotate-old?user=AB&project_code=P1")
        self.assertIn(b"known.raw", page.data)
        self.assertNotIn(b"unknown.raw", page.data)
        self.assertEqual(self.client.get("/annotate-old?status=bad").status_code, 400)

    def test_review_and_import_preserve_known_values_and_normalize_unknown(self):
        selection = json.dumps([row["id"] for row in self.rows[:2]])
        page = self.client.post("/annotate-old/select", data={"selected_ids": selection})
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Annotate 2 selected files", page.data)
        original = self.source.read_bytes()
        response = self.apply()
        self.assertEqual(response.status_code, 302)
        known = AcquiredFile.query.filter_by(filename="known.raw").one()
        unknown = AcquiredFile.query.filter_by(filename="unknown.raw").one()
        self.assertEqual((known.project_code, known.experiment_code, known.user_initials, known.instrument_initial), ("P1", "E1", "AB", "MS"))
        self.assertIsNone(unknown.user_initials)
        self.assertIsNone(unknown.instrument_initial)
        self.assertEqual(unknown.experiment_code, "E1")
        self.assertEqual(self.source.read_bytes(), original)
        self.assertIn("known.raw", self.client.get("/api/files/search?q=known").get_data(as_text=True))
        self.assertNotIn(b"<strong>known.raw</strong>", self.client.get("/annotate-old").data)
        self.assertIn(b"<strong>known.raw</strong>", self.client.get("/annotate-old?status=annotated").data)

    def test_create_project_experiment_and_assign(self):
        response = self.apply(indexes=(2,), project="__new__", experiment="__new__", user="AB", instrument="MS",
                              new_project_code="NEW", new_project_name="New project", new_project_description="Project description",
                              new_experiment_code="E03", new_experiment_name="Legacy experiment")
        self.assertEqual(response.status_code, 302)
        project = db.session.get(Project, "NEW")
        self.assertEqual(project.name, "New project")
        experiment = db.session.get(IdentificationExperiment, ("NEW", "E03"))
        self.assertEqual(experiment.name, "Legacy experiment")
        record = AcquiredFile.query.one()
        self.assertEqual((record.project_code, record.experiment_code, record.user_initials, record.instrument_initial), ("NEW", "E03", "AB", "MS"))

    def test_allow_empty_assignments_and_project_without_experiment(self):
        response = self.apply(indexes=(0,), project="", experiment="", user="", instrument="")
        self.assertEqual(response.status_code, 302)
        record = AcquiredFile.query.one()
        self.assertEqual((record.project_code, record.experiment_code, record.user_initials, record.instrument_initial), (None, None, None, None))
        self.apply(indexes=(1,), project="P1", experiment="")
        record = AcquiredFile.query.filter_by(filename="unknown.raw").one()
        self.assertEqual(record.project_code, "P1")
        self.assertIn(b"unknown.raw", self.client.get("/projects/P1").data)
        self.assertIn(b'href="/projects/P1"', self.client.get(f"/files/{record.id}").data)
        edit = self.client.get(f"/files/{record.id}/edit")
        self.assertIn(b'selected value="P1"', edit.data)
        self.client.post(f"/files/{record.id}/edit", data={"project_code": "P1", "experiment_code": ""})
        self.assertEqual(record.project_code, "P1")

    def test_existing_skip_update_and_metadata_preservation(self):
        self.apply(indexes=(0,))
        record = AcquiredFile.query.one()
        record.size_bytes, record.meta = 1234, "original metadata"
        db.session.commit()
        self.apply(indexes=(0,), project="P2", experiment="E2", user="", instrument="")
        self.assertEqual(AcquiredFile.query.count(), 1)
        self.assertEqual(record.project_code, "P1")
        response = self.apply(indexes=(0,), project="P2", experiment="E2", existing="update", user="", instrument="")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(record.project_code, "P2")
        self.assertEqual(record.size_bytes, 1234)
        self.assertEqual(record.meta, "original metadata")
        self.assertIsNone(record.user_initials)

    def test_invalid_input_is_atomic(self):
        cases = [
            dict(project="P1", experiment="E2"),
            dict(project="", experiment="__new__", new_experiment_code="NEW"),
            dict(project="__new__", new_project_code="P1", experiment=""),
            dict(project="__new__", new_project_code="bad-code", experiment=""),
            dict(project="__new__", new_project_code="NEW", experiment="__new__", new_experiment_code=""),
            dict(user="forged"), dict(instrument="forged"),
        ]
        for overrides in cases:
            response = self.apply(**overrides)
            self.assertEqual(response.status_code, 422, overrides)
            self.assertEqual(AcquiredFile.query.count(), 0)
            self.assertIsNone(db.session.get(Project, "NEW"))
        for selection in ("[]", '"bad"', '["nonexistent"]', '[{}]'):
            self.assertEqual(self.apply(selected_ids=selection).status_code, 400)

    def test_missing_inventory_and_csrf(self):
        app.config["LEGACY_SCAN_PATH"] = str(self.source) + ".missing"
        page = self.client.get("/annotate-old")
        self.assertEqual(page.status_code, 503)
        self.assertIn(b"could not be read", page.data)
        app.config["LEGACY_SCAN_PATH"] = str(self.source)
        app.config["WTF_CSRF_ENABLED"] = True
        try:
            self.assertEqual(self.apply().status_code, 400)
        finally:
            app.config["WTF_CSRF_ENABLED"] = False


if __name__ == "__main__":
    unittest.main()
