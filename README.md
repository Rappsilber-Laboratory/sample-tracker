# File Acquisition Database

Simple setup and run instructions.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m flask --app app init-db

# Local development: FLASK_DEBUG=1 allows the built-in insecure dev key.
FLASK_DEBUG=1 python3 -m flask --app app run
```

For any non-debug / shared deployment, set a real secret instead of using debug mode —
the app refuses to start otherwise:

```bash
export SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
python3 -m flask --app app run
```

## Open in browser

After the Flask server starts, open:

`http://127.0.0.1:5000`

## File search

Click **Search** in the navigation to find acquired files
by any part of their name (case insensitive). Select a suggestion to open its
experiment. Unassociated acquired files open their file details instead.

Use **Local folder search** to select a folder on your computer and search filenames
in that folder and its subfolders. Files are searched in your browser without
uploading their contents. Results show relative paths; **Find experiment** looks up
the filename in the acquired-file database and provides any matching experiment links.

## Issues

Open **Issues** after **SDRF Export** in the navigation to create an issue with a
title, description, and your name. Issues start open. Users can add responses,
close resolved issues, and reopen them. The conversation preserves responses and
status changes with names and UTC timestamps. Filter the list by Open, Closed, or All.

Issues and their conversation history are stored on the host's disk in the same
SQLite database as the rest of the app (`acquisitions.db` by default, or the
database configured with `DATABASE_URL`). They survive browser and server restarts;
include this database in backups. The app creates the issue tables automatically
on startup, so existing installations do not need to run `init-db` again.
Names are entered by users, as the app has no sign-in system.

## Annotate old files

Open **Annotate old** after **Issues** to browse
`scanned_files_12102026_mapped.tsv` on the host. Set `LEGACY_SCAN_PATH` to use a
different TSV with `file_name`, `path`, `user`, `instrument`, and `project_code`
columns. Missing values such as `nan` are treated as unknown.

Filter by scanned user, instrument, or project; leave a filter on **Any** to skip
it, or choose **No selection / unknown** to find missing values. Select up to
1,000 files across pages, then click **Annotate selected files**. Assign an
existing project and experiment or create them in the annotation form. Either
assignment may be empty. For user and instrument, preserve each file's known
value, select a shared value, or leave it empty.

Saving creates acquired-file records in the host database, so they appear in file
search and their assigned experiments. Existing records are matched by full path
and skipped by default; updating them must be explicitly selected. File sizes and
dates stay empty because the scan does not provide them. The TSV and physical
files are unchanged. New experiments can be further configured on their experiment
page after importing.
