#!/usr/bin/env python3
"""Bulk-link the seven small remaining projects (≤7 unlinked files each):
ERM, SPB, PLX, UVL, LMP, AMX, WTC. 26 files total.

These projects don't have enough volume to warrant per-project custom
parsing rules. Uniform fallback rule:

  - one experiment per project, code `<PROJECT>001`, name `<PROJECT> 001`
  - one sample per distinct `batch_name` (verbatim, underscores preserved)
  - `sample.name = batch_name.replace('_', ' ')`
  - `crosslinked_sample = 0`

Refuses to run if any of the target projects already has experiments or
already-linked files.
"""
import sqlite3
import sys
from collections import OrderedDict
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "samples.db"
PROJECT_CODES = ["ERM", "SPB", "PLX", "UVL", "LMP", "AMX", "WTC"]

# Some project codes are non-unique in the database; pin the project_id
# explicitly for those. SPB has two rows (10 'BS3 crosslinked SPB' and
# 40 'Septin Borg2 SDA crosslinking'). The unlinked SPB files all carry
# `SeptinBORG` in the batch_name, so they belong to id=40 (user-confirmed).
PROJECT_ID_OVERRIDES = {"SPB": 40}


def _link_project(conn, project_code):
    if project_code in PROJECT_ID_OVERRIDES:
        project_id = PROJECT_ID_OVERRIDES[project_code]
        exists = conn.execute(
            "SELECT 1 FROM project WHERE id = ? AND code = ?",
            (project_id, project_code),
        ).fetchone()
        if not exists:
            sys.exit(
                f"override project_id={project_id} for code={project_code} "
                "does not match any project row"
            )
    else:
        project = conn.execute(
            "SELECT id FROM project WHERE code = ?", (project_code,)
        ).fetchall()
        if len(project) != 1:
            sys.exit(
                f"expected exactly 1 project with code={project_code}, "
                f"found {len(project)}"
            )
        project_id = project[0][0]

    existing = conn.execute(
        "SELECT COUNT(*) FROM experiment WHERE project_id = ?",
        (project_id,),
    ).fetchone()[0]
    if existing:
        sys.exit(
            f"project {project_code} already has {existing} experiments. "
            "Refusing to populate."
        )
    already_linked = conn.execute(
        "SELECT COUNT(*) FROM file "
        "WHERE project_code = ? AND sample_id IS NOT NULL",
        (project_code,),
    ).fetchone()[0]
    if already_linked:
        sys.exit(
            f"{already_linked} {project_code} files already have a "
            "sample_id set. Refusing to populate."
        )

    files = conn.execute(
        "SELECT id, batch_name FROM file WHERE project_code = ? "
        "ORDER BY id",
        (project_code,),
    ).fetchall()
    if not files:
        return 0, 0  # nothing to do for this project

    cur = conn.execute(
        "INSERT INTO experiment (project_id, code, name) "
        "VALUES (?, ?, ?)",
        (project_id, f"{project_code}001", f"{project_code} 001"),
    )
    exp_id = cur.lastrowid

    sample_ids = OrderedDict()
    for fid, bn in files:
        if not bn:
            sys.exit(
                f"file id={fid} (project {project_code}) has empty batch_name; "
                "small-projects rule requires every file to have one"
            )
        if bn not in sample_ids:
            cur = conn.execute(
                "INSERT INTO sample "
                "(experiment_id, name, code, crosslinked_sample) "
                "VALUES (?, ?, ?, 0)",
                (exp_id, bn.replace("_", " "), bn),
            )
            sample_ids[bn] = cur.lastrowid

    for fid, bn in files:
        conn.execute(
            "UPDATE file SET sample_id = ? WHERE id = ?",
            (sample_ids[bn], fid),
        )

    print(
        f"  {project_code}: 1 experiment, {len(sample_ids)} samples, "
        f"{len(files)} files"
    )
    return len(sample_ids), len(files)


def main():
    if not DB_PATH.exists():
        sys.exit(f"{DB_PATH} not found")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:  # one transaction across all 7 projects
            total_samples = 0
            total_files = 0
            for code in PROJECT_CODES:
                ns, nf = _link_project(conn, code)
                total_samples += ns
                total_files += nf

        violations = list(conn.execute("PRAGMA foreign_key_check"))
        if violations:
            print("FK violations:", violations, file=sys.stderr)
            sys.exit(2)

        print(
            f"Created {len(PROJECT_CODES)} experiments, "
            f"{total_samples} samples, "
            f"linked {total_files} files across "
            f"{len(PROJECT_CODES)} small projects."
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
