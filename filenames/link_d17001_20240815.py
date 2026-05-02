#!/usr/bin/env python3
"""Create the 8 IdentificationSample rows for experiment D17001 / 2024-08-15
and link the existing matching `file` rows to them.

The 8 .d files were loaded into the file table by populate_samples_db.py with
sample_id = NULL ("samples don't exist yet"). This script creates those samples
and back-fills file.sample_id.

Re-running is refused if any sample with code matching '8_60_sample_%' already
exists for D17001, to avoid duplicate inserts.
"""
import re
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "samples.db"

EXPERIMENT_CODE = "D17001"
FILE_DATE = "20240815"
FILE_PROJECT_CODE = "D17"
EXPECTED_FILES = 8

SAMPLE_INDEX_RE = re.compile(r"_sample_(\d+)_")


def main():
    if not DB_PATH.exists():
        sys.exit(f"{DB_PATH} not found")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:  # transaction
            exp = conn.execute(
                "SELECT id FROM experiment WHERE code = ?", (EXPERIMENT_CODE,)
            ).fetchall()
            if len(exp) != 1:
                sys.exit(f"expected exactly 1 experiment with code={EXPERIMENT_CODE}, found {len(exp)}")
            experiment_id = exp[0][0]

            existing = conn.execute(
                "SELECT COUNT(*) FROM sample "
                "WHERE experiment_id = ? AND code LIKE '8_60_sample_%' ESCAPE '\\'",
                (experiment_id,),
            ).fetchone()[0]
            if existing:
                sys.exit(
                    f"experiment {EXPERIMENT_CODE} already has {existing} samples "
                    "with code matching 8_60_sample_%. Refusing to duplicate."
                )

            files = conn.execute(
                "SELECT id, filename, batch_name FROM file "
                "WHERE date = ? AND project_code = ? AND sample_id IS NULL "
                "ORDER BY filename",
                (FILE_DATE, FILE_PROJECT_CODE),
            ).fetchall()
            if len(files) != EXPECTED_FILES:
                sys.exit(
                    f"expected {EXPECTED_FILES} unlinked files for date={FILE_DATE} "
                    f"project={FILE_PROJECT_CODE}, found {len(files)}"
                )

            seen_indices = set()
            for file_id, filename, batch_name in files:
                m = SAMPLE_INDEX_RE.search(batch_name or "") or SAMPLE_INDEX_RE.search(filename or "")
                if not m:
                    sys.exit(f"could not parse sample index from file id={file_id}: {filename}")
                idx = int(m.group(1))
                if idx in seen_indices:
                    sys.exit(f"duplicate sample index {idx} parsed from files")
                seen_indices.add(idx)

                code = f"8_60_sample_{idx}"
                name = f"8 60 sample {idx}"
                cur = conn.execute(
                    "INSERT INTO sample (experiment_id, name, code, crosslinked_sample) "
                    "VALUES (?, ?, ?, 0)",
                    (experiment_id, name, code),
                )
                sample_id = cur.lastrowid
                conn.execute(
                    "UPDATE file SET sample_id = ? WHERE id = ?",
                    (sample_id, file_id),
                )

        violations = list(conn.execute("PRAGMA foreign_key_check"))
        if violations:
            print("FK violations:", violations, file=sys.stderr)
            sys.exit(2)

        print(f"Created {EXPECTED_FILES} samples under experiment {EXPERIMENT_CODE} "
              f"(id={experiment_id}) and linked {EXPECTED_FILES} files.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
