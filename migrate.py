"""
One-time migration for samples.db:
- Rename sample -> mass_spec_sample (drop file_name_root, add user_initials)
- Rename file -> mass_spec_acquisition (FK updated)
- Recreate junction tables with updated FK targets
- Recreate user table with initials as primary key (drop id)
"""
import sqlite3
import sys

DB_PATH = "samples.db"

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

try:
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys = OFF")

    # --- Check for users without initials (would be dropped) ---
    cur.execute("SELECT id, name, initials FROM user WHERE initials IS NULL OR initials = ''")
    bad_users = cur.fetchall()
    if bad_users:
        print("WARNING: The following users have no initials and will be dropped:")
        for u in bad_users:
            print(f"  id={u['id']} name={u['name']!r}")
        answer = input("Continue? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            sys.exit(1)

    # --- 1. Recreate sample -> mass_spec_sample ---
    cur.executescript("""
        CREATE TABLE mass_spec_sample (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            experiment_id INTEGER NOT NULL REFERENCES experiment(id),
            name TEXT NOT NULL,
            comment TEXT,
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
    """)

    cur.execute("""
        INSERT INTO mass_spec_sample (
            id, experiment_id, name, comment,
            disease, phenotype, isotope_labeling_channel, chemical_labelling,
            tissue, organism_age, organism_age_unit, organism_sex,
            enrichment_process, replicate, synthetic_peptide, digestion,
            protein_isolation_or_fractionation, crosslinked_sample, quantitation,
            quantitation_scheme, quantitation_method,
            crosslinker, crosslinking_type,
            protein_or_cell_concentration, protein_or_cell_concentration_unit,
            crosslinker_or_compound_concentration, crosslinker_or_compound_concentration_unit,
            organic_solvent_concentration, organic_solvent_concentration_unit,
            reaction_temperature_in_celsius, reaction_time_in_minutes,
            quenching_reagent, uv_source, uv_time_in_seconds, uv_wavelength_in_nanometers,
            peptide_level_fraction, code
        )
        SELECT
            id, experiment_id, name, comment,
            disease, phenotype, isotope_labeling_channel, chemical_labelling,
            tissue, organism_age, organism_age_unit, organism_sex,
            enrichment_process, replicate, synthetic_peptide, digestion,
            protein_isolation_or_fractionation, crosslinked_sample, quantitation,
            quantitation_scheme, quantitation_method,
            crosslinker, crosslinking_type,
            protein_or_cell_concentration, protein_or_cell_concentration_unit,
            crosslinker_or_compound_concentration, crosslinker_or_compound_concentration_unit,
            organic_solvent_concentration, organic_solvent_concentration_unit,
            reaction_temperature_in_celsius, reaction_time_in_minutes,
            quenching_reagent, uv_source, uv_time_in_seconds, uv_wavelength_in_nanometers,
            peptide_level_fraction, code
        FROM sample
    """)

    # --- 2. Recreate junction tables with updated FK ---
    cur.executescript("""
        CREATE TABLE sample_species_new (
            sample_id INTEGER NOT NULL REFERENCES mass_spec_sample(id),
            species_id INTEGER NOT NULL REFERENCES species(id),
            PRIMARY KEY (sample_id, species_id)
        );
        INSERT INTO sample_species_new SELECT * FROM sample_species;
        DROP TABLE sample_species;
        ALTER TABLE sample_species_new RENAME TO sample_species;

        CREATE TABLE sample_cell_line_new (
            sample_id INTEGER NOT NULL REFERENCES mass_spec_sample(id),
            cell_line_id INTEGER NOT NULL REFERENCES cell_line(id),
            PRIMARY KEY (sample_id, cell_line_id)
        );
        INSERT INTO sample_cell_line_new SELECT * FROM sample_cell_line;
        DROP TABLE sample_cell_line;
        ALTER TABLE sample_cell_line_new RENAME TO sample_cell_line;
    """)

    # Drop old sample table
    cur.execute("DROP TABLE sample")

    # --- 3. Rename file -> mass_spec_acquisition ---
    cur.executescript("""
        CREATE TABLE mass_spec_acquisition (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sample_id INTEGER REFERENCES mass_spec_sample(id),
            location TEXT,
            filename TEXT,
            size_bytes REAL,
            instrument_initial TEXT,
            date TEXT,
            project_code TEXT,
            user_initials TEXT,
            batch_name TEXT,
            scan_count INTEGER,
            meta TEXT,
            sample_code TEXT
        );
        INSERT INTO mass_spec_acquisition SELECT * FROM file;
        DROP TABLE file;
    """)

    # --- 4. Recreate user table with initials as PK ---
    cur.executescript("""
        CREATE TABLE user_new (
            initials TEXT PRIMARY KEY NOT NULL,
            name TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );
    """)
    cur.execute("""
        INSERT INTO user_new (initials, name, active)
        SELECT initials, name, active FROM user
        WHERE initials IS NOT NULL AND initials != ''
    """)
    cur.executescript("""
        DROP TABLE user;
        ALTER TABLE user_new RENAME TO user;
    """)

    cur.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    print("Migration complete.")

except Exception as e:
    conn.rollback()
    print(f"Migration failed: {e}")
    raise
finally:
    conn.close()
