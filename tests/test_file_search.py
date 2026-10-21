import os
import tempfile
import unittest

# Keep all test writes off the real DB.
_database = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = "sqlite:///" + _database.name + "/test.db"
os.environ["SECRET_KEY"] = "file-search-tests"

from app import app
from models import AcquiredFile, Experiment, Instrument, Project, Species, User, db


class FileSearchTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.context = app.app_context()
        self.context.push()
        db.create_all()
        db.session.add_all([
            Project(code="P1", name="Project one", description="", user_initials="AB"),
            Project(code="P2", name="Archived project", description="", user_initials="AB", active=False),
            User(initials="AB", name="Operator"),
            Instrument(initial="MS", name="Instrument"),
        ])
        db.session.flush()
        db.session.add_all([
            Experiment(project_code="P1", code="E1", name="First", description="", user_initials="AB"),
            Experiment(project_code="P2", code="E1", name="Second", description="", user_initials="AB", active=False),
        ])
        db.session.commit()
        self.client = app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def search(self, query):
        response = self.client.get("/api/files/search", query_string={"q": query})
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_create_experiment_from_experiments_page(self):
        page = self.client.get("/experiments")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"New Experiment", page.data)
        self.assertIn(b'name="project_code"', page.data)
        selected = self.client.get("/experiments/new", query_string={"project_code": "P1"})
        self.assertEqual(selected.status_code, 302)
        self.assertTrue(selected.location.endswith("/projects/P1/experiments/new"))
        created = self.client.post(selected.location, data={"code": "E02"})
        self.assertEqual(created.status_code, 302)
        self.assertTrue(created.location.endswith("/projects/P1/experiments/E02"))
        self.assertIsNotNone(db.session.get(Experiment, ("P1", "E02")))
        self.assertEqual(self.client.get(created.location).status_code, 200)

    def test_partial_case_insensitive_and_literal_wildcards(self):
        db.session.add_all([
            AcquiredFile(filename="Sample_A%1.raw", project_code="P1", experiment_code="E1"),
            AcquiredFile(filename="SampleXA21.raw", project_code="P2", experiment_code="E1"),
        ])
        db.session.commit()
        self.assertEqual(len(self.search("sAmPlE")["results"]), 2)
        matches = self.search("_A%1")["results"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["url"], "/projects/P1/experiments/E1")
        self.assertEqual(self.client.get(matches[0]["url"]).status_code, 200)
        self.assertEqual(self.search("   ")["results"], [])
        self.assertEqual(self.search("missing")["results"], [])

    def test_archived_composite_keys(self):
        db.session.add_all([
            AcquiredFile(filename="RUN_SAMPLE.RAW", project_code="P1", experiment_code="E1"),
            AcquiredFile(filename="run_sample.raw", project_code="P2", experiment_code="E1"),
        ])
        db.session.commit()
        matches = self.search("sample")["results"]
        self.assertEqual(len(matches), 2)
        by_project = {match["project_code"]: match for match in matches}
        self.assertEqual(by_project["P1"]["status"], "Acquired")
        self.assertEqual(by_project["P2"]["status"], "Acquired")
        self.assertEqual(by_project["P2"]["url"], "/projects/P2/experiments/E1")

    def test_unassociated_files_and_limit(self):
        db.session.add_all([AcquiredFile(filename=f"sample_{i}.raw") for i in range(35)])
        db.session.commit()
        data = self.search("sample")
        self.assertEqual(len(data["results"]), 30)
        self.assertTrue(data["has_more"])
        self.assertTrue(data["results"][0]["url"].startswith("/files/"))

    def generate(self, **overrides):
        data = dict(
            name="First", description="", user_initials="AB", action="generate",
            gen_instrument="MS", gen_user="AB", gen_date="2026-10-19", gen_run_start="1",
            varying_conditions='[{"prefix":"dose","values":"1,2","postfix":""}]',
            well_order="rowwise", well_start="A1",
        )
        data.update(overrides)
        return self.client.post("/projects/P1/experiments/E1", data=data)

    def test_generated_queue_is_not_searchable(self):
        response = self.generate(well_enable="y", well_in_filename="y")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/csv")
        names = [
            "MS_20261019-001_P1_AB_E1_dose1_A1",
            "MS_20261019-002_P1_AB_E1_dose2_A2",
        ]
        for name in names:
            self.assertIn(name, response.data.decode("utf-8-sig"))
        self.assertEqual(self.search("dose")["results"], [])

    def test_generate_persists_instrument_and_well_settings(self):
        self.generate(well_enable="y", well_order="columnwise", well_start="b3", well_in_position="y")
        db.session.expire_all()
        experiment = db.session.get(Experiment, ("P1", "E1"))
        self.assertEqual(experiment.instrument_initial, "MS")
        self.assertTrue(experiment.well_enable)
        self.assertEqual(experiment.well_order, "columnwise")
        self.assertEqual(experiment.well_start, "B3")
        self.assertFalse(experiment.well_in_filename)
        self.assertTrue(experiment.well_in_position)
        page = self.client.get("/projects/P1/experiments/E1").data.decode()
        self.assertIn('<option selected value="MS">', page)
        self.assertIn('value="B3"', page)

    def test_queue_comment_species_only_for_identification(self):
        db.session.add(Species(species_name="Homo sapiens", species_taxon="9606"))
        db.session.commit()
        response = self.generate(species_ids="1", crosslinker="DSSO", quenching_reagent="Tris")
        lines = response.data.decode("utf-8-sig").splitlines()
        self.assertTrue(lines[2].endswith(",Species: Homo sapiens"))

    def test_queue_comment_includes_crosslinker_and_quenching(self):
        db.session.add_all([
            Species(species_name="Homo sapiens", species_taxon="9606"),
            Species(species_name="Mus musculus", species_taxon="10090"),
        ])
        db.session.commit()
        response = self.generate(
            species_ids=["1", "2"], crosslinking_experiment="y",
            crosslinker="DSSO", quenching_reagent="Tris",
        )
        lines = response.data.decode("utf-8-sig").splitlines()
        self.assertTrue(lines[2].endswith(
            ',"Species: Homo sapiens, Mus musculus; Crosslinker: DSSO; Quenching: Tris"'
        ))

    def test_exact_local_filename_lookup(self):
        db.session.add_all([
            AcquiredFile(filename="sample.raw", project_code="P1", experiment_code="E1"),
            AcquiredFile(filename="prefix_sample.raw", project_code="P2", experiment_code="E1"),
        ])
        db.session.commit()
        response = self.client.get("/api/files/search", query_string={"q": "SAMPLE.RAW", "exact": "1"})
        matches = response.get_json()["results"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["url"], "/projects/P1/experiments/E1")

    def test_search_page_and_navigation(self):
        page = self.client.get("/search")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'aria-current="page"', page.data)
        self.assertIn(b'js/file-search.js', page.data)
        self.assertIn(b'id="file-search-input"', page.data)
        self.assertIn(b'webkitdirectory', page.data)
        self.assertIn(b'id="local-search-input"', page.data)
        self.assertNotIn(b'queued', page.data.lower())


if __name__ == "__main__":
    unittest.main()
