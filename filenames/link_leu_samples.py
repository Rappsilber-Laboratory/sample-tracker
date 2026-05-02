#!/usr/bin/env python3
"""Bulk-create 2 experiments + 14 samples for project LEU and link all 15
LEU files.

LEU has two dates that are clearly different studies:

  - 251209 → LEU001 ("LEU 001"), atpG protein, 9 files → 8 samples
      `90_atpG_IDs_K`              → sample `atpG_IDs`         (2 reps collapsed)
      `90_atpG_pepSEC_fN`          → sample `atpG_pepSEC_fN`   (6 fractions, 1 file each)
      `HelaQC`                     → sample `HelaQC`           (instrument QC, kept here per user)
  - 260329 → LEU002 ("LEU 002"), atpD-Flag protein, 6 files → 6 samples
      `atpD_Flag_pepSECfN`         → sample `atpD_Flag_pepSECfN` (6 fractions, 1 file each)

Refuses to run if LEU001/LEU002 already exist or any matched file already
has sample_id set.
"""
import re
import sqlite3
import sys
from collections import OrderedDict
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "samples.db"
PROJECT_CODE = "LEU"


def _parse_251209(bn):
    if re.match(r"^90_atpG_IDs_\d+$", bn):
        return "atpG_IDs"
    m = re.match(r"^90_atpG_(pepSEC_f\d+)$", bn)
    if m:
        return f"atpG_{m.group(1)}"
    if bn == "HelaQC":
        return "HelaQC"
    return None


def _parse_260329(bn):
    m = re.match(r"^atpD_Flag_pepSECf\d+$", bn)
    return bn if m else None


DATES = OrderedDict([
    ("251209", ("LEU001", "LEU 001", _parse_251209)),
    ("260329", ("LEU002", "LEU 002", _parse_260329)),
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
