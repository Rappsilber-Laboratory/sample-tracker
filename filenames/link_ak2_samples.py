#!/usr/bin/env python3
"""Bulk-create experiments + samples for project AK2 and link all AK2 files.

AK2 batch_names do not match the QPT structural template
(`<exp_code>_<descriptor>_S<plate>-<well>_1_<run>`); the leading token is a
*condition*, not an experiment code. So this script applies a project-specific
rule documented in PARSING_RULES.md (AK2 section): each batch_name is routed to
one of three experiments by first-token prefix.

  - `EV_…` or `AK2_…`   → experiment AK2001 ("AK2 001")  — paired conditions
  - `FLAG-AK2_…`        → experiment AK2002 ("AK2 002")  — FLAG-AK2 pulldown
  - `10mM_…`            → experiment AK2003 ("AK2 003")  — 10mM SDA fractions

Within each experiment, one `sample` row per distinct `batch_name` (verbatim).
No deduplication, no merging. Refuses to run if AK2 already has any
experiments or any AK2 file already has sample_id set.
"""
import sqlite3
import sys
from collections import OrderedDict
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "samples.db"
PROJECT_CODE = "AK2"

# Routing rule: ordered list of (predicate, exp_code, exp_name).
# First match wins. `FLAG-AK2_` must be checked before `AK2_` since
# `FLAG-AK2` does not start with `AK2_` but the order is still defensive.
def _route(batch_name):
    if batch_name is None:
        return None
    if batch_name.startswith("FLAG-AK2_"):
        return ("AK2002", "AK2 002")
    if batch_name.startswith("EV_") or batch_name.startswith("AK2_"):
        return ("AK2001", "AK2 001")
    if batch_name.startswith("10mM_"):
        return ("AK2003", "AK2 003")
    return None


def main():
    if not DB_PATH.exists():
        sys.exit(f"{DB_PATH} not found")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:  # transaction
            projects = conn.execute(
                "SELECT id FROM project WHERE code = ?", (PROJECT_CODE,)
            ).fetchall()
            if len(projects) != 1:
                sys.exit(
                    f"expected exactly 1 project with code={PROJECT_CODE}, "
                    f"found {len(projects)}"
                )
            project_id = projects[0][0]

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

            # Route every batch_name; bail loudly on any unmatched prefix.
            routed = []
            for file_id, bn in files:
                r = _route(bn)
                if r is None:
                    sys.exit(
                        f"unroutable batch_name on file id={file_id}: {bn!r} "
                        f"(no matching prefix rule)"
                    )
                routed.append((file_id, bn, r))

            # Insert experiments in deterministic order of first appearance.
            exp_ids = OrderedDict()
            for _, _, (exp_code, exp_name) in routed:
                if exp_code not in exp_ids:
                    cur = conn.execute(
                        "INSERT INTO experiment (project_id, code, name) "
                        "VALUES (?, ?, ?)",
                        (project_id, exp_code, exp_name),
                    )
                    exp_ids[exp_code] = cur.lastrowid

            # Insert samples: one per distinct (exp_code, batch_name) pair.
            sample_ids = {}  # (exp_code, batch_name) -> sample.id
            for _, bn, (exp_code, _) in routed:
                key = (exp_code, bn)
                if key not in sample_ids:
                    cur = conn.execute(
                        "INSERT INTO sample "
                        "(experiment_id, name, code, crosslinked_sample) "
                        "VALUES (?, ?, ?, 0)",
                        (
                            exp_ids[exp_code],
                            bn.replace("_", " "),
                            bn,
                        ),
                    )
                    sample_ids[key] = cur.lastrowid

            # Link every file row.
            link_count = 0
            for file_id, bn, (exp_code, _) in routed:
                conn.execute(
                    "UPDATE file SET sample_id = ? WHERE id = ?",
                    (sample_ids[(exp_code, bn)], file_id),
                )
                link_count += 1

        violations = list(conn.execute("PRAGMA foreign_key_check"))
        if violations:
            print("FK violations:", violations, file=sys.stderr)
            sys.exit(2)

        print(
            f"Created {len(exp_ids)} experiments, "
            f"{len(sample_ids)} samples, "
            f"linked {link_count} files for project {PROJECT_CODE}."
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
