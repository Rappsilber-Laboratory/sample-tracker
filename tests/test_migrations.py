import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
QUEUE_COLUMNS = (
    "instrument_initial",
    "well_enable",
    "well_order",
    "well_start",
    "well_in_filename",
    "well_in_position",
)


class ExperimentQueueMigrationTests(unittest.TestCase):
    def test_existing_experiment_survives_upgrade_and_experiments_page_loads(self):
        schema = (ROOT / "schema.sql").read_text()
        start = schema.index("    -- Queue-generation settings remembered per experiment.")
        end = schema.index("    -- Invariant conditions defined on the experiment.", start)
        old_schema = schema[:start] + schema[end:]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.db"
            with sqlite3.connect(path) as connection:
                connection.executescript(old_schema)
                connection.execute(
                    "INSERT INTO project (code, name, description, user_initials) "
                    "VALUES ('P1', 'Project', '', 'AB')"
                )
                connection.execute(
                    "INSERT INTO experiment (project_code, code, name, description, user_initials) "
                    "VALUES ('P1', 'E1', 'Existing experiment', '', 'AB')"
                )

            environment = dict(
                os.environ,
                DATABASE_URL="sqlite:///" + str(path),
                SECRET_KEY="migration-test",
            )
            command = [
                sys.executable,
                "-c",
                "from app import app; "
                "response = app.test_client().get('/experiments'); "
                "assert response.status_code == 200, response.status_code; "
                "assert b'Existing experiment' in response.data",
            ]
            for _ in range(2):  # Repeated startups must be safe.
                result = subprocess.run(
                    command, env=environment, cwd=ROOT, capture_output=True, text=True
                )
                self.assertEqual(result.returncode, 0, result.stderr)

            with sqlite3.connect(path) as connection:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(experiment)")}
                self.assertTrue(set(QUEUE_COLUMNS) <= columns)
                row = connection.execute(
                    "SELECT name, instrument_initial, well_enable, well_order, well_start, "
                    "well_in_filename, well_in_position FROM experiment"
                ).fetchone()
                self.assertEqual(
                    row, ("Existing experiment", None, 0, "rowwise", "A1", 0, 0)
                )


if __name__ == "__main__":
    unittest.main()
