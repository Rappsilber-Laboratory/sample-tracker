import os
import csv
from pathlib import Path
import argparse
import logging
import sys


class SpectraAddressBook:
    def __init__(self, search_root, outfile=None, logfile=None):
        self.search_root = Path(search_root).resolve()
        self.outfile = Path.cwd() / 'address_book.csv' if outfile is None else Path(outfile)
        self.logfile = Path.cwd() / 'output.log' if logfile is None else Path(logfile)
        self.extensions = {'.raw', '.mgf', '.mzml'}
        self.logger = logging.getLogger('SpectraAddressBook')
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
            sh = logging.StreamHandler(sys.stdout)
            sh.setFormatter(fmt)
            self.logger.addHandler(sh)
            try:
                fh = logging.FileHandler(self.logfile, encoding='utf-8')
                fh.setFormatter(fmt)
                self.logger.addHandler(fh)
            except Exception:
                pass

    def get_dir_size(self, path):
        total = 0
        for dirpath, _, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
        return total

    def collect(self):
        stack = [self.search_root]
        while stack:
            current = stack.pop()
            if "xi_data" in str(current) or os.path.islink(str(current)) or "new_storage" in str(current):
                self.logger.warning(f"Skipping the folder: {current}")
                continue
            try:
                with os.scandir(current) as it:
                    for entry in it:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if entry.name.lower().endswith('.d'):
                                    yield entry.name, str(Path(entry.path).resolve()), self.get_dir_size(
                                        entry.path)  # / (1024**3)
                                else:
                                    stack.append(entry.path)
                            else:
                                if Path(entry.name).suffix.lower() in self.extensions:
                                    yield entry.name, str(
                                        Path(entry.path).resolve()), entry.stat().st_size  # / (1024**3)
                        except PermissionError:
                            self.logger.warning(f'Cannot access fold due to permission issues: {entry.path}')
                        except OSError as e:
                            self.logger.warning(f'Cannot access path: {entry.path} ({e})')
            except PermissionError:
                self.logger.warning(f'Cannot access folder due to permission issues: {current}')
            except OSError as e:
                self.logger.warning(f'Cannot access folder: {current} ({e})')

    def write(self):
        try:
            exists = self.outfile.exists()
            mode = 'a' if exists else 'w'
            with open(self.outfile, mode, newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                if (not exists) or self.outfile.stat().st_size == 0:
                    w.writerow(['file_name', 'location', 'size_GB'])
                for name, loc, size in self.collect():
                    w.writerow([name, loc, f"{size:.3f}"])
        except Exception as e:
            self.logger.error(f'Failed writing CSV: {self.outfile} ({e})')

    def run(self):
        self.logger.info(f'Searching the parent folder: {self.search_root}')
        self.write()
        self.logger.info(f'Finished. Output CSV: {self.outfile}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument('path', type=str)
    args = parser.parse_args()
    SpectraAddressBook(args.path).run()