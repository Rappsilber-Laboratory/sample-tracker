#!/usr/bin/env python3
"""Bulk-create one experiment + 16 samples for project QCX and link all 64
QCX files.

QCX is an ongoing instrument-QC HeLa stream: 64 files spread across 34
dates, with only 16 distinct `batch_name` values (the same QC sample is
re-run periodically). Per the user's decision, the whole project is
modelled as **one experiment** (`QCX001`, "QCX 001"), with **one sample
per distinct batch_name** — so when a given QC batch_name reappears on a
later date, the new file links to the existing sample.

Refuses to run if QCX already has any experiments or any QCX file already
has sample_id set.
"""
import sqlite3
import sys
from collections import OrderedDict
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "samples.db"
PROJECT_CODE = "QCX"
EXPERIMENT_CODE = "QCX001"
EXPERIMENT_NAME = "QCX 001"


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

            # Bail if any batch_name is NULL/empty — every QCX file should
            # have one for this rule to apply.
            for fid, bn in files:
                if not bn:
                    sys.exit(
                        f"file id={fid} has empty batch_name; "
                        "QCX rule requires every file to have one"
                    )

            # Insert the single experiment.
            cur = conn.execute(
                "INSERT INTO experiment (project_id, code, name) "
                "VALUES (?, ?, ?)",
                (project_id, EXPERIMENT_CODE, EXPERIMENT_NAME),
            )
            exp_id = cur.lastrowid

            # Insert one sample per distinct batch_name (order of first
            # appearance — deterministic since files are ordered by id).
            sample_ids = OrderedDict()
            for _, bn in files:
                if bn not in sample_ids:
                    cur = conn.execute(
                        "INSERT INTO sample "
                        "(experiment_id, name, code, crosslinked_sample) "
                        "VALUES (?, ?, ?, 0)",
                        (exp_id, bn.replace("_", " "), bn),
                    )
                    sample_ids[bn] = cur.lastrowid

            link_count = 0
            for fid, bn in files:
                conn.execute(
                    "UPDATE file SET sample_id = ? WHERE id = ?",
                    (sample_ids[bn], fid),
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
