# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Initialize the database (run once before first use)
flask init-db

# Run the development server (FLASK_DEBUG=1 permits the insecure dev SECRET_KEY;
# in any non-debug run a real SECRET_KEY env var is required or startup aborts)
FLASK_DEBUG=1 flask run
```

There are no tests or linting tools configured in this project.

## Architecture

This is a Flask web application for laboratory sample tracking, focused on protein cross-linking and mass spectrometry experiments.

### Core Files

- **app.py** — Flask app, all routes, CRUD logic, and form/model wiring
- **models.py** — SQLAlchemy ORM models with relationships
- **forms.py** — WTForms form definitions, including a custom `MultiCheckboxField`
- **config.py** — Flask/SQLAlchemy config (SQLite by default)
- **schema.sql** — Raw DDL; used by `flask init-db` to create tables
- **fileInfoScript.py** — standalone command-line `SpectraAddressBook` utility (run directly, e.g. `python fileInfoScript.py <path>`) that recursively scans directories for mass spectrometry data files (`.raw`, `.mgf`, `.mzml`, and `.d` acquisition directories) and writes a CSV. It is **not imported by the web app** — it's an offline helper for populating file inventories.

### Data Model

Five main entities: **Project → Experiment → Sample**, plus **Species**, **CellLine**, and **Virus** as reference data.

- Samples are polymorphic: `CrosslinkSample` vs `IdentificationSample`, each with a distinct set of type-specific fields. The discriminator is the `crosslinked_sample` flag (1 = crosslink, 0 = identification); app.py nullifies the irrelevant type's fields on save to maintain integrity.
- Species and CellLine relate to Sample via many-to-many junction tables (`sample_species`, `sample_cell_line`).
- CellLine relates to Virus via the `cell_line_virus` many-to-many junction table.
- Virus has an optional FK to Species and an optional `variant` field.
- Projects have an `active` flag for archival without data deletion.
- `Project.code`, `Experiment.code`, and `MassSpecSample.code` (the PK fields) may not contain underscores — enforced by the `no_underscores` validator in forms.py. This is required because the run queue's generated `file_name_root` (see Files & Run Queue) joins codes with `_` as a separator; an underscore inside a code would make that filename unparseable.

### Request Flow

1. Route handlers in `app.py` instantiate forms (from `forms.py`) and query models (from `models.py`).
2. Form choices (dropdowns, multi-selects) are populated dynamically from the database at request time.
3. On POST, form data is validated, then mapped to SQLAlchemy model instances and committed.
4. Templates in `templates/<entity>/` render the response; `base.html` and `_form_helpers.html` are shared.

### Files & Run Queue

- **Acquired files** are tracked as `AcquiredFile` DB rows (not by live disk scanning). They are listed at `/files`, created per-sample via `.../db-files/new`, and can be re-associated to a different sample at `/files/<id>/edit`. Sizes are stored in bytes (entered as decimal GB and multiplied by `1e9`).
- **Run queue** (`QueuedFile`): `/api/queue/*` endpoints append/update/delete/clear runs for an instrument's per-day run order, driven by the `static/js/queue-panel.js` panel. `daily_counter` is the run order; clearing marks rows `exported` rather than deleting so counters never get reused. `/api/queue/csv` exports a Thermo Xcalibur sequence CSV (cp1252-encoded).
- **Aggregate views**: `/api/tree` (size-sorted project→experiment→sample→file tree), `/instrument-usage`, and `/disk-usage`.
