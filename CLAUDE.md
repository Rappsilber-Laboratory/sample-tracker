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

This is a Flask web application (the "File Acquisition Database") for tracking laboratory mass-spectrometry acquisitions, focused on protein cross-linking and identification experiments.

### Core Files

- **app.py** — Flask app, all routes, CRUD logic, and form/model wiring
- **models.py** — SQLAlchemy ORM models with relationships
- **forms.py** — WTForms form definitions, including a custom `MultiCheckboxField`
- **config.py** — Flask/SQLAlchemy config (SQLite by default)
- **schema.sql** — Raw DDL; used by `flask init-db` to create tables
- **fileInfoScript.py** — standalone command-line `SpectraAddressBook` utility (run directly, e.g. `python fileInfoScript.py <path>`) that recursively scans directories for mass spectrometry data files (`.raw`, `.mgf`, `.mzml`, and `.d` acquisition directories) and writes a CSV. It is **not imported by the web app** — it's an offline helper for populating file inventories.

### Data Model

Main entities: **Project → Experiment**, plus **Species**, **CellLine**, **Virus**, **Instrument**, and **User** as reference data.

- Experiments are polymorphic: `CrosslinkingExperiment` vs `IdentificationExperiment`, each with a distinct set of type-specific fields, on top of the shared invariant-condition fields carried by the base `Experiment`. The discriminator is the `crosslinking_experiment` flag (1 = crosslinking, 0 = identification, -1 = plain); app.py nullifies the irrelevant type's fields on save to maintain integrity. The type is editable on the edit page: since SQLAlchemy can't re-class an instance in place, `experiment_detail` switches it via a direct discriminator-column UPDATE (keeping the in-session instance's identity for the flush), and the row reloads as the correct subclass on the next request.
- Species and CellLine relate to Experiment via many-to-many junction tables (`experiment_species`, `experiment_cell_line`).
- CellLine relates to Virus via the `cell_line_virus` many-to-many junction table.
- Virus has an optional FK to Species and an optional `variant` field.
- **Instrument** (`initial` PK, `name`, `model`) is reference data with its own CRUD (like Users). `AcquiredFile.instrument_initial` references it loosely by initial (plain text, not an enforced FK — matching the `user_initials` convention).
- Projects have an `active` flag for archival without data deletion.
- `Project.code` and `Experiment.code` (the PK fields) must be alphanumeric and at most 8 characters — enforced by the `valid_code` validator in forms.py.

### Request Flow

1. Route handlers in `app.py` instantiate forms (from `forms.py`) and query models (from `models.py`).
2. Form choices (dropdowns, multi-selects) are populated dynamically from the database at request time.
3. On POST, form data is validated, then mapped to SQLAlchemy model instances and committed.
4. Templates in `templates/<entity>/` render the response; `base.html` and `_form_helpers.html` are shared.

### Files

- **Acquired files** are tracked as `AcquiredFile` DB rows (not by live disk scanning). They are listed at `/files`, created per-experiment via `.../db-files/new`, and can be re-associated to a different experiment at `/files/<id>/edit`. Sizes are stored in bytes (entered as decimal GB and multiplied by `1e9`).
- **Aggregate views**: `/api/tree` (size-sorted project→experiment→file tree), `/instrument-usage`, and `/disk-usage`.
