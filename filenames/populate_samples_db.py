#!/usr/bin/env python3
"""Populate samples.db from the reference CSVs in this directory.

Loads:
    user     <- MSUserNameInitial.csv
    project  <- ProjectCode.csv      (contact_person_initials = looked-up Creater initials)
    file     <- matching-filenames.csv  (sample_id NULL; samples don't exist yet)

samples.db is treated as the real, persistent database — by default the script
refuses to run if it already exists. Pass --force to wipe and recreate.

Aborts (with a clear error listing every unmatched value) if any
ProjectCode.Creater is not present as a User in MSUserNameInitial.csv.
"""
import argparse
import csv
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent

DB_PATH = PROJECT_ROOT / "samples.db"
SCHEMA_PATH = PROJECT_ROOT / "schema.sql"
USER_CSV = HERE / "MSUserNameInitial.csv"
PROJECT_CSV = HERE / "ProjectCode.csv"
FILES_CSV = HERE / "matching-filenames.csv"


def create_db():
    schema_sql = SCHEMA_PATH.read_text()
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(schema_sql)
    conn.close()


def read_users():
    with USER_CSV.open(newline="") as f:
        return [
            (r["User"].strip(), r["Initial used for MS run names"].strip())
            for r in csv.DictReader(f)
            if r["User"].strip()
        ]


def read_projects():
    with PROJECT_CSV.open(newline="") as f:
        return [r for r in csv.DictReader(f) if r["Project code"].strip()]


def validate(user_rows, project_rows):
    """Raise SystemExit if any Creater has no matching User. Runs before any
    db work so a failed validation leaves no half-built samples.db behind."""
    names = {name for name, _ in user_rows}
    unmatched = sorted({
        r["Creater"].strip() for r in project_rows
        if r["Creater"].strip() and r["Creater"].strip() not in names
    })
    if unmatched:
        raise SystemExit(
            "Aborting: the following ProjectCode.Creater values have no matching "
            "User in MSUserNameInitial.csv:\n  - "
            + "\n  - ".join(unmatched)
            + "\nClean the CSVs and re-run."
        )


def load_users(conn, user_rows):
    conn.executemany("INSERT INTO user (name, initials) VALUES (?, ?)", user_rows)


def load_projects(conn, project_rows, name_to_initials):
    conn.executemany(
        "INSERT INTO project (code, name, description, contact_person_initials) "
        "VALUES (?, ?, ?, ?)",
        [
            (
                r["Project code"].strip(),
                r["discription"].strip(),
                r["discription"].strip(),
                name_to_initials.get(r["Creater"].strip()),
            )
            for r in project_rows
        ],
    )


def load_files(conn):
    with FILES_CSV.open(newline="") as f:
        rows = [
            (
                None,                         # sample_id
                r["location"],
                r["file_name"],
                float(r["size_GB"]) if r["size_GB"] else None,
                r["InstrumentInitial"],
                r["Date"],
                r["ProjectCode"],
                r["UserInitials"],
                r["BatchName"],
            )
            for r in csv.DictReader(f)
        ]
    conn.executemany(
        "INSERT INTO file (sample_id, location, filename, size_bytes, "
        "instrument_initial, date, project_code, user_initials, batch_name) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--force",
        action="store_true",
        help="Delete an existing samples.db before loading. Required to overwrite.",
    )
    args = ap.parse_args()

    if DB_PATH.exists():
        if not args.force:
            print(
                f"{DB_PATH.name} already exists — refusing to overwrite. "
                "Pass --force to recreate from scratch.",
                file=sys.stderr,
            )
            sys.exit(1)
        DB_PATH.unlink()

    user_rows = read_users()
    project_rows = read_projects()
    validate(user_rows, project_rows)
    name_to_initials = dict(user_rows)

    create_db()

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:  # transaction
            load_users(conn, user_rows)
            load_projects(conn, project_rows, name_to_initials)
            load_files(conn)

        violations = list(conn.execute("PRAGMA foreign_key_check"))
        if violations:
            print("FK violations:", violations, file=sys.stderr)
            sys.exit(2)

        print(f"Loaded {DB_PATH}:")
        for table in ("user", "project", "file"):
            (n,) = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            print(f"  {table}: {n}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
