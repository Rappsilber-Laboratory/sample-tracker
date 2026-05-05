"""
Migration 2: sync DB with sampleTracker14.dia
- mass_spec_sample: rename comment -> description
- mass_spec_acquisition: drop project_code/batch_name/sample_code,
  size_bytes REAL->INTEGER, date TEXT->DATE (ISO format)
- experiment: rename contact_person -> user_initials, add active column
- project: rename contact_person_initials -> user_initials
"""
import sqlite3
import sys

DB_PATH = "samples.db"

def parse_date(s):
    if not s:
        return None
    s = s.strip()
    if len(s) == 6:
        return f"20{s[:2]}-{s[2:4]}-{s[4:6]}"
    if len(s) == 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s  # already ISO or unrecognised — leave as-is

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
try:
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys = OFF")

    # 1. mass_spec_sample: rename comment -> description
    cur.execute("ALTER TABLE mass_spec_sample RENAME COLUMN comment TO description")

    # 2. experiment: rename contact_person -> user_initials, add active
    cur.execute("ALTER TABLE experiment RENAME COLUMN contact_person TO user_initials")
    cur.execute("ALTER TABLE experiment ADD COLUMN active INTEGER NOT NULL DEFAULT 1")

    # 3. project: rename contact_person_initials -> user_initials
    cur.execute("ALTER TABLE project RENAME COLUMN contact_person_initials TO user_initials")

    # 4. Recreate mass_spec_acquisition (type changes + drop columns + parse dates)
    rows = conn.execute("""
        SELECT id, sample_id, location, filename, size_bytes,
               instrument_initial, date, user_initials, scan_count, meta
        FROM mass_spec_acquisition
    """).fetchall()

    cur.executescript("""
        DROP TABLE mass_spec_acquisition;
        CREATE TABLE mass_spec_acquisition (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sample_id INTEGER REFERENCES mass_spec_sample(id),
            location TEXT,
            filename TEXT,
            size_bytes INTEGER,
            instrument_initial TEXT,
            date DATE,
            user_initials TEXT,
            scan_count INTEGER,
            meta TEXT
        );
    """)

    cur.executemany("""
        INSERT INTO mass_spec_acquisition
            (id, sample_id, location, filename, size_bytes,
             instrument_initial, date, user_initials, scan_count, meta)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        (
            r["id"], r["sample_id"], r["location"], r["filename"],
            int(r["size_bytes"]) if r["size_bytes"] is not None else None,
            r["instrument_initial"],
            parse_date(r["date"]),
            r["user_initials"], r["scan_count"], r["meta"],
        )
        for r in rows
    ])

    cur.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    print(f"Migration complete. Converted {len(rows)} acquisition rows.")

except Exception as e:
    conn.rollback()
    print(f"Migration failed: {e}")
    raise
finally:
    conn.close()
