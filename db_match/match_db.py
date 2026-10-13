from __future__ import annotations
import argparse
import json
import sqlite3
import sys
import time
import csv
from datetime import datetime
from typing import Literal
from pathlib import Path

from dataclasses import asdict, dataclass
from pathlib import Path


# Need to be added: move directory and windows new start automataion: 
#python match_db.py   --db_path acquisitions.db   --input-dir ./   --min-file-size-mb 250   --min-file-age-minutes 20 --log-tsv move_log.tsv --watch --poll-seconds 60

@dataclass(slots=True, frozen=True)
class Config:
    db_path: Path
    input_dir: Path
    min_file_size_mb: float
    min_file_age_minutes: float
    log_tsv_path: Path
    watch: bool
    poll_seconds: int

    @classmethod
    def from_args(cls) -> Config:
        parser = argparse.ArgumentParser(
            description="Move acquisition files based on queue."
        )
        parser.add_argument(
            "--db-path",
            "--db_path",
            dest="db_path",
            type=Path,
            required=True,
            help="Path to the acquisitions.db SQLite database.",
        )
        parser.add_argument(
            "--input-dir",
            "--input_dir",
            dest="input_dir",
            type=Path,
            required=True,
            help="Directory containing acquisition files.",
        )
        parser.add_argument(
            "--min-file-size-mb",
            dest="min_file_size_mb",
            type=float,
            default=250,
            help="Minimum file size to consider, in MB. Blank are normally below 100MB, and clean runs around 200 MB",
        )
        parser.add_argument(
            "--min-file-age-minutes",
            dest="min_file_age_minutes",
            type=float,
            default=20,
            help="Only consider files last modified at least this many minutes ago.",
        )
        parser.add_argument(
            "--log-tsv",
            dest="log_tsv_path",
            type=Path,
            default=Path("move_log.tsv"),
            help="Path to output TSV log file.",
        )
        
        parser.add_argument(
            "--watch",
            action="store_true",
            help="Keep running and scan the input directory repeatedly.",
        )
        parser.add_argument(
            "--poll-seconds",
            dest="poll_seconds",
            type=int,
            default=180,
            help="Seconds to wait between scans in watch mode.",
        )

        args = parser.parse_args()

        if args.min_file_size_mb < 0:
            parser.error("--min-file-size-mb must be >= 0")

        if args.min_file_age_minutes < 0:
            parser.error("--min-file-age-minutes must be >= 0")

        return cls(
            db_path=args.db_path,
            input_dir=args.input_dir,
            min_file_size_mb=args.min_file_size_mb,
            min_file_age_minutes=args.min_file_age_minutes,
            log_tsv_path=args.log_tsv_path,
            watch=args.watch,
            poll_seconds=args.poll_seconds,
        )

# A file is claimed by an experiment purely on its filename: it must carry a
# valid project code and experiment code in the expected positions. Nothing else
# (instrument, date, run number, user, or the variable-condition segments) affects
# the decision — they're parsed only for logging.
ExperimentKey = tuple[str, str]  # (project_code, experiment_code)


@dataclass(frozen=True, slots=True)
class FileNameStruct:
    instrument: str
    date: str
    run_number: str
    project: str
    user: str
    experiment: str

    @property
    def experiment_key(self) -> ExperimentKey:
        return (self.project, self.experiment)


