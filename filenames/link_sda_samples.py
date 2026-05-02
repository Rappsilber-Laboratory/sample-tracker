#!/usr/bin/env python3
"""Bulk-create one experiment + 6 samples for project SDA and link all 12
SDA files (single date 240724).

The leading two tokens (`<run>_<column>_`) are run-order and column-id, both
varied per file and not part of sample identity. Replicate suffix `_repN`
collapses (D17/CCL/LEU style). Acquisition-mode tokens (DDA/DIA) stay as
part of sample identity because they reflect distinct sample runs.

Sample groupings:
  `<n>_<col>_HeLa_(DDA|DIA)`              → `HeLa_DDA` / `HeLa_DIA`     (1 file each)
  `<n>_<col>_Control_SDA-DTB_DDA_repK`    → `Control_SDA-DTB_DDA`        (3 reps)
  `<n>_<col>_Enrichment_SDA-DTB_DDA_repK` → `Enrichment_SDA-DTB_DDA`     (3 reps)
  `<n>_<col>_pepSEC_N_repK`               → `pepSEC_N`                   (pepSEC_6 = 3 reps, pepSEC_7 = 1)

Refuses to run if SDA already has any experiments or any SDA file has
sample_id set.
"""
import re
import sqlite3
import sys
from collections import OrderedDict
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "samples.db"
PROJECT_CODE = "SDA"
EXPERIMENT_CODE = "SDA001"
EXPERIMENT_NAME = "SDA 001"

PATTERNS = [
    (re.compile(r"^\d+_\d+_HeLa_(DDA|DIA)$"), lambda m: f"HeLa_{m.group(1)}"),
    (re.compile(r"^\d+_\d+_(Control|Enrichment)_SDA-DTB_DDA_rep\d+$"),
        lambda m: f"{m.group(1)}_SDA-DTB_DDA"),
    (re.compile(r"^\d+_\d+_(pepSEC_\d+)_rep\d+$"), lambda m: m.group(1)),
]


def _parse(bn):
    for rgx, mk in PATTERNS:
        m = rgx.match(bn)
        if m:
            return mk(m)
    return None


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
                sc = _parse(bn or "")
                if sc is None:
                    sys.exit(f"unparseable batch_name on file id={fid}: {bn!r}")
                parsed.append((fid, sc))

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
