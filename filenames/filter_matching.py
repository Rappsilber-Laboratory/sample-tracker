#!/usr/bin/env python3
"""Filter address_book.csv to filenames matching the lab run-name schema:

    InstrumentInitial_Date_ProjectCode_UserInitials_BatchName

ProjectCode is validated against ProjectCode.csv; UserInitials against
MSUserNameInitial.csv. Filenames prefixed with `recal_` are parsed as if
the prefix were not there (the original filename is preserved in the output).

By default, only rows whose `location` is under /data/synbox/MS_Data/ are
emitted. Pass --all-locations to disable that restriction.

Output: filenames/matching-filenames.csv with five new parsed columns.
"""
import argparse
import csv
from pathlib import Path

HERE = Path(__file__).resolve().parent

USER_TABLE = HERE / "MSUserNameInitial.csv"
PROJECT_TABLE = HERE / "ProjectCode.csv"
INPUT_CSV = HERE / "address_book.csv"
OUTPUT_CSV = HERE / "matching-filenames.csv"

LOCATION_PREFIX = "/data/synbox/MS_Data/"

OUTPUT_FIELDS = [
    "file_name", "location", "size_GB",
    "InstrumentInitial", "Date", "ProjectCode", "UserInitials", "BatchName",
]


def load_column(path, column):
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        return {row[column].strip() for row in reader if row.get(column, "").strip()}


def parse(name, project_codes, user_initials):
    parsed = name[6:] if name.startswith("recal_") else name
    stem = parsed.rsplit(".", 1)[0]
    parts = stem.split("_")
    if len(parts) < 5:
        return None
    if parts[2] not in project_codes or parts[3] not in user_initials:
        return None
    return {
        "InstrumentInitial": parts[0],
        "Date": parts[1],
        "ProjectCode": parts[2],
        "UserInitials": parts[3],
        "BatchName": "_".join(parts[4:]),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--all-locations",
        action="store_true",
        help=f"Include matching rows regardless of location. "
             f"Without this flag, only rows under {LOCATION_PREFIX} are emitted.",
    )
    args = ap.parse_args()

    project_codes = load_column(PROJECT_TABLE, "Project code")
    user_initials = load_column(USER_TABLE, "Initial used for MS run names")

    total = 0
    matched = 0
    with INPUT_CSV.open(newline="") as src, OUTPUT_CSV.open("w", newline="") as dst:
        reader = csv.DictReader(src)
        writer = csv.DictWriter(dst, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        for row in reader:
            total += 1
            if not args.all_locations and not row["location"].startswith(LOCATION_PREFIX):
                continue
            parsed = parse(row["file_name"], project_codes, user_initials)
            if parsed is None:
                continue
            writer.writerow({**row, **parsed})
            matched += 1

    scope = "all locations" if args.all_locations else f"locations under {LOCATION_PREFIX}"
    print(f"{matched} matched of {total} rows ({scope}) -> {OUTPUT_CSV.name}")


if __name__ == "__main__":
    main()
