#!/usr/bin/env python3
"""Bulk-create one experiment + 3 samples for project MAB ("Antibody-antigen
crosslinking") and link all 3 MAB files.

All three batch_names share the form `71_s<N>_rep1_120ng` (column 71,
single replicate, 120ng load). The constant tokens are stripped from the
sample code so codes are just `s2`, `s3`, `s4`.

Modelled as **one experiment** (`MAB001`, "MAB 001"), 3 samples × 1 file each.
Refuses to run if MAB already has any experiments or any MAB file already
has sample_id set.
"""
import re
import sqlite3
import sys
from collections import OrderedDict
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "samples.db"
PROJECT_CODE = "MAB"
EXPERIMENT_CODE = "MAB001"
EXPERIMENT_NAME = "MAB 001"

BATCH_RE = re.compile(r"^71_(s\d+)_rep1_120ng$")


def main():
    if not DB_PATH.exists():
        sys.exit(f"{DB_PATH} not found")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:  # transaction
            project = conn.execute(
                "SELECT id FROM project WHERE code = ?", (PROJECT_CODE,)
            ).fetchall()
            if len(project) != 1:
                sys.exit(
                    f"expected exactly 1 project with code={PROJECT_CODE}, "
                    f"found {len(project)}"
                )
            project_id = project[0][0]

            existing_exps = conn.execute(
                "SELECT COUNT(*) FROM experiment WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0]
            if existing_exps:
                sys.exit(
                    f"project {PROJECT_CODE} already has {existing_exps} "
                    "experiments. Refusing to populate."
                )
            already_linked = conn.execute(
                "SELECT COUNT(*) FROM file "
                "WHERE project_code = ? AND sample_id IS NOT NULL",
                (PROJECT_CODE,),
            ).fetchone()[0]
            if already_linked:
                sys.exit(
                    f"{already_linked} {PROJECT_CODE} files already have a "
                    "sample_id set. Refusing to populate."
                )

            files = conn.execute(
                "SELECT id, batch_name FROM file WHERE project_code = ? "
                "ORDER BY id",
                (PROJECT_CODE,),
            ).fetchall()
            if not files:
                sys.exit(f"no files found for project_code={PROJECT_CODE}")

            parsed = []
            for fid, bn in files:
                m = BATCH_RE.match(bn or "")
                if not m:
                    sys.exit(f"unparseable batch_name on file id={fid}: {bn!r}")
                parsed.append((fid, m.group(1)))

            cur = conn.execute(
                "INSERT INTO experiment (project_id, code, name) "
                "VALUES (?, ?, ?)",
                (project_id, EXPERIMENT_CODE, EXPERIMENT_NAME),
            )
            exp_id = cur.lastrowid

            sample_ids = OrderedDict()
            for _, sc in parsed:
                if sc not in sample_ids:
                    cur = conn.execute(
                        "INSERT INTO sample "
                        "(experiment_id, name, code, crosslinked_sample) "
                        "VALUES (?, ?, ?, 0)",
                        (exp_id, sc, sc),
                    )
                    sample_ids[sc] = cur.lastrowid

            link_count = 0
            for fid, sc in parsed:
                conn.execute(
                    "UPDATE file SET sample_id = ? WHERE id = ?",
                    (sample_ids[sc], fid),
                )
                link_count += 1

        violations = list(conn.execute("PRAGMA foreign_key_check"))
        if violations:
            print("FK violations:", violations, file=sys.stderr)
            sys.exit(2)

        print(
            f"Created 1 experiment, {len(sample_ids)} samples, "
            f"linked {link_count} files for project {PROJECT_CODE}."
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
