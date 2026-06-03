"""One-off migration: move experiment/sample to parent-scoped composite keys.

Rebuilds samples.db with the new schema (composite PKs on experiment and
mass_spec_sample, with the parent key cascaded into mass_spec_acquisition,
sample_species and sample_cell_line). Parent columns are derived by joining
through the existing single-column codes, which are globally unique today so the
derivation is unambiguous.

Run once:  python migrate_composite_keys.py
A backup (samples.db.bak) should already exist; this script also refuses to run
if the new columns are already present.
"""
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "samples.db")
NEW_DB = os.path.join(HERE, "samples_new.db")
SCHEMA = os.path.join(HERE, "schema.sql")


def already_migrated(conn):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(mass_spec_sample)")]
    return "project_code" in cols


def main():
    if not os.path.exists(DB):
        sys.exit(f"No database at {DB}")

    src = sqlite3.connect(DB)
    if already_migrated(src):
        sys.exit("mass_spec_sample already has project_code — already migrated.")
    src.close()

    if os.path.exists(NEW_DB):
        os.remove(NEW_DB)

    with open(SCHEMA) as f:
        schema_sql = f.read()

    conn = sqlite3.connect(NEW_DB)
    conn.executescript(schema_sql)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(f"ATTACH DATABASE '{DB}' AS old")

    # Tables whose shape is unchanged — straight copy.
    for table in ("project", "species", "cell_line", "virus",
                  "cell_line_virus", "user"):
        conn.execute(f"INSERT INTO main.{table} SELECT * FROM old.{table}")

    # experiment: same columns, reordered.
    conn.execute(
        "INSERT INTO main.experiment "
        "(project_code, code, name, description, user_initials, active) "
        "SELECT project_code, code, name, description, user_initials, active "
        "FROM old.experiment"
    )

    # mass_spec_sample: derive project_code from the parent experiment.
    old_sample_cols = [r[1] for r in conn.execute("PRAGMA old.table_info(mass_spec_sample)")]
    rest = [c for c in old_sample_cols if c not in ("code", "experiment_code")]
    rest_sel = ", ".join(f"s.{c}" for c in rest)
    rest_into = ", ".join(rest)
    conn.execute(
        f"INSERT INTO main.mass_spec_sample "
        f"(project_code, experiment_code, code, {rest_into}) "
        f"SELECT e.project_code, s.experiment_code, s.code, {rest_sel} "
        f"FROM old.mass_spec_sample s "
        f"JOIN old.experiment e ON s.experiment_code = e.code"
    )

    # mass_spec_acquisition: derive project/experiment via sample (keep NULL FKs).
    old_acq_cols = [r[1] for r in conn.execute("PRAGMA old.table_info(mass_spec_acquisition)")]
    rest = [c for c in old_acq_cols if c != "sample_code"]
    rest_sel = ", ".join(f"a.{c}" for c in rest)
    rest_into = ", ".join(rest)
    conn.execute(
        f"INSERT INTO main.mass_spec_acquisition "
        f"({rest_into}, project_code, experiment_code, sample_code) "
        f"SELECT {rest_sel}, e.project_code, s.experiment_code, a.sample_code "
        f"FROM old.mass_spec_acquisition a "
        f"LEFT JOIN old.mass_spec_sample s ON a.sample_code = s.code "
        f"LEFT JOIN old.experiment e ON s.experiment_code = e.code"
    )

    # Junction tables: derive project/experiment via sample.
    conn.execute(
        "INSERT INTO main.sample_species "
        "(project_code, experiment_code, sample_code, species_id) "
        "SELECT e.project_code, s.experiment_code, ss.sample_code, ss.species_id "
        "FROM old.sample_species ss "
        "JOIN old.mass_spec_sample s ON ss.sample_code = s.code "
        "JOIN old.experiment e ON s.experiment_code = e.code"
    )
    conn.execute(
        "INSERT INTO main.sample_cell_line "
        "(project_code, experiment_code, sample_code, cellosaurus_id) "
        "SELECT e.project_code, s.experiment_code, scl.sample_code, scl.cellosaurus_id "
        "FROM old.sample_cell_line scl "
        "JOIN old.mass_spec_sample s ON scl.sample_code = s.code "
        "JOIN old.experiment e ON s.experiment_code = e.code"
    )

    conn.commit()

    # Integrity checks.
    conn.execute("PRAGMA foreign_keys=ON")
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        conn.close()
        sys.exit(f"FK violations after migration: {violations}")

    def count(c, t):
        return c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]

    for t in ("experiment", "mass_spec_sample", "mass_spec_acquisition",
              "sample_species", "sample_cell_line"):
        old_n = count(conn, f"old.{t}")
        new_n = count(conn, f"main.{t}")
        status = "OK" if old_n == new_n else "MISMATCH"
        print(f"  {t}: old={old_n} new={new_n} [{status}]")
        if old_n != new_n:
            conn.close()
            sys.exit(f"Row count mismatch on {t}")

    conn.execute("DETACH DATABASE old")
    conn.close()

    os.replace(NEW_DB, DB)
    print(f"Migration complete; {DB} replaced.")


if __name__ == "__main__":
    main()
