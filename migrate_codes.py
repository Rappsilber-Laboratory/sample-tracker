"""
Migrate mass_spec_sample codes:
 1. Normalize: uppercase, strip non-alphanumeric characters
 2. Deduplicate: prepend experiment_code for any still-duplicate codes
 3. Rebuild mass_spec_sample, sample_species, sample_cell_line, mass_spec_acquisition
    to use sample.code (TEXT) as the primary/foreign key instead of id (INTEGER)

Run with: .venv/bin/python migrate_codes.py
"""

import re
import shutil
import sqlite3
from collections import Counter

DB_PATH = "samples.db"
BACKUP_PATH = "samples.db.bak"


def normalize(code):
    return re.sub(r"[^A-Z0-9]", "", code.upper())


def compute_new_codes(conn):
    rows = conn.execute(
        "SELECT id, code, experiment_code FROM mass_spec_sample ORDER BY id"
    ).fetchall()

    # First pass: normalize
    normalized = {row[0]: (normalize(row[1]), row[2]) for row in rows}

    # Count occurrences of each normalized code
    counts = Counter(nc for nc, _ in normalized.values())

    # Second pass: prepend experiment_code for duplicates
    id_to_new_code = {}
    for sample_id, (nc, exp_code) in normalized.items():
        if counts[nc] > 1:
            id_to_new_code[sample_id] = exp_code + nc
        else:
            id_to_new_code[sample_id] = nc

    # Verify uniqueness
    final_codes = list(id_to_new_code.values())
    final_counts = Counter(final_codes)
    duplicates = {c: n for c, n in final_counts.items() if n > 1}
    if duplicates:
        print("ERROR: Still-duplicate codes after transformation:")
        for dup_code, count in sorted(duplicates.items()):
            affected = [sid for sid, nc in id_to_new_code.items() if nc == dup_code]
            print(f"  {dup_code!r} (count={count}, ids={affected})")
        raise SystemExit(1)

    return id_to_new_code


def rebuild(conn, id_to_new_code):
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys = OFF")

    # ------------------------------------------------------------------
    # 1. Update codes in-place first (while id still exists as PK)
    # ------------------------------------------------------------------
    for sample_id, new_code in id_to_new_code.items():
        cur.execute(
            "UPDATE mass_spec_sample SET code = ? WHERE id = ?",
            (new_code, sample_id),
        )

    # ------------------------------------------------------------------
    # 2. Rebuild sample_species (sample_id -> sample_code)
    # ------------------------------------------------------------------
    cur.execute("ALTER TABLE sample_species RENAME TO _sample_species_old")
    cur.execute("""
        CREATE TABLE sample_species (
            sample_code TEXT NOT NULL REFERENCES mass_spec_sample(code),
            species_id  INTEGER NOT NULL REFERENCES species(id),
            PRIMARY KEY (sample_code, species_id)
        )
    """)
    cur.execute("""
        INSERT INTO sample_species (sample_code, species_id)
        SELECT mss.code, o.species_id
        FROM _sample_species_old o
        JOIN mass_spec_sample mss ON mss.id = o.sample_id
    """)
    cur.execute("DROP TABLE _sample_species_old")

    # ------------------------------------------------------------------
    # 3. Rebuild sample_cell_line (sample_id -> sample_code)
    # ------------------------------------------------------------------
    cur.execute("ALTER TABLE sample_cell_line RENAME TO _sample_cell_line_old")
    cur.execute("""
        CREATE TABLE sample_cell_line (
            sample_code  TEXT NOT NULL REFERENCES mass_spec_sample(code),
            cell_line_id INTEGER NOT NULL REFERENCES cell_line(id),
            PRIMARY KEY (sample_code, cell_line_id)
        )
    """)
    cur.execute("""
        INSERT INTO sample_cell_line (sample_code, cell_line_id)
        SELECT mss.code, o.cell_line_id
        FROM _sample_cell_line_old o
        JOIN mass_spec_sample mss ON mss.id = o.sample_id
    """)
    cur.execute("DROP TABLE _sample_cell_line_old")

    # ------------------------------------------------------------------
    # 4. Rebuild mass_spec_acquisition (sample_id INT -> sample_code TEXT)
    # ------------------------------------------------------------------
    cur.execute("ALTER TABLE mass_spec_acquisition RENAME TO _mass_spec_acquisition_old")
    cur.execute("""
        CREATE TABLE mass_spec_acquisition (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            sample_code        TEXT REFERENCES mass_spec_sample(code),
            location           TEXT,
            filename           TEXT,
            size_bytes         INTEGER,
            instrument_initial TEXT,
            date               DATE,
            user_initials      TEXT,
            scan_count         INTEGER,
            meta               TEXT
        )
    """)
    cur.execute("""
        INSERT INTO mass_spec_acquisition
            (id, sample_code, location, filename, size_bytes,
             instrument_initial, date, user_initials, scan_count, meta)
        SELECT o.id,
               mss.code,
               o.location, o.filename, o.size_bytes,
               o.instrument_initial, o.date, o.user_initials,
               o.scan_count, o.meta
        FROM _mass_spec_acquisition_old o
        LEFT JOIN mass_spec_sample mss ON mss.id = o.sample_id
    """)
    cur.execute("DROP TABLE _mass_spec_acquisition_old")

    # ------------------------------------------------------------------
    # 5. Rebuild mass_spec_sample (remove id, promote code to PK)
    # ------------------------------------------------------------------
    cur.execute("ALTER TABLE mass_spec_sample RENAME TO _mass_spec_sample_old")
    cur.execute("""
        CREATE TABLE mass_spec_sample (
            code                                    TEXT NOT NULL PRIMARY KEY,
            experiment_code                         TEXT NOT NULL REFERENCES experiment(code),
            name                                    TEXT NOT NULL,
            description                             TEXT,
            user_initials                           TEXT NOT NULL,
            disease                                 TEXT,
            phenotype                               TEXT,
            isotope_labeling_channel                TEXT,
            chemical_labelling                      TEXT,
            tissue                                  TEXT,
            organism_age                            REAL,
            organism_age_unit                       TEXT,
            organism_sex                            TEXT,
            enrichment_process                      TEXT,
            replicate                               TEXT,
            synthetic_peptide                       TEXT CHECK(synthetic_peptide IN ('yes', 'no', 'spiked in')),
            digestion                               TEXT,
            protein_isolation_or_fractionation      TEXT,
            crosslinked_sample                      INTEGER NOT NULL DEFAULT 0,
            quantitation                            INTEGER DEFAULT 0,
            quantitation_scheme                     TEXT,
            quantitation_method                     TEXT CHECK(quantitation_method IN ('LFQ', 'isotope labelled MS1', 'Isobaric MS2')),
            crosslinker                             TEXT,
            crosslinking_type                       TEXT CHECK(crosslinking_type IN ('in cell', 'in lysate', 'in solution', 'compound')),
            protein_or_cell_concentration           REAL,
            protein_or_cell_concentration_unit      TEXT,
            crosslinker_or_compound_concentration   REAL,
            crosslinker_or_compound_concentration_unit TEXT,
            organic_solvent_concentration           REAL,
            organic_solvent_concentration_unit      TEXT,
            reaction_temperature_in_celsius         REAL,
            reaction_time_in_minutes                REAL,
            quenching_reagent                       TEXT,
            uv_source                               TEXT,
            uv_time_in_seconds                      REAL,
            uv_wavelength_in_nanometers             REAL,
            peptide_level_fraction                  TEXT
        )
    """)
    cur.execute("""
        INSERT INTO mass_spec_sample SELECT
            code, experiment_code, name, description, user_initials,
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
            peptide_level_fraction
        FROM _mass_spec_sample_old
    """)
    cur.execute("DROP TABLE _mass_spec_sample_old")

    cur.execute("PRAGMA foreign_keys = ON")


