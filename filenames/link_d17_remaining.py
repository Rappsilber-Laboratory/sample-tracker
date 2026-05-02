#!/usr/bin/env python3
"""Create experiments + samples for the four remaining unlinked D17 dates and
link the existing matching `file` rows to them.

The two earliest D17 dates (240815 → D17001, 240817 → D17002) were linked by
`link_d17001_20240815.py` / `link_d17002_20240817.py`. The remaining 99
unlinked D17 files span four further dates, each with a *different* batch_name
shape — so this script applies a per-date parser:

  - 240729 → D17003 ("D17 003"), 15 files → 7 samples
      `_DIA_pool-K`         → sample `DIA_pool`        (3 reps collapsed)
      `_pepSECfN-K`         → sample `pepSECfN`        (2 reps collapsed each)
  - 240806 → D17004 ("D17 004"), 30 files → 10 samples
      `_DIA_(pool|FN)-K`    → sample `pool` / `FN`     (3 reps collapsed each)
  - 250616 → D17005 ("D17 005"), 18 files → 7 samples
      `_(pool|pepSEC_N)_repK` → sample `pool` / `pepSEC_N` (2-3 reps each)
  - 260109 → D17006 ("D17 006"), 36 files → 36 samples
      `_DCAF17-HTBH_PD_<well>` → sample `<well>`       (no replicates to collapse)

Files for each date are filtered by `project_code='D17' AND date=<date> AND
sample_id IS NULL`. Refuses to run if any of D17003..D17006 already exist or
if any of the matched files already have sample_id set.
"""
import re
import sqlite3
import sys
from collections import OrderedDict, defaultdict
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "samples.db"
PROJECT_CODE = "D17"

# date -> (exp_code, exp_name, parser)
# parser: function(batch_name) -> sample_code or None.
def _parse_240729(bn):
    m = re.match(r"^\d+_FAIMS50_Hela_47_OTMS1ASMS2_DIA_pool-\d+$", bn)
    if m:
        return "DIA_pool"
    m = re.match(r"^\d+_FAIMS40-50-60_XL_101mins_OTMS1ASMS2_(pepSECf\d+)-\d+$", bn)
    if m:
        return m.group(1)
    return None


def _parse_240806(bn):
    m = re.match(r"^\d+_uPAC_Hela_47_OTMS1ASMS2_DIA_(pool|F\d+)-\d+$", bn)
    return m.group(1) if m else None


def _parse_250616(bn):
    m = re.match(r"^\d+_TEV-E-DSSO_(pool|pepSEC_\d+)_rep\d+$", bn)
    return m.group(1) if m else None


def _parse_260109(bn):
    m = re.match(r"^\d+_DCAF17-HTBH_PD_([A-Za-z]+\d+)$", bn)
    return m.group(1) if m else None


DATES = OrderedDict([
    ("240729", ("D17003", "D17 003", _parse_240729)),
    ("240806", ("D17004", "D17 004", _parse_240806)),
    ("250616", ("D17005", "D17 005", _parse_250616)),
    ("260109", ("D17006", "D17 006", _parse_260109)),
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

            # Refuse if any of the new exp codes already exist.
            for date, (exp_code, _, _) in DATES.items():
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

            # Refuse if any of the matched files already have sample_id set.
            already = conn.execute(
                f"SELECT COUNT(*) FROM file "
                f"WHERE project_code = ? AND date IN ({','.join('?' * len(DATES))}) "
                f"AND sample_id IS NOT NULL",
                (PROJECT_CODE, *DATES.keys()),
            ).fetchone()[0]
            if already:
                sys.exit(
                    f"{already} D17 files in target dates already have "
                    "sample_id set. Refusing to populate."
                )

            total_exp = 0
            total_samples = 0
            total_files = 0
            for date, (exp_code, exp_name, parser) in DATES.items():
                files = conn.execute(
                    "SELECT id, batch_name FROM file "
                    "WHERE project_code = ? AND date = ? AND sample_id IS NULL "
                    "ORDER BY id",
                    (PROJECT_CODE, date),
                ).fetchall()
                if not files:
                    sys.exit(
                        f"no unlinked D17 files found for date={date}; "
                        "nothing to do (script designed for first-time-only run)"
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

                # Insert experiment.
                cur = conn.execute(
                    "INSERT INTO experiment (project_id, code, name) "
                    "VALUES (?, ?, ?)",
                    (project_id, exp_code, exp_name),
                )
                exp_id = cur.lastrowid
                total_exp += 1

                # Insert samples in order of first appearance.
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

                # Link files.
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
