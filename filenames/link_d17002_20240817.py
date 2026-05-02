#!/usr/bin/env python3
"""Create the 8 IdentificationSample rows for experiment D17002 and link the
existing matching `file` rows to them.

Unlike D17001, this experiment has multiple files per sample (different wells):
samples 1-4 and 7-8 have 4 files each (rows A-D of one column), samples 5 and 6
have 8 files each (rows A-D across two columns) — 40 files total. Files are
grouped to samples by the `_sample_N_` token in the batch name.

File selection uses `batch_name LIKE 'D17002%'` — more robust than date+project
filtering, since the experiment code is encoded in the batch name itself.
"""
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "samples.db"

EXPERIMENT_CODE = "D17002"
BATCH_PREFIX = "D17002_"
EXPECTED_FILES = 40
EXPECTED_SAMPLE_INDICES = set(range(1, 9))

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
                "WHERE batch_name LIKE ? AND sample_id IS NULL "
                "ORDER BY filename",
                (BATCH_PREFIX + "%",),
            ).fetchall()
            if len(files) != EXPECTED_FILES:
                sys.exit(
                    f"expected {EXPECTED_FILES} unlinked files with batch prefix "
                    f"{BATCH_PREFIX!r}, found {len(files)}"
                )

            files_by_sample = defaultdict(list)
            for file_id, filename, batch_name in files:
                m = SAMPLE_INDEX_RE.search(batch_name or "") or SAMPLE_INDEX_RE.search(filename or "")
                if not m:
                    sys.exit(f"could not parse sample index from file id={file_id}: {filename}")
                files_by_sample[int(m.group(1))].append(file_id)

            indices = set(files_by_sample)
            if indices != EXPECTED_SAMPLE_INDICES:
                sys.exit(
                    f"expected sample indices {sorted(EXPECTED_SAMPLE_INDICES)}, "
                    f"got {sorted(indices)}"
                )

            link_count = 0
            for idx in sorted(files_by_sample):
                code = f"8_60_sample_{idx}"
                name = f"8 60 sample {idx}"
                cur = conn.execute(
                    "INSERT INTO sample (experiment_id, name, code, crosslinked_sample) "
                    "VALUES (?, ?, ?, 0)",
                    (experiment_id, name, code),
                )
                sample_id = cur.lastrowid
                for file_id in files_by_sample[idx]:
                    conn.execute(
                        "UPDATE file SET sample_id = ? WHERE id = ?",
                        (sample_id, file_id),
                    )
                    link_count += 1

        violations = list(conn.execute("PRAGMA foreign_key_check"))
        if violations:
            print("FK violations:", violations, file=sys.stderr)
            sys.exit(2)

        print(
            f"Created {len(files_by_sample)} samples under experiment {EXPERIMENT_CODE} "
            f"(id={experiment_id}) and linked {link_count} files."
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()