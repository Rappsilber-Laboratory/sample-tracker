"""
Migrate project and experiment from integer PKs to code-based PKs (Option C).
MassSpecSample keeps its integer PK; its experiment_id FK is renamed experiment_code.
sample_species, sample_cell_line, mass_spec_acquisition are preserved unchanged
(sample_id integers stay valid).

Usage: .venv/bin/python migrate_codes_to_pk.py
"""
import sqlite3
import sys

DB_PATH = "samples.db"


def abort(msg):
    print(f"ABORT: {msg}")
    sys.exit(1)


def check_codes(cur, table, code_col="code"):
    cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {code_col} IS NULL OR {code_col} = ''")
    if cur.fetchone()[0]:
        abort(f"{table} has rows with NULL/empty code. Fill in all codes before migrating.")
    cur.execute(f"SELECT {code_col}, COUNT(*) FROM {table} GROUP BY {code_col} HAVING COUNT(*) > 1")
    dups = cur.fetchall()
    if dups:
        abort(f"Duplicate codes in {table}: {[r[0] for r in dups]}")


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    print("Checking pre-conditions...")
    check_codes(cur, "project")
    check_codes(cur, "experiment")
    print("All codes are present and unique. Proceeding.")

    before = {}
    for t in ("project", "experiment", "mass_spec_sample",
              "mass_spec_acquisition", "sample_species", "sample_cell_line"):
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        before[t] = cur.fetchone()[0]

    conn.execute("PRAGMA foreign_keys = OFF")

    conn.executescript("""
        -- project: code becomes PK
        CREATE TABLE project_new (
            code TEXT NOT NULL PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            user_initials TEXT,
            active INTEGER NOT NULL DEFAULT 1
        );
        INSERT INTO project_new SELECT code, name, description, user_initials, active
        FROM project;

        -- experiment: code becomes PK, project_code FK replaces project_id
        CREATE TABLE experiment_new (
            code TEXT NOT NULL PRIMARY KEY,
            project_code TEXT NOT NULL REFERENCES project_new(code),
            name TEXT NOT NULL,
            description TEXT,
            user_initials TEXT,
            active INTEGER NOT NULL DEFAULT 1
        );
        INSERT INTO experiment_new
            SELECT e.code, p.code, e.name, e.description, e.user_initials, e.active
            FROM experiment e
            JOIN project p ON e.project_id = p.id;

        -- mass_spec_sample: keep integer id PK; rename experiment_id -> experiment_code
        CREATE TABLE mass_spec_sample_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            experiment_code TEXT NOT NULL REFERENCES experiment_new(code),
            name TEXT NOT NULL,
            description TEXT,
            user_initials TEXT,
            disease TEXT,
            phenotype TEXT,
            isotope_labeling_channel TEXT,
            chemical_labelling TEXT,
            tissue TEXT,
            organism_age REAL,
            organism_age_unit TEXT,
            organism_sex TEXT,
            enrichment_process TEXT,
            replicate TEXT,
            synthetic_peptide TEXT CHECK(synthetic_peptide IN ('yes', 'no', 'spiked in')),
            digestion TEXT,
            protein_isolation_or_fractionation TEXT,
            crosslinked_sample INTEGER NOT NULL DEFAULT 0,
            quantitation INTEGER DEFAULT 0,
            quantitation_scheme TEXT,
            quantitation_method TEXT CHECK(quantitation_method IN ('LFQ', 'isotope labelled MS1', 'Isobaric MS2')),
            crosslinker TEXT,
            crosslinking_type TEXT CHECK(crosslinking_type IN ('in cell', 'in lysate', 'in solution', 'compound')),
            protein_or_cell_concentration REAL,
            protein_or_cell_concentration_unit TEXT,
            crosslinker_or_compound_concentration REAL,
            crosslinker_or_compound_concentration_unit TEXT,
            organic_solvent_concentration REAL,
            organic_solvent_concentration_unit TEXT,
            reaction_temperature_in_celsius REAL,
            reaction_time_in_minutes REAL,
            quenching_reagent TEXT,
            uv_source TEXT,
            uv_time_in_seconds REAL,
            uv_wavelength_in_nanometers REAL,
            peptide_level_fraction TEXT,
            code TEXT
        );
        INSERT INTO mass_spec_sample_new
            SELECT s.id, e.code,
                   s.name, s.description, s.user_initials,
                   s.disease, s.phenotype, s.isotope_labeling_channel, s.chemical_labelling,
                   s.tissue, s.organism_age, s.organism_age_unit, s.organism_sex,
                   s.enrichment_process, s.replicate, s.synthetic_peptide, s.digestion,
                   s.protein_isolation_or_fractionation, s.crosslinked_sample,
                   s.quantitation, s.quantitation_scheme, s.quantitation_method,
                   s.crosslinker, s.crosslinking_type,
                   s.protein_or_cell_concentration, s.protein_or_cell_concentration_unit,
                   s.crosslinker_or_compound_concentration, s.crosslinker_or_compound_concentration_unit,
                   s.organic_solvent_concentration, s.organic_solvent_concentration_unit,
                   s.reaction_temperature_in_celsius, s.reaction_time_in_minutes,
                   s.quenching_reagent, s.uv_source, s.uv_time_in_seconds,
                   s.uv_wavelength_in_nanometers, s.peptide_level_fraction, s.code
            FROM mass_spec_sample s
            JOIN experiment e ON s.experiment_id = e.id;

        -- Drop old project/experiment (sample_species, sample_cell_line, and
        -- mass_spec_acquisition reference mass_spec_sample by integer id and
        -- are unaffected — no need to touch them).
        DROP TABLE mass_spec_sample;
        DROP TABLE experiment;
        DROP TABLE project;

        ALTER TABLE mass_spec_sample_new RENAME TO mass_spec_sample;
        ALTER TABLE experiment_new RENAME TO experiment;
        ALTER TABLE project_new RENAME TO project;
    """)

    conn.execute("PRAGMA foreign_keys = ON")

    print("\nRow count verification:")
    ok = True
    cur2 = conn.cursor()
    for t, count_before in before.items():
        cur2.execute(f"SELECT COUNT(*) FROM {t}")
        count_after = cur2.fetchone()[0]
        status = "OK" if count_before == count_after else "MISMATCH"
        if status != "OK":
            ok = False
        print(f"  {t}: {count_before} → {count_after}  {status}")

    cur2.execute("PRAGMA foreign_key_check")
    violations = cur2.fetchall()
    if violations:
        print(f"\nFK violations: {violations}")
        ok = False
    else:
        print("\nNo FK violations.")

    conn.close()

    if ok:
        print("\nMigration complete.")
    else:
        print("\nMigration completed with warnings — review output above.")


if __name__ == "__main__":
    main()
