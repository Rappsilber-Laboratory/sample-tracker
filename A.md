# install python and rsync and set. 
COPY_INSTRUMENT = "astral"  # "zoom" or "raptor" on the other PCs
SOURCE_DIRS = [Path(r"D:\Data")]
DESTINATION_ROOT = Path(r"\\ms-server\RawData")
RSYNC_EXE = r"C:\Program Files\cwRsync\bin\rsync.exe"

# update the paths in the copy_raw_files.py script based on windows paths! 
ACQUISITIONS_DB_PATH = Path(_setting("ACQUISITIONS_DB_PATH", r"/home/lutfia95/sample-tracker-annotation/sample-tracker/acquisitions.db"))

python .\copy_raw_files.py --dry-run --verbose

# real runs: 
powershell -ExecutionPolicy Bypass -File .\install_copy_task.ps1 -Python "C:\Path\To\python.exe"


