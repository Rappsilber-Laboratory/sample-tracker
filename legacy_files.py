"""Browse a hosted scan inventory and annotate selected files into the database."""
import csv
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_wtf import FlaskForm
from sqlalchemy.exc import IntegrityError
from wtforms import HiddenField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional

from forms import _strip_text, valid_code
from models import AcquiredFile, Experiment, IdentificationExperiment, Instrument, Project, User, db

legacy = Blueprint("legacy", __name__)
MAX_SELECTION = 1000


def _value(value):
    value = (value or "").strip()
    return "" if value.lower() in ("nan", "none", "null", "n/a") else value


@lru_cache(maxsize=2)
def _read_inventory(path, modified, size):
    rows = {}
    with open(path, encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        if not {"file_name", "path", "user", "instrument", "project_code"}.issubset(reader.fieldnames or []):
            raise ValueError("The scanned file must contain file_name, path, user, instrument, and project_code columns.")
        for row in reader:
            location = (row.get("path") or "").strip()
            if not location:
                continue
            key = hashlib.sha256(location.encode("utf-8")).hexdigest()
            rows[key] = dict(
                id=key, filename=(row.get("file_name") or "").strip() or location.replace("\\", "/").rsplit("/", 1)[-1],
                path=location, user=_value(row.get("user")), instrument=_value(row.get("instrument")),
                project_code=_value(row.get("project_code")),
            )
    return rows


def _inventory():
    path = Path(current_app.config["LEGACY_SCAN_PATH"])
    try:
        stat = path.stat()
        rows = _read_inventory(str(path), stat.st_mtime_ns, stat.st_size)
    except (OSError, UnicodeError, ValueError, csv.Error) as error:
        current_app.logger.warning("Cannot read scan inventory: %s", error)
        return None, "The scanned-file inventory could not be read. Check the configured TSV file on the host."
    return rows, None


class SelectionForm(FlaskForm):
    selected_ids = HiddenField(default="[]", validators=[DataRequired()])


class AnnotationForm(SelectionForm):
    project = SelectField("Project", validators=[Optional()])
    new_project_code = StringField("New project code", filters=[_strip_text], validators=[Optional(), valid_code])
    new_project_name = StringField("New project name", filters=[_strip_text], validators=[Optional(), Length(max=200)])
    new_project_description = TextAreaField("New project description", filters=[_strip_text], validators=[Optional(), Length(max=20000)])
    experiment = SelectField("Experiment", validators=[Optional()])
    new_experiment_code = StringField("New experiment code", filters=[_strip_text], validators=[Optional(), valid_code])
    new_experiment_name = StringField("New experiment name", filters=[_strip_text], validators=[Optional(), Length(max=200)])
    new_experiment_description = TextAreaField("New experiment description", filters=[_strip_text], validators=[Optional(), Length(max=20000)])
    user = SelectField("Assign user", validators=[Optional()])
    instrument = SelectField("Assign instrument", validators=[Optional()])
    existing = SelectField("Files already in the database", choices=[
        ("skip", "Skip existing files"), ("update", "Update selected existing files"),
    ], default="skip", validators=[DataRequired()])


def _selection(form, inventory):
    try:
        keys = json.loads(form.selected_ids.data or "[]")
        if keys == []:
            keys = request.form.getlist("file_ids")
        if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
            raise ValueError
        keys = list(dict.fromkeys(keys))
        if not 1 <= len(keys) <= MAX_SELECTION:
            raise ValueError
        return [inventory[key] for key in keys]
    except (ValueError, TypeError, KeyError):
        abort(400, description="Select between 1 and 1,000 files from the current inventory, then try again.")


def _records_by_path():
    records = {}
    for record in AcquiredFile.query.filter(AcquiredFile.location.isnot(None)).all():
        records.setdefault(record.location, []).append(record)
    return records


@legacy.route("/annotate-old")
def list_files():
    inventory, error = _inventory()
    form = SelectionForm()
    if error:
        return render_template("legacy/list.html", error=error, form=form), 503
    records = _records_by_path()
    filters = {key: request.args.get(key, "") for key in ("user", "instrument", "project_code", "q")}
    status = request.args.get("status", "pending")
    if status not in ("pending", "annotated", "all"):
        abort(400)
    choices = {key: sorted({row[key] for row in inventory.values() if row[key]}, key=str.casefold)
               for key in ("user", "instrument", "project_code")}
    matches = []
    for row in inventory.values():
        annotated = row["path"] in records
        if (status == "pending" and annotated) or (status == "annotated" and not annotated):
            continue
        if any(value and row[key] != ("" if value == "__missing__" else value)
               for key, value in filters.items() if key != "q"):
            continue
        if filters["q"].casefold() not in (row["filename"] + " " + row["path"]).casefold():
            continue
        matches.append(row)
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        abort(400)
    pages = max(1, (len(matches) + 99) // 100)
    page = min(page, pages)
    def page_url(number):
        return url_for("legacy.list_files", **filters, status=status, page=number)
    return render_template(
        "legacy/list.html", error=None, form=form, rows=matches[(page - 1) * 100:page * 100],
        total=len(inventory), matched=len(matches), choices=choices, filters=filters, status=status,
        records=records, page=page, pages=pages, page_url=page_url,
        selection_key="legacy-selection-" + hashlib.sha256(str(current_app.config["LEGACY_SCAN_PATH"]).encode()).hexdigest(),
    )


def _prepare_form(form, inventory):
    projects = Project.query.order_by(Project.code).all()
    experiments = Experiment.query.order_by(Experiment.project_code, Experiment.code).all()
    form.project.choices = [("", "No selection"), ("__new__", "+ Create new project")] + [
        (project.code, f"{project.code} — {project.name}") for project in projects
    ]
    form.experiment.choices = [("", "No selection"), ("__new__", "+ Create new experiment")] + [
        (experiment.code, f"{experiment.code} — {experiment.name}") for experiment in experiments
        if experiment.project_code == form.project.data
    ]
    users = {user.initials: user.name for user in User.query.order_by(User.initials)}
    instruments = {instrument.initial: instrument.name for instrument in Instrument.query.order_by(Instrument.initial)}
    for row in inventory.values():
        if row["user"]:
            users.setdefault(row["user"], "from scan")
        if row["instrument"]:
            instruments.setdefault(row["instrument"], "from scan")
    keep = [("__keep__", "Keep each file's current / scanned value"), ("", "No selection")]
    form.user.choices = keep + [(key, f"{key} — {value}") for key, value in sorted(users.items())]
    form.instrument.choices = keep + [(key, f"{key} — {value}") for key, value in sorted(instruments.items())]
    return [dict(project=experiment.project_code, code=experiment.code, name=experiment.name)
            for experiment in experiments]


@legacy.route("/annotate-old/select", methods=["POST"])
def select_files():
    inventory, error = _inventory()
    if error:
        abort(503, description=error)
    selection = SelectionForm()
    if not selection.validate_on_submit():
        abort(400, description="Select files to annotate.")
    rows = _selection(selection, inventory)
    form = AnnotationForm(formdata=None)
    form.selected_ids.data = json.dumps([row["id"] for row in rows])
    form.user.data = form.instrument.data = "__keep__"
    projects = {row["project_code"] for row in rows}
    if len(projects) == 1 and db.session.get(Project, next(iter(projects))):
        form.project.data = next(iter(projects))
    tree = _prepare_form(form, inventory)
    return render_template("legacy/annotate.html", form=form, rows=rows, tree=tree)


@legacy.route("/annotate-old/apply", methods=["POST"])
def apply_annotation():
    inventory, error = _inventory()
    if error:
        abort(503, description=error)
    form = AnnotationForm()
    tree = _prepare_form(form, inventory)
    rows = _selection(form, inventory)
    valid = form.validate_on_submit()
    for field, mode, label in (
        (form.new_project_code, form.project.data, "project"),
        (form.new_experiment_code, form.experiment.data, "experiment"),
    ):
        if mode == "__new__" and not field.data:
            field.errors.append(f"Enter a code for the new {label}.")
            valid = False
    project_code = form.new_project_code.data if form.project.data == "__new__" else form.project.data
    experiment_code = form.new_experiment_code.data if form.experiment.data == "__new__" else form.experiment.data
    if experiment_code and not project_code:
        form.project.errors.append("Select or create a project for this experiment.")
        valid = False
    if form.project.data == "__new__" and project_code and db.session.get(Project, project_code):
        form.new_project_code.errors.append("This project code already exists. Select the existing project.")
        valid = False
    if form.experiment.data == "__new__" and project_code and experiment_code and db.session.get(Experiment, (project_code, experiment_code)):
        form.new_experiment_code.errors.append("This experiment code already exists in this project.")
        valid = False
    records = _records_by_path()
    if form.existing.data == "update" and any(len(records.get(row["path"], [])) > 1 for row in rows):
        form.existing.errors.append("Some paths have multiple database records. Resolve those duplicates before updating them.")
        valid = False
    if not valid:
        return render_template("legacy/annotate.html", form=form, rows=rows, tree=tree), 422

    targets = [row for row in rows if form.existing.data == "update" or row["path"] not in records]
    if not targets:
        flash("All selected files already exist in the database; no changes were made.", "success")
        return redirect(url_for("legacy.list_files", done="1"))
    owner = "" if form.user.data in ("", "__keep__") else form.user.data
    try:
        if form.project.data == "__new__":
            db.session.add(Project(
                code=project_code, name=form.new_project_name.data or project_code,
                description=form.new_project_description.data or "", user_initials=owner,
            ))
            db.session.flush()
        if form.experiment.data == "__new__":
            db.session.add(IdentificationExperiment(
                project_code=project_code, code=experiment_code,
                name=form.new_experiment_name.data or experiment_code,
                description=form.new_experiment_description.data or "", user_initials=owner,
            ))
            db.session.flush()
        for row in targets:
            existing = records.get(row["path"], [])
            record = existing[0] if existing else AcquiredFile(location=row["path"], filename=row["filename"])
            record.project_code = project_code or None
            record.experiment_code = experiment_code or None
            if form.user.data != "__keep__":
                record.user_initials = form.user.data or None
            elif not existing:
                record.user_initials = row["user"] or None
            if form.instrument.data != "__keep__":
                record.instrument_initial = form.instrument.data or None
            elif not existing:
                record.instrument_initial = row["instrument"] or None
            if not existing:
                record.meta = json.dumps({"source": "legacy_scan", "scanned_user": row["user"],
                                          "scanned_instrument": row["instrument"], "scanned_project_code": row["project_code"]})
                db.session.add(record)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        form.project.errors.append("The database changed while saving. Check the project and experiment codes and try again.")
        return render_template("legacy/annotate.html", form=form, rows=rows, tree=tree), 409
    flash(f"Annotated {len(targets)} file(s). Skipped {len(rows) - len(targets)} existing file(s).", "success")
    return redirect(url_for("legacy.list_files", status="annotated", done="1"))
