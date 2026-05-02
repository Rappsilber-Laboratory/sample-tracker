#!/usr/bin/env python3
"""Bulk-create 2 experiments + 12 samples for project LMB and link all 12
LMB files.

LMB has two dates that are different studies:

  - 250415 → LMB001 ("LMB 001"), CSA, 4 files → 4 samples
      `60_LiPMS9_(CSA-E|CSA-N)(_DIA)?`        → sample = trailing condition
  - 250613 → LMB002 ("LMB 002"), Eltrombopag + Lactate, 8 files → 8 samples
      `104_LiPMS9_(Eltrombopag-NL|Eltrombopag-NH)`         → sample = trailing
      `104_LiPMS9_(Lactate-N_<conc>mM)`                    → sample = trailing

The leading column-id token (`60_LiPMS9_`, `104_LiPMS9_`) is stripped from
the sample code (constant within each experiment, not part of identity).
No replicates to collapse — 1 sample per distinct batch_name.

Refuses to run if LMB001/LMB002 already exist or any matched file has
sample_id set.
"""
import re
import sqlite3
import sys
from collections import OrderedDict
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "samples.db"
PROJECT_CODE = "LMB"


def _parse_250415(bn):
    m = re.match(r"^60_LiPMS9_(CSA-[EN](?:_DIA)?)$", bn)
    return m.group(1) if m else None


def _parse_250613(bn):
    m = re.match(r"^104_LiPMS9_(Eltrombopag-\d+[LH])$", bn)
    if m:
        return m.group(1)
    m = re.match(r"^104_LiPMS9_(Lactate-\d+_\d+mM)$", bn)
    return m.group(1) if m else None


DATES = OrderedDict([
    ("250415", ("LMB001", "LMB 001", _parse_250415)),
    ("250613", ("LMB002", "LMB 002", _parse_250613)),
])


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

            for _, (exp_code, _, _) in DATES.items():
                existing = conn.execute(
                    "SELECT COUNT(*) FROM experiment "
                    "WHERE project_id = ? AND code = ?",
                    (project_id, exp_code),
                ).fetchone()[0]
                if existing:
                    sys.exit(
                        f"experiment {exp_code} already exists. "
                        "Refusing to populate."
                    )

            already = conn.execute(
                f"SELECT COUNT(*) FROM file "
                f"WHERE project_code = ? AND date IN ({','.join('?' * len(DATES))}) "
                f"AND sample_id IS NOT NULL",
                (PROJECT_CODE, *DATES.keys()),
            ).fetchone()[0]
            if already:
                sys.exit(
                    f"{already} {PROJECT_CODE} files in target dates already "
                    "have sample_id set. Refusing to populate."
                )

            total_exp = total_samples = total_files = 0
            for date, (exp_code, exp_name, parser) in DATES.items():
                files = conn.execute(
                    "SELECT id, batch_name FROM file "
                    "WHERE project_code = ? AND date = ? AND sample_id IS NULL "
                    "ORDER BY id",
                    (PROJECT_CODE, date),
                ).fetchall()
                if not files:
                    sys.exit(
                        f"no unlinked {PROJECT_CODE} files found for "
                        f"date={date}"
                    )

                parsed = []
                for fid, bn in files:
                    sc = parser(bn or "")
                    if sc is None:
                        sys.exit(
                            f"unparseable batch_name on file id={fid} "
                            f"(date={date}): {bn!r}"
                        )
                    parsed.append((fid, sc))

                cur = conn.execute(
                    "INSERT INTO experiment (project_id, code, name) "
                    "VALUES (?, ?, ?)",
                    (project_id, exp_code, exp_name),
                )
                exp_id = cur.lastrowid
                total_exp += 1

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
                total_samples += len(sample_ids)

                for fid, sc in parsed:
                    conn.execute(
                        "UPDATE file SET sample_id = ? WHERE id = ?",
                        (sample_ids[sc], fid),
                    )
                    total_files += 1

                print(
                    f"  {date} → {exp_code}: "
                    f"{len(sample_ids)} samples, {len(parsed)} files"
                )

        violations = list(conn.execute("PRAGMA foreign_key_check"))
        if violations:
            print("FK violations:", violations, file=sys.stderr)
            sys.exit(2)

        print(
            f"Created {total_exp} experiments, "
            f"{total_samples} samples, "
            f"linked {total_files} files for project {PROJECT_CODE}."
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
