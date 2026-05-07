"""
Migration: make description NOT NULL in project, experiment, mass_spec_sample.
Backfills existing NULLs to '-', then recreates each table with the constraint.
"""
import sqlite3
import sys

DB = "samples.db"

con = sqlite3.connect(DB)
con.execute("PRAGMA foreign_keys = OFF")

with con:
    # Backfill NULLs
    for table in ("project", "experiment", "mass_spec_sample"):
        con.execute(f"UPDATE {table} SET description = '-' WHERE description IS NULL")

    # Recreate project
    con.execute("""
        CREATE TABLE project_new (
            code TEXT NOT NULL PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            user_initials TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        )
    """)
    con.execute("INSERT INTO project_new SELECT code, name, description, user_initials, active FROM project")
    con.execute("DROP TABLE project")
    con.execute("ALTER TABLE project_new RENAME TO project")

    # Recreate experiment
    con.execute("""
        CREATE TABLE experiment_new (
            code TEXT NOT NULL PRIMARY KEY,
            project_code TEXT NOT NULL REFERENCES project(code),
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            user_initials TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        )
    """)
    con.execute("INSERT INTO experiment_new SELECT code, project_code, name, description, user_initials, active FROM experiment")
    con.execute("DROP TABLE experiment")
    con.execute("ALTER TABLE experiment_new RENAME TO experiment")

    # Recreate mass_spec_sample
    con.execute("""
        CREATE TABLE mass_spec_sample_new (
            code TEXT NOT NULL PRIMARY KEY,
            experiment_code TEXT NOT NULL REFERENCES experiment(code),
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            user_initials TEXT NOT NULL,
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
            peptide_level_fraction TEXT
        )
    """)
    con.execute("""
        INSERT INTO mass_spec_sample_new SELECT
            code, experiment_code, name, description, user_initials,
            disease, phenotype, isotope_labeling_channel, chemical_labelling,
            tissue, organism_age, organism_age_unit, organism_sex,
            enrichment_process, replicate, synthetic_peptide, digestion,
            protein_isolation_or_fractionation, crosslinked_sample,
            quantitation, quantitation_scheme, quantitation_method,
            crosslinker, crosslinking_type,
            protein_or_cell_concentration, protein_or_cell_concentration_unit,
            crosslinker_or_compound_concentration, crosslinker_or_compound_concentration_unit,
            organic_solvent_concentration, organic_solvent_concentration_unit,
            reaction_temperature_in_celsius, reaction_time_in_minutes,
            quenching_reagent, uv_source, uv_time_in_seconds, uv_wavelength_in_nanometers,
            peptide_level_fraction
        FROM mass_spec_sample
    """)
    con.execute("DROP TABLE mass_spec_sample")
    con.execute("ALTER TABLE mass_spec_sample_new RENAME TO mass_spec_sample")

con.execute("PRAGMA foreign_keys = ON")
result = con.execute("PRAGMA integrity_check").fetchone()[0]
con.close()

if result == "ok":
    print("Migration complete. Integrity check: ok")
else:
    print(f"WARNING: integrity_check returned: {result}", file=sys.stderr)
    sys.exit(1)
