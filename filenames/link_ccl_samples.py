#!/usr/bin/env python3
"""Bulk-create one experiment + 12 samples for project CCL and link all 36
CCL files.

CCL is a clean 2 x 6 x 3 grid: 2 prep samples (`CollagenS1`, `CollagenS4`)
times 6 SEC fractions (f6-f11) times 3 replicates, all on date 241205.
Modelled as **one experiment** (`CCL001`, "CCL 001") with **one sample per
(prep, fraction) pair**: replicates collapse, matching the D17 style.

Sample codes: `CollagenS1-f6` ... `CollagenS4-f11` (12 total).

Refuses to run if CCL already has any experiments or any CCL file already
has sample_id set.
"""
import re
import sqlite3
import sys
from collections import OrderedDict
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "samples.db"
PROJECT_CODE = "CCL"
EXPERIMENT_CODE = "CCL001"
EXPERIMENT_NAME = "CCL 001"

# `100_CollagenS1-f6_rep1` → sample_code `CollagenS1-f6`
BATCH_RE = re.compile(r"^100_(Collagen(?:S\d+)-f\d+)_rep\d+$")


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
                        (exp_id, sc.replace("_", " "), sc),
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
