"""One-shot migration: rename cell_line_code -> cellosaurus_id and make it the PK.

Drops & recreates cell_line, cell_line_virus, sample_cell_line (all empty in
samples.db at the time of writing), then seeds Mouse / African green monkey
species and 17 commonly-used cell lines with verified Cellosaurus accessions.
"""
import sqlite3

DB_PATH = "samples.db"

CELL_LINES = [
    # (cellosaurus_id, cell_line_name, species_common_name)
    ("CVCL_0030", "HeLa",         "Human"),
    ("CVCL_0063", "293T",         "Human"),
    ("CVCL_0367", "Jurkat E6.1",  "Human"),
    ("CVCL_0023", "A549",         "Human"),
    ("CVCL_0004", "K562",         "Human"),
    ("CVCL_0033", "SK-BR-3",      "Human"),
    ("CVCL_0031", "MCF7",         "Human"),
    ("CVCL_0045", "HEK293",       "Human"),
    ("CVCL_0042", "U2OS",         "Human"),
    ("CVCL_0224", "COS-7",        "African green monkey"),
    ("CVCL_A221", "Ma-Mel-86a",   "Human"),
    ("CVCL_0022", "U87-MG",       "Human"),
    ("CVCL_0105", "DU-145",       "Human"),
    ("CVCL_0291", "HCT 116",      "Human"),
    ("CVCL_0470", "Neuro-2A",     "Mouse"),
    ("CVCL_0134", "A2780",        "Human"),
    ("CVCL_1133", "Colo-741",     "Human"),
]

SPECIES_TAXA = {
    "Human": "9606",
    "Mouse": "10090",
    "African green monkey": "9534",
}


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")
    cur = conn.cursor()

    cur.execute("DROP TABLE IF EXISTS sample_cell_line")
    cur.execute("DROP TABLE IF EXISTS cell_line_virus")
    cur.execute("DROP TABLE IF EXISTS cell_line")

    cur.execute("""
        CREATE TABLE cell_line (
            cellosaurus_id TEXT NOT NULL PRIMARY KEY,
            cell_line_name TEXT NOT NULL,
            species_id INTEGER NOT NULL REFERENCES species(id)
        )
    """)
    cur.execute("""
        CREATE TABLE cell_line_virus (
            cellosaurus_id TEXT NOT NULL REFERENCES cell_line(cellosaurus_id),
            virus_id INTEGER NOT NULL REFERENCES virus(id),
            PRIMARY KEY (cellosaurus_id, virus_id)
        )
    """)
    cur.execute("""
        CREATE TABLE sample_cell_line (
            sample_code TEXT NOT NULL REFERENCES mass_spec_sample(code),
            cellosaurus_id TEXT NOT NULL REFERENCES cell_line(cellosaurus_id),
            PRIMARY KEY (sample_code, cellosaurus_id)
        )
    """)

    for name, taxon in SPECIES_TAXA.items():
        row = cur.execute(
            "SELECT id FROM species WHERE species_name = ?", (name,)
        ).fetchone()
        if row is None:
            cur.execute(
                "INSERT INTO species (species_name, species_taxon) VALUES (?, ?)",
                (name, taxon),
            )

    species_ids = {
        name: cur.execute(
            "SELECT id FROM species WHERE species_name = ?", (name,)
        ).fetchone()[0]
        for name in SPECIES_TAXA
    }

    for cvcl, name, sp_name in CELL_LINES:
        cur.execute(
            "INSERT INTO cell_line (cellosaurus_id, cell_line_name, species_id) VALUES (?, ?, ?)",
            (cvcl, name, species_ids[sp_name]),
        )

    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()

    print("Species:")
    for row in cur.execute("SELECT id, species_name, species_taxon FROM species ORDER BY id"):
        print(" ", row)
    print("Cell lines:")
    for row in cur.execute(
        "SELECT cl.cellosaurus_id, cl.cell_line_name, s.species_name "
        "FROM cell_line cl JOIN species s ON s.id = cl.species_id "
        "ORDER BY cl.cell_line_name"
    ):
        print(" ", row)

    conn.close()


if __name__ == "__main__":
    main()