def verify(conn, id_to_new_code):
    total = conn.execute("SELECT COUNT(*) FROM mass_spec_sample").fetchone()[0]
    unique = conn.execute("SELECT COUNT(DISTINCT code) FROM mass_spec_sample").fetchone()[0]
    acq_linked = conn.execute(
        "SELECT COUNT(*) FROM mass_spec_acquisition WHERE sample_code IS NOT NULL"
    ).fetchone()[0]
    acq_total = conn.execute("SELECT COUNT(*) FROM mass_spec_acquisition").fetchone()[0]
    ss_count = conn.execute("SELECT COUNT(*) FROM sample_species").fetchone()[0]
    scl_count = conn.execute("SELECT COUNT(*) FROM sample_cell_line").fetchone()[0]
    print(f"  mass_spec_sample:     {total} rows, {unique} unique codes (expected {len(id_to_new_code)})")
    print(f"  mass_spec_acquisition:{acq_total} rows, {acq_linked} linked to a sample")
    print(f"  sample_species:       {ss_count} rows")
    print(f"  sample_cell_line:     {scl_count} rows")
    if total != unique:
        raise SystemExit("ERROR: duplicate codes remain after migration")


def main():
    print(f"Backing up {DB_PATH} → {BACKUP_PATH}")
    shutil.copy2(DB_PATH, BACKUP_PATH)

    conn = sqlite3.connect(DB_PATH)
    try:
        print("Computing new codes …")
        id_to_new_code = compute_new_codes(conn)

        changed = sum(
            1 for sid, nc in id_to_new_code.items()
            if nc != conn.execute(
                "SELECT code FROM mass_spec_sample WHERE id=?", (sid,)
            ).fetchone()[0]
        )
        print(f"  {len(id_to_new_code)} samples total, {changed} codes will change")

        print("Rebuilding tables …")
        with conn:
            rebuild(conn, id_to_new_code)

        print("Verifying …")
        verify(conn, id_to_new_code)
        print("Migration complete.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
