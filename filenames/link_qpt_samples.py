#!/usr/bin/env python3
"""Bulk-create experiments + samples for project QPT and link all QPT files.

Applies the parsing rule documented in PARSING_RULES.md uniformly to every
file with project_code='QPT'. Concretely:

    batch_name = <exp_code>_<descriptor>_S<plate>-<well>_1_<run>

  - One `experiment` row per distinct exp_code (first batch_name token).
  - One `sample` row per distinct (exp_code, descriptor) pair.
  - Every `file.sample_id` set accordingly.

No deduplication, no typo merging, no date-based splitting. If a batch_name
fails to parse the script aborts loudly. Refuses to run if QPT already has
any experiments or any QPT file already has sample_id set.
"""
import re
import sqlite3
import sys
from collections import OrderedDict
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "samples.db"
PROJECT_CODE = "QPT"

TAIL = re.compile(r"^(?P<prefix>.*?)_S\d+-[A-Z]\d+_1_\d+$")
EXP_NAME_SPLIT = re.compile(r"^([A-Za-z]+)(\d+)$")


def experiment_name(code):
    m = EXP_NAME_SPLIT.match(code)
    return f"{m.group(1)} {m.group(2)}" if m else code


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

            # Parse every batch_name; bail loudly on any failure.
            parsed = []
            for file_id, bn in files:
                m = TAIL.match(bn or "")
                if not m:
                    sys.exit(f"unparseable batch_name on file id={file_id}: {bn!r}")
                prefix = m.group("prefix")
                exp_code, _, descriptor = prefix.partition("_")
                parsed.append((file_id, exp_code, descriptor))

            # Insert experiments in deterministic order of first appearance.
            exp_ids = OrderedDict()
            for _, exp_code, _ in parsed:
                if exp_code not in exp_ids:
                    cur = conn.execute(
                        "INSERT INTO experiment (project_id, code, name) "
                        "VALUES (?, ?, ?)",
                        (project_id, exp_code, experiment_name(exp_code)),
                    )
                    exp_ids[exp_code] = cur.lastrowid

            # Insert samples in deterministic order of first appearance.
            sample_ids = {}  # (exp_code, descriptor) -> sample.id
            for _, exp_code, descriptor in parsed:
                key = (exp_code, descriptor)
                if key not in sample_ids:
                    cur = conn.execute(
                        "INSERT INTO sample "
                        "(experiment_id, name, code, crosslinked_sample) "
                        "VALUES (?, ?, ?, 0)",
                        (
                            exp_ids[exp_code],
                            descriptor.replace("_", " "),
                            descriptor,
                        ),
                    )
                    sample_ids[key] = cur.lastrowid

            # Link every file row.
            link_count = 0
            for file_id, exp_code, descriptor in parsed:
                conn.execute(
                    "UPDATE file SET sample_id = ? WHERE id = ?",
                    (sample_ids[(exp_code, descriptor)], file_id),
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