class SampleTrackerDB:
    def __init__(self, dp_path: Path) -> None:
        self.db_path = dp_path

    def get_valid_experiment_keys(self) -> set[ExperimentKey]:
        """Every (project_code, experiment_code) pair that exists in the DB.

        Since experiment rows are keyed by (project_code, code), the presence of a
        pair here also confirms the project code is valid. A scanned file is
        associated with an experiment iff its filename's codes are in this set.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT project_code, code AS experiment_code FROM experiment"
            )
            return {(row["project_code"], row["experiment_code"]) for row in rows}

@dataclass(frozen=True, slots=True)
class AcquisitionFile:
    path: Path
    file_name_root: str
    struct: FileNameStruct
    size_mb: float
    age_minutes: float
    last_modified_time: datetime


class AcquisitionDirectoryScanner:
    def __init__(
        self,
        input_dir: Path,
        min_file_size_mb: float,
        min_file_age_minutes: float,
    ) -> None:
        self.input_dir = input_dir
        self.min_file_size_mb = min_file_size_mb
        self.min_file_age_minutes = min_file_age_minutes

    def scan(self) -> list[AcquisitionFile]:
        if not self.input_dir.is_dir():
            raise NotADirectoryError(f"Input directory not found: {self.input_dir}")

        acquisition_files: list[AcquisitionFile] = []

        for path in self.input_dir.rglob("*"):
            if not path.is_file():
                continue

            acquisition_file = self._path_to_acquisition_file(path)

            if acquisition_file is None:
                continue

            acquisition_files.append(acquisition_file)

        return acquisition_files

    def _path_to_acquisition_file(self, path: Path) -> AcquisitionFile | None:
        stat = path.stat()

        size_mb = stat.st_size / 1024 / 1024
        last_modified_time = datetime.fromtimestamp(stat.st_mtime).astimezone()
        age_minutes = (
            datetime.now().astimezone() - last_modified_time
        ).total_seconds() / 60

        if size_mb < self.min_file_size_mb: return None
        if age_minutes < self.min_file_age_minutes: return None

        file_name_root = path.stem
        struct = self._parse_file_name_root(file_name_root)

        if struct is None: return None

        return AcquisitionFile(
            path=path,
            file_name_root=file_name_root,
            struct=struct,
            size_mb=size_mb,
            age_minutes=age_minutes,
            last_modified_time=last_modified_time,
        )

    @staticmethod
    def _parse_file_name_root(file_name_root: str) -> FileNameStruct | None:
        # New filename scheme (see app.py _generate_runs):
        #   INSTRUMENT_YYYYMMDD-NNN_PROJECT_USER_EXPERIMENT[_seg1_seg2...]
        # The date and run number are fused into one underscore-piece
        # (YYYYMMDD-NNN); anything after the experiment code is variable-condition
        # segments and is ignored. We need at least the first five pieces.
        pieces = file_name_root.split("_")

        if len(pieces) < 5:
            return None

        date_run = pieces[1].split("-", 1)
        date = date_run[0]
        run_number = date_run[1] if len(date_run) == 2 else ""

        return FileNameStruct(
            instrument=pieces[0],
            date=date,
            run_number=run_number,
            project=pieces[2],
            user=pieces[3],
            experiment=pieces[4],
        )

LogStatus = Literal["moved", "kept"]


@dataclass(frozen=True, slots=True)
class MoveLogRow:
    file_name: str
    status: LogStatus
    destination_path: str
    file_size_mb: float
    last_modified_time: str


class MoveAuditLogger:
    fieldnames = [
        "scan_time",
        "file_name",
        "status",
        "destination_path",
        "file_size_mb",
        "last_modified_time",
    ]

    def __init__(self, log_tsv_path: Path) -> None:
        self.log_tsv_path = log_tsv_path

    def write(
        self,
        acquisition_files: list[AcquisitionFile],
        valid_experiment_keys: set[ExperimentKey],
    ) -> None:
        rows = [
            self._build_row(
                acquisition_file=acquisition_file,
                valid_experiment_keys=valid_experiment_keys,
            )
            for acquisition_file in acquisition_files
        ]

        self.log_tsv_path.parent.mkdir(parents=True, exist_ok=True)

        should_write_header = (
            not self.log_tsv_path.exists()
            or self.log_tsv_path.stat().st_size == 0
        )

        scan_time = datetime.now().astimezone().isoformat(timespec="seconds")

        with self.log_tsv_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=self.fieldnames,
                delimiter="\t",
            )

            if should_write_header:
                writer.writeheader()

            for row in rows:
                writer.writerow(
                    {
                        "scan_time": scan_time,
                        "file_name": row.file_name,
                        "status": row.status,
                        "destination_path": row.destination_path,
                        "file_size_mb": f"{row.file_size_mb:.3f}",
                        "last_modified_time": row.last_modified_time,
                    }
                )

    def _build_row(
        self,
        acquisition_file: AcquisitionFile,
        valid_experiment_keys: set[ExperimentKey],
    ) -> MoveLogRow:
        # A file is claimed ("moved") iff its filename's (project, experiment) codes
        # name an experiment that exists. The physical move + AcquiredFile
        # association are still TODO, so destination_path is left blank for now.
        status: LogStatus
        if acquisition_file.struct.experiment_key in valid_experiment_keys:
            status = "moved"
        else:
            status = "kept"

        return MoveLogRow(
            file_name=acquisition_file.path.name,
            status=status,
            destination_path="",
            file_size_mb=acquisition_file.size_mb,
            last_modified_time=acquisition_file.last_modified_time.isoformat(
                timespec="seconds"
            ),
        )

class Run:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.database = SampleTrackerDB(config.db_path)
        self.scanner = AcquisitionDirectoryScanner(
            input_dir=config.input_dir,
            min_file_size_mb=config.min_file_size_mb,
            min_file_age_minutes=config.min_file_age_minutes,
        )
        self.logger = MoveAuditLogger(config.log_tsv_path)

    def run(self) -> int:
        if self.config.watch:
            return self.run_forever()

        return self.run_once()

    def run_forever(self) -> int:
        while True:
            exit_code = self.run_once()

            if exit_code != 0:
                print(
                    "Scan failed. Will try again on next polling cycle.",
                    file=sys.stderr,
                )

            time.sleep(self.config.poll_seconds)

    def run_once(self) -> int:
        if not self.config.db_path.is_file():
            print(f"Database not found: {self.config.db_path}", file=sys.stderr)
            return 1

        try:
            valid_experiment_keys = self.database.get_valid_experiment_keys()
        except sqlite3.Error as exc:
            print(
                f"Could not read experiments from {self.config.db_path}: {exc}",
                file=sys.stderr,
            )
            return 1

        try:
            acquisition_files = self.scanner.scan()
        except OSError as exc:
            print(f"Could not scan input directory: {exc}", file=sys.stderr)
            return 1

        self.logger.write(
            acquisition_files=acquisition_files,
            valid_experiment_keys=valid_experiment_keys,
        )
        print(json.dumps(sorted(valid_experiment_keys), indent=2))
        print(json.dumps([asdict(file) for file in acquisition_files], indent=2, default=str))
        print(f"Wrote move log: {self.config.log_tsv_path}")

        return 0
    
    
def main() -> int:
    config = Config.from_args()
    app = Run(config)
    return app.run()


if __name__ == "__main__":
    raise SystemExit(main())