import csv
import io
import itertools
import json
import os
import re
import sqlite3
from datetime import date
from pathlib import Path

import click
from collections import defaultdict

from flask import Flask, Response, abort, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import event, func, inspect as sa_inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from flask_wtf import CSRFProtect
from sqlalchemy.orm import joinedload

from config import Config
from forms import (
    CellLineForm,
    ExperimentForm,
    ExperimentCreateForm,
    AcquiredFileEditForm,
    AcquiredFileForm,
    InstrumentForm,
    ProjectForm,
    SpeciesForm,
    UserForm,
    VirusForm,
)
from models import (
    CellLine,
    CrosslinkingExperiment,
    Experiment,
    AcquiredFile,
    IdentificationExperiment,
    Instrument,
    Project,
    Species,
    User,
    Virus,
    db,
)
from sdrf_support.sdrf_columns import KNOWN_SDRF_HEADERS, column_group
from sdrf_support.sdrf_export import (
    ACQUISITION_METHODS,
    INSTRUMENT_MODELS,
    SDRF_ANNOTATION_TOOL,
    SDRF_TEMPLATE,
    SDRF_VERSION,
    normalize_instrument,
)

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)
CSRFProtect(app)


@event.listens_for(Engine, "connect")
def _configure_sqlite_connection(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        # Make concurrent writers wait for the lock rather than immediately
        # failing with "database is locked".
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()


# ---------------------------------------------------------------------------
# Composite-key encoding for SelectField values
# A bare code is no longer unique, so dropdown values that identify an
# experiment encode the full parent chain, joined by \x1f (a char that never
# appears in a code).
# ---------------------------------------------------------------------------
TOKEN_SEP = "\x1f"

# Project holding commonly-used template experiments, offered as copy sources when
# creating a new experiment (in addition to the current project's experiments).
FAV_PROJECT_CODE = "FAV"


def _exp_token(project_code, experiment_code):
    return f"{project_code}{TOKEN_SEP}{experiment_code}"


def _next_experiment_code(project_code):
    """Suggested code for a new experiment: 'E' + zero-padded (count + 1)."""
    n = Experiment.query.filter_by(project_code=project_code).count() + 1
    return f"E{n:02d}"


def _commit_unique(form, field_name, what):
    """Commit a pending insert, turning a duplicate-key clash into a form error.

    Codes are (part of) the primary key, so re-submitting an existing one — e.g.
    hitting Save again after navigating back — raises an IntegrityError that would
    otherwise surface as a 500. We catch the duplicate, roll back, and report it
    inline on the offending field. Returns True if committed, False if it was a
    duplicate (in which case the caller should re-render the form). Any other
    IntegrityError is genuinely unexpected and is re-raised.
    """
    try:
        db.session.commit()
        return True
    except IntegrityError as exc:
        db.session.rollback()
        if "UNIQUE constraint failed" not in str(getattr(exc, "orig", exc)):
            raise
        field = getattr(form, field_name, None)
        value = field.data if field is not None else ""
        message = f"{what} “{value}” already exists — please choose a different one."
        if field is not None:
            field.errors.append(message)
        flash(message, "error")
        return False


def _split_token(token):
    """Split a \x1f-joined token into its parts, or () for an empty value."""
    return tuple(token.split(TOKEN_SEP)) if token else ()


def _is_autosave():
    """True when the request is a background autosave POST (see autosave.js)."""
    return request.headers.get("X-Autosave") == "1"


def _autosave_json(form, saved):
    """JSON body for an autosave POST: success, or the form's per-field errors."""
    if saved:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "errors": form.errors})


@app.template_filter('wrap_code')
def wrap_code_filter(s, code, marker, only_first=False):
    if not code or not s:
        return s
    pattern = re.compile(re.escape(code), re.IGNORECASE)
    return pattern.sub(
        lambda m: f'\x01{marker}\x02{m.group(0)}\x03',
        s,
        count=1 if only_first else 0,
    )


@app.context_processor
def inject_nav_counts():
    # Runs on every page render, so fold the per-entity counts into a single
    # SELECT of scalar subqueries rather than one round trip per entity.
    nav_models = {
        "projects": Project,
        "experiments": Experiment,
        "species": Species,
        "cell_lines": CellLine,
        "viruses": Virus,
        "files": AcquiredFile,
        "instruments": Instrument,
        "users": User,
    }
    try:
        cols = [
            db.select(func.count()).select_from(model).scalar_subquery().label(key)
            for key, model in nav_models.items()
        ]
        row = db.session.execute(db.select(*cols)).one()
        return {"nav_counts": dict(row._mapping)}
    except Exception:
        return {"nav_counts": {}}


# ---------------------------------------------------------------------------
# CLI: init-db
# ---------------------------------------------------------------------------
@app.cli.command("init-db")
def init_db():
    """Create database tables from schema.sql."""
    db_path = app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "")
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path) as f:
        schema_sql = f.read()
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(schema_sql)
    conn.close()
    click.echo(f"Initialized database at {db_path}")


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return redirect(url_for("project_list"))


# ---------------------------------------------------------------------------
# SDRF metadata editor
# ---------------------------------------------------------------------------
SDRF_REQUIRED_HEADERS = (
    "source name",
    "characteristics[organism]",
    "characteristics[organism part]",
    "characteristics[biological replicate]",
    "assay name",
    "technology type",
    "comment[proteomics data acquisition method]",
    "comment[label]",
    "comment[instrument]",
    "comment[cleavage agent details]",
    "comment[fraction identifier]",
    "comment[technical replicate]",
    "comment[data file]",
)

SDRF_TECHNOLOGY_TYPE = "proteomic profiling by mass spectrometry"
SDRF_AUTOMATIC_COLUMNS = {
    "comment[sdrf annotation tool]": SDRF_ANNOTATION_TOOL,
    "comment[sdrf version]": SDRF_VERSION,
    "comment[sdrf template]": SDRF_TEMPLATE,
}


def _sdrf_text(value):
    """Return a safe, single-line SDRF cell value."""
    if value is None:
        return ""
    return str(value).replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()


def _sdrf_organism(species):
    if not species:
        return ""
    name = _sdrf_text(species.species_name)
    taxon = _sdrf_text(species.species_taxon)
    if taxon and taxon.casefold().startswith("ncbitaxon:"):
        return f"NT={name};AC={taxon}" if name else taxon
    if taxon:
        return f"NT={name};AC=NCBITaxon:{taxon}" if name else f"AC=NCBITaxon:{taxon}"
    return name


def _sdrf_file_row(acquired_file, instruments):
    """Draft one editable SDRF row from an acquired file and its experiment."""
    experiment = acquired_file.experiment
    filename = _sdrf_text(acquired_file.filename)
    if not filename and acquired_file.location:
        filename = Path(acquired_file.location).name
    stem = Path(filename).stem if filename else _sdrf_text(experiment.code if experiment else "")
    instrument = instruments.get(acquired_file.instrument_initial)
    instrument_name = normalize_instrument(_sdrf_text(
        (instrument.model or instrument.name) if instrument else acquired_file.instrument_initial
    ))

    if experiment is None:
        return {
            "file_id": acquired_file.id,
            "project_code": acquired_file.project_code,
            "experiment_code": acquired_file.experiment_code or "",
            "row": {
                "source name": stem,
                "characteristics[organism]": "",
                "characteristics[organism part]": "",
                "characteristics[biological replicate]": "",
                "assay name": stem,
                "technology type": SDRF_TECHNOLOGY_TYPE,
                "comment[proteomics data acquisition method]": "",
                "comment[label]": "",
                "comment[instrument]": instrument_name,
                "comment[cleavage agent details]": "",
                "comment[fraction identifier]": "1",
                "comment[technical replicate]": "1",
                "comment[data file]": filename,
            },
            "optional": {
                "comment[acquisition date]": (
                    acquired_file.file_date.isoformat() if acquired_file.file_date else ""
                ),
            },
        }

    species_values = [_sdrf_organism(species) for species in experiment.species_list]
    species_values = [value for value in species_values if value]
    cell_line_names = [_sdrf_text(cell.cell_line_name) for cell in experiment.cell_lines]
    cell_line_ids = [_sdrf_text(cell.cellosaurus_id) for cell in experiment.cell_lines]
    label = _sdrf_text(
        experiment.chemical_labelling or experiment.isotope_labeling_channel
    )
    row = {
        "source name": _sdrf_text(experiment.name or experiment.code),
        "characteristics[organism]": ";".join(species_values),
        "characteristics[organism part]": _sdrf_text(experiment.tissue),
        "characteristics[biological replicate]": _sdrf_text(experiment.replicate),
        "assay name": stem,
        "technology type": SDRF_TECHNOLOGY_TYPE,
        "comment[proteomics data acquisition method]": "",
        "comment[label]": label,
        "comment[instrument]": instrument_name,
        "comment[cleavage agent details]": _sdrf_text(experiment.digestion),
        "comment[fraction identifier]": "1",
        "comment[technical replicate]": "1",
        "comment[data file]": filename,
    }
    optional = {
        "characteristics[project name]": _sdrf_text(experiment.project.name),
        "characteristics[sample name]": _sdrf_text(experiment.name),
        "characteristics[disease]": _sdrf_text(experiment.disease),
        "characteristics[phenotype]": _sdrf_text(experiment.phenotype),
        "characteristics[age]": " ".join(filter(None, [
            _sdrf_text(experiment.organism_age), _sdrf_text(experiment.organism_age_unit)
        ])),
        "characteristics[sex]": _sdrf_text(experiment.organism_sex),
        "characteristics[enrichment process]": _sdrf_text(experiment.enrichment_process),
        "characteristics[synthetic peptide]": _sdrf_text(experiment.synthetic_peptide),
        "characteristics[cell line]": ";".join(filter(None, cell_line_names)),
        "characteristics[cellosaurus accession]": ";".join(filter(None, cell_line_ids)),
        "comment[acquisition date]": acquired_file.file_date.isoformat() if acquired_file.file_date else "",
        "comment[quenching reagent]": _sdrf_text(experiment.quenching_reagent),
    }
    return {
        "file_id": acquired_file.id,
        "project_code": acquired_file.project_code,
        "experiment_code": acquired_file.experiment_code,
        "row": row,
        "optional": optional,
    }


@app.route("/sdrf")
def sdrf_editor():
    projects = Project.query.order_by(Project.name, Project.code).all()
    optional_headers = [
        header for header in KNOWN_SDRF_HEADERS
        if header not in SDRF_REQUIRED_HEADERS
        and header not in SDRF_AUTOMATIC_COLUMNS
    ]
    optional_groups = defaultdict(list)
    for header in optional_headers:
        optional_groups[column_group(header)].append(header)
    return render_template(
        "sdrf/editor.html",
        projects=projects,
        required_headers=SDRF_REQUIRED_HEADERS,
        optional_groups=dict(optional_groups),
        initial_project=request.args.get("project", ""),
        technology_type=SDRF_TECHNOLOGY_TYPE,
        acquisition_terms=ACQUISITION_METHODS,
        instrument_terms=INSTRUMENT_MODELS,
        automatic_columns=SDRF_AUTOMATIC_COLUMNS,
    )


@app.route("/api/sdrf/project-files")
def api_sdrf_project_files():
    project_code = request.args.get("project", "").strip()
    if not project_code:
        abort(400, "project is required")
    if db.session.get(Project, project_code) is None:
        abort(404, "Project not found")
    files = (
        AcquiredFile.query.options(
            joinedload(AcquiredFile.experiment).joinedload(Experiment.project),
            joinedload(AcquiredFile.experiment).joinedload(Experiment.species_list),
            joinedload(AcquiredFile.experiment).joinedload(Experiment.cell_lines),
        )
        .filter(AcquiredFile.project_code == project_code)
        .order_by(
            AcquiredFile.experiment_code,
            AcquiredFile.file_date,
            AcquiredFile.filename,
        )
        .all()
    )
    experiments = (
        Experiment.query.filter_by(project_code=project_code)
        .order_by(Experiment.name, Experiment.code)
        .all()
    )
    file_counts = defaultdict(int)
    for acquired_file in files:
        file_counts[acquired_file.experiment_code] += 1
    instruments = {item.initial: item for item in Instrument.query.all()}
    return jsonify({
        "experiments": [
            {
                "code": experiment.code,
                "name": experiment.name,
                "file_count": file_counts[experiment.code],
            }
            for experiment in experiments
        ],
        "files": [_sdrf_file_row(acquired_file, instruments) for acquired_file in files],
    })


@app.route("/api/tree")
def api_tree():
    def file_nodes(experiment):
        nodes = [
            {
                "id": f.id,
                "name": f.filename or f.location or str(f.id),
                "level": "file",
                "total_bytes": int(f.size_bytes or 0),
                "url": url_for("file_detail", id=f.id),
            }
            for f in experiment.acquired_files
        ]
        nodes.sort(key=lambda n: n["total_bytes"], reverse=True)
        return nodes

    def experiment_node(e):
        children = file_nodes(e)
        total = sum(c["total_bytes"] for c in children)
        return {"id": e.code, "name": e.name, "level": "experiment", "total_bytes": total,
                "url": url_for("experiment_detail", project_code=e.project_code, code=e.code),
                "children": children}

    def project_node(p):
        children = sorted([experiment_node(e) for e in p.experiments], key=lambda n: n["total_bytes"], reverse=True)
        total = sum(c["total_bytes"] for c in children)
        return {"id": p.code, "name": p.name or p.code, "level": "project", "total_bytes": total,
                "url": url_for("project_detail", code=p.code), "children": children}

    projects = Project.query.filter_by(active=True).all()
    project_nodes = sorted([project_node(p) for p in projects], key=lambda n: n["total_bytes"], reverse=True)
    tree = {
        "name": "File Acquisition Database",
        "level": "root",
        "total_bytes": sum(n["total_bytes"] for n in project_nodes),
        "children": project_nodes,
    }
    return jsonify(tree)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
@app.route("/projects")
def project_list():
    show_archived = request.args.get("show_archived", "0") == "1"
    if show_archived:
        projects = Project.query.order_by(Project.name).all()
    else:
        projects = Project.query.filter_by(active=True).order_by(Project.name).all()
    users = {u.initials: u.name for u in User.query.all()}
    # One grouped aggregate per entity instead of a query per project. Files
    # carry project_code on their own composite key, so they don't need to join
    # through Experiment. Projects with zero rows are simply absent from the
    # result (the template defaults missing codes to 0).
    exp_counts = dict(
        db.session.query(Experiment.project_code, func.count())
        .group_by(Experiment.project_code).all()
    )
    file_counts = dict(
        db.session.query(AcquiredFile.project_code, func.count())
        .group_by(AcquiredFile.project_code).all()
    )
    return render_template(
        "project/list.html", projects=projects, show_archived=show_archived,
        users=users, exp_counts=exp_counts, file_counts=file_counts
    )


@app.route("/projects/<code>/toggle-active", methods=["POST"])
def project_toggle_active(code):
    project = db.get_or_404(Project, code)
    project.active = not project.active
    db.session.commit()
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return {"active": project.active}
    show_archived = request.args.get("show_archived", "0")
    return redirect(url_for("project_list", show_archived=show_archived))


@app.route("/projects/<code>")
def project_detail(code):
    project = db.get_or_404(Project, code)
    experiments = (
        Experiment.query.filter_by(project_code=code).order_by(Experiment.name).all()
    )
    contact_user = (
        User.query.filter_by(initials=project.user_initials).first()
        if project.user_initials
        else None
    )
    contact_name = contact_user.name if contact_user else project.user_initials
    files = (
        AcquiredFile.query.join(Experiment)
        .options(joinedload(AcquiredFile.experiment).joinedload(Experiment.project))
        .filter(Experiment.project_code == code)
        .order_by(AcquiredFile.file_date.desc(), AcquiredFile.filename).all()
    )
    total_size_gb = sum(f.size_bytes or 0 for f in files) / 1e9
    chart_rows = (
        db.session.query(
            AcquiredFile.file_date,
            Experiment.code.label("experiment_code"),
            func.sum(AcquiredFile.size_bytes).label("total_bytes"),
        )
        .join(AcquiredFile.experiment)
        .filter(Experiment.project_code == code)
        .filter(AcquiredFile.file_date.isnot(None))
        .group_by(AcquiredFile.file_date, Experiment.code)
        .order_by(AcquiredFile.file_date)
        .all()
    )
    chart_data = [
        {"date": row.file_date.isoformat(), "experiment": row.experiment_code, "gb": round((row.total_bytes or 0) / 1e9, 4)}
        for row in chart_rows
    ]
    users = {u.initials: u.name for u in User.query.all()}
    return render_template(
        "project/detail.html", project=project, experiments=experiments,
        contact_name=contact_name, files=files,
        experiment_count=len(experiments),
        file_count=len(files), total_size_gb=total_size_gb, users=users,
        chart_data=chart_data,
    )


@app.route("/projects/new", methods=["GET", "POST"])
def project_create():
    form = ProjectForm()
    form.user_initials.choices = _user_initials_choices()
    if form.validate_on_submit():
        project = Project()
        form.populate_obj(project)
        db.session.add(project)
        if _commit_unique(form, "code", "A project with code"):
            flash("Project created.", "success")
            return redirect(url_for("project_detail", code=project.code))
    return render_template("project/form.html", form=form, project=None)


@app.route("/projects/<code>/edit", methods=["GET", "POST"])
def project_edit(code):
    project = db.get_or_404(Project, code)
    form = ProjectForm(obj=project)
    del form.code
    form.user_initials.choices = _user_initials_choices()
    if form.validate_on_submit():
        form.populate_obj(project)
        db.session.commit()
        if _is_autosave():
            return _autosave_json(form, True)
        flash("Project updated.", "success")
        return redirect(url_for("project_detail", code=project.code))
    if request.method == "POST" and _is_autosave():
        return _autosave_json(form, False)
    if request.method == "POST":
        flash("Could not save — please check the fields below.", "error")
    return render_template("project/form.html", form=form, project=project)


def _user_initials_choices():
    users = User.query.filter_by(active=True).order_by(User.name).all()
    choices = [("", "— none —")]
    choices += [(u.initials, u.name) for u in users if u.initials]
    return choices


def _user_name_choices():
    users = User.query.filter_by(active=True).order_by(User.name).all()
    choices = [("", "— none —")]
    choices += [(u.name, u.name) for u in users]
    return choices


# ---------------------------------------------------------------------------
# Experiments
# ---------------------------------------------------------------------------
@app.route("/experiments")
def experiment_list():
    show_archived = request.args.get("show_archived", "0") == "1"
    q = Experiment.query.join(Project).options(joinedload(Experiment.project))
    if not show_archived:
        q = q.filter(Project.active == True, Experiment.active == True)  # noqa: E712
    experiments = q.order_by(Experiment.name).all()
    users = {u.initials: u.name for u in User.query.all()}
    return render_template("experiment/list.html", experiments=experiments, users=users, show_archived=show_archived)


@app.route("/projects/<project_code>/experiments/<code>", methods=["GET", "POST"])
def experiment_detail(project_code, code):
    # The experiment's canonical page IS the editable form: you land here to view
    # it, edit it, and generate a queue, all in one place (there's no separate
    # read-only detail page).
    experiment = db.get_or_404(Experiment, (project_code, code))
    form = ExperimentForm(obj=experiment)
    del form.code
    # project_code is part of the primary key — re-parenting is not supported.
    del form.project_code
    form.user_initials.choices = _user_initials_choices()
    form.species_ids.choices = _species_multi_choices()
    form.cellosaurus_ids.choices = _cell_line_multi_choices()
    form.gen_instrument.choices = _instrument_choices()
    form.gen_user.choices = _user_initials_choices()
    if request.method == "GET":
        form.crosslinking_experiment.data = bool(experiment.crosslinking_experiment == 1)
        form.quantitation.data = bool(experiment.quantitation)
        form.species_ids.data = [s.id for s in experiment.species_list]
        form.cellosaurus_ids.data = [cl.cellosaurus_id for cl in experiment.cell_lines]
        form.varying_conditions.data = json.dumps(experiment.varying_conditions or [])
        if experiment.user_initials:
            form.gen_user.data = experiment.user_initials
        form.gen_date.data = date.today().isoformat()
    if form.validate_on_submit():
        # Type (crosslinking vs identification) is editable here. Populate + nullify
        # for the DESIRED type, but keep the in-session instance's discriminator at
        # its current value for the flush (flushing a row whose column no longer
        # matches its mapped subclass warns and may not refresh cleanly). The actual
        # switch is a direct column update below; the row then reloads as the
        # correct subclass on the next request.
        desired = 1 if form.crosslinking_experiment.data else 0
        current = experiment.crosslinking_experiment
        _apply_experiment_conditions(form, experiment, desired)
        if desired != current:
            experiment.crosslinking_experiment = current
        db.session.commit()
        if desired != current:
            db.session.query(Experiment).filter_by(
                project_code=experiment.project_code, code=experiment.code
            ).update({"crosslinking_experiment": desired}, synchronize_session=False)
            db.session.commit()
            db.session.expire(experiment)
        resp = _generate_csv_or_none(form, experiment)
        if resp is not None:
            return resp
        if _is_autosave():
            return _autosave_json(form, True)
        flash("Experiment updated.", "success")
        return redirect(url_for("experiment_detail", project_code=experiment.project_code, code=experiment.code))
    if request.method == "POST" and _is_autosave():
        return _autosave_json(form, False)
    if request.method == "POST":
        flash("Could not save — please check the fields below.", "error")
    return render_template("experiment/form.html", form=form, experiment=experiment)


@app.route("/projects/<project_code>/experiments/<code>/delete", methods=["POST"])
def experiment_delete(project_code, code):
    experiment = db.get_or_404(Experiment, (project_code, code))
    if experiment.acquired_files:
        flash("Cannot delete an experiment that has acquired files.", "error")
        return redirect(url_for("experiment_detail", project_code=project_code, code=code))
    db.session.delete(experiment)
    db.session.commit()
    flash("Experiment deleted.", "success")
    return redirect(url_for("project_detail", code=project_code))


def _apply_experiment_conditions(form, experiment, discriminator):
    """Map the submitted ExperimentForm's condition fields onto the experiment.

    ``discriminator`` is the crosslinking_experiment value to persist (1/0/-1); it
    is set explicitly because ``populate_obj`` would otherwise clobber the
    discriminator column with the form's boolean. The caller decides the value —
    the edit page passes the toggle's desired type (so type is editable), while
    creation pins it to the new row's type. SQLAlchemy doesn't re-class the
    in-session instance, but the changed column value is persisted and the row
    reloads as the correct subclass on the next request.
    """
    form.populate_obj(experiment)
    experiment.crosslinking_experiment = discriminator
    experiment.quantitation = 1 if form.quantitation.data else 0
    # populate_obj set varying_conditions to the raw hidden-field JSON string;
    # replace it with the parsed list (the actual JSON column value).
    experiment.varying_conditions = _load_varying_conditions(form.varying_conditions.data)
    _coerce_select_fields(experiment)
    _nullify_crosslink_fields(experiment)
    with db.session.no_autoflush:
        experiment.species_list = Species.query.filter(
            Species.id.in_(form.species_ids.data)
        ).all()
        experiment.cell_lines = CellLine.query.filter(
            CellLine.cellosaurus_id.in_(form.cellosaurus_ids.data)
        ).all()


def _experiment_copy_choices(project_code):
    """Grouped options for the 'base new experiment on an existing one' dropdown:
    the current project's experiments, then the shared FAV templates (always
    included, regardless of the FAV project's active flag).
    """
    def _options(experiments):
        return [
            (_exp_token(e.project_code, e.code), f"{e.code} — {e.name}")
            for e in experiments
        ]

    current = (
        Experiment.query.filter_by(project_code=project_code)
        .order_by(Experiment.code).all()
    )
    groups = [("Current project", _options(current))]

    # FAV templates: always available. Skip if the current project IS FAV (its
    # experiments are already listed above).
    if project_code != FAV_PROJECT_CODE:
        fav = (
            Experiment.query.filter(Experiment.project_code == FAV_PROJECT_CODE)
            .order_by(Experiment.code).all()
        )
        if fav:
            groups.append(("FAV templates", _options(fav)))
    return groups


def _clone_experiment(source, project_code, new_code):
    """Build a new experiment that copies everything from ``source`` except its
    identity. Type is preserved; ``name`` is left blank (it must be re-entered).
    Runs server-side (not via a rendered form) so the copy survives the minimal
    create page, which doesn't render the condition fields.
    """
    experiment = (
        CrosslinkingExperiment() if source.crosslinking_experiment == 1
        else IdentificationExperiment()
    )
    skip = {"project_code", "code", "name", "crosslinking_experiment"}
    for attr in sa_inspect(Experiment).mapper.column_attrs:
        if attr.key in skip:
            continue
        setattr(experiment, attr.key, getattr(source, attr.key))
    experiment.project_code = project_code
    experiment.code = new_code
    experiment.name = ""
    experiment.varying_conditions = list(source.varying_conditions or [])
    # Add to the session before assigning M2M relationships so the association
    # rows are recorded (assigning them on a transient object is a no-op).
    db.session.add(experiment)
    experiment.species_list = list(source.species_list)
    experiment.cell_lines = list(source.cell_lines)
    return experiment


@app.route("/projects/<project_code>/experiments/new", methods=["GET", "POST"])
def experiment_create(project_code):
    # The parent project is fixed by the URL (you reach this page from a project),
    # so there's no project selector — it's shown read-only on the form. The create
    # page asks only for a code; everything else is filled in on the autosaving edit
    # page afterwards. Optionally the new row is cloned from an existing experiment
    # (the "base on existing" <select name="copy_from">), applied server-side.
    project = db.get_or_404(Project, project_code)
    form = ExperimentCreateForm()

    if request.method == "GET":
        form.code.data = _next_experiment_code(project_code)

    if form.validate_on_submit():
        copy_source = None
        copy_from = request.form.get("copy_from")
        if copy_from:
            parts = _split_token(copy_from)
            if len(parts) == 2:
                copy_source = db.session.get(Experiment, tuple(parts))

        if copy_source:
            experiment = _clone_experiment(copy_source, project_code, form.code.data)
        else:
            # A bare new experiment: only the code is entered here; the rest is
            # filled in on the edit page. name/description/user_initials are
            # NOT NULL, so default them to blank (shown as the code until named).
            experiment = IdentificationExperiment()
            experiment.project_code = project_code
            experiment.code = form.code.data
            experiment.name = ""
            experiment.description = ""
            experiment.user_initials = ""
        db.session.add(experiment)
        if _commit_unique(form, "code", "An experiment with code"):
            flash("Experiment created.", "success")
            return redirect(url_for("experiment_detail", project_code=experiment.project_code, code=experiment.code))

    cancel_url = url_for("project_detail", code=project_code)
    return render_template(
        "experiment/create.html", form=form, project=project, cancel_url=cancel_url,
        copy_experiments=_experiment_copy_choices(project_code),
    )


@app.route("/projects/<project_code>/experiments/<code>/edit")
def experiment_edit(project_code, code):
    # The edit form was merged into experiment_detail (the canonical page). Keep
    # this route as a redirect so old /edit bookmarks still resolve.
    return redirect(url_for("experiment_detail", project_code=project_code, code=code))


# ---------------------------------------------------------------------------
# Species
# ---------------------------------------------------------------------------
@app.route("/species")
def species_list():
    species_list = Species.query.order_by(Species.species_name).all()
    return render_template("species/list.html", species_list=species_list)


@app.route("/species/new", methods=["GET", "POST"])
def species_create():
    form = SpeciesForm()
    if form.validate_on_submit():
        species = Species()
        form.populate_obj(species)
        db.session.add(species)
        db.session.commit()
        flash("Species created.", "success")
        return redirect(url_for("species_detail", id=species.id))
    return render_template("species/form.html", form=form, species=None)


@app.route("/species/<int:id>")
def species_detail(id):
    species = db.get_or_404(Species, id)
    return render_template("species/detail.html", species=species)


@app.route("/species/<int:id>/edit", methods=["GET", "POST"])
def species_edit(id):
    species = db.get_or_404(Species, id)
    form = SpeciesForm(obj=species)
    if form.validate_on_submit():
        form.populate_obj(species)
        db.session.commit()
        if _is_autosave():
            return _autosave_json(form, True)
        flash("Species updated.", "success")
        return redirect(url_for("species_detail", id=species.id))
    if request.method == "POST" and _is_autosave():
        return _autosave_json(form, False)
    return render_template("species/form.html", form=form, species=species)


# ---------------------------------------------------------------------------
# Cell Lines
# ---------------------------------------------------------------------------
def _species_choices():
    species = Species.query.order_by(Species.species_name).all()
    return [(0, "")] + [(s.id, s.species_name) for s in species]


def _virus_multi_choices():
    return [(v.id, v.name) for v in Virus.query.order_by(Virus.name).all()]


@app.route("/cell-lines")
def cell_line_list():
    cell_lines = CellLine.query.order_by(CellLine.cell_line_name).all()
    return render_template("cell_line/list.html", cell_lines=cell_lines)


@app.route("/cell-lines/new", methods=["GET", "POST"])
def cell_line_create():
    form = CellLineForm()
    form.species_id.choices = _species_choices()
    form.virus_ids.choices = _virus_multi_choices()
    if form.validate_on_submit():
        cl = CellLine()
        form.populate_obj(cl)
        db.session.add(cl)
        # Suppress autoflush so a duplicate Cellosaurus ID surfaces at commit
        # (handled by _commit_unique) rather than from this query's autoflush.
        with db.session.no_autoflush:
            cl.viruses = Virus.query.filter(Virus.id.in_(form.virus_ids.data)).all()
        if _commit_unique(form, "cellosaurus_id", "A cell line with Cellosaurus ID"):
            flash("Cell line created.", "success")
            return redirect(url_for("cell_line_detail", cellosaurus_id=cl.cellosaurus_id))
    return render_template("cell_line/form.html", form=form, cell_line=None)


@app.route("/cell-lines/<cellosaurus_id>")
def cell_line_detail(cellosaurus_id):
    cl = db.get_or_404(CellLine, cellosaurus_id)
    return render_template("cell_line/detail.html", cell_line=cl)


@app.route("/cell-lines/<cellosaurus_id>/edit", methods=["GET", "POST"])
def cell_line_edit(cellosaurus_id):
    cl = db.get_or_404(CellLine, cellosaurus_id)
    form = CellLineForm(obj=cl)
    del form.cellosaurus_id  # primary key — not editable after creation
    form.species_id.choices = _species_choices()
    form.virus_ids.choices = _virus_multi_choices()
    if request.method == "GET":
        form.virus_ids.data = [v.id for v in cl.viruses]
    if form.validate_on_submit():
        form.populate_obj(cl)
        cl.viruses = Virus.query.filter(Virus.id.in_(form.virus_ids.data)).all()
        db.session.commit()
        if _is_autosave():
            return _autosave_json(form, True)
        flash("Cell line updated.", "success")
        return redirect(url_for("cell_line_detail", cellosaurus_id=cl.cellosaurus_id))
    if request.method == "POST" and _is_autosave():
        return _autosave_json(form, False)
    return render_template("cell_line/form.html", form=form, cell_line=cl)


# ---------------------------------------------------------------------------
# Viruses
# ---------------------------------------------------------------------------
@app.route("/viruses")
def virus_list():
    viruses = Virus.query.order_by(Virus.name).all()
    return render_template("virus/list.html", viruses=viruses)


@app.route("/viruses/new", methods=["GET", "POST"])
def virus_create():
    form = VirusForm()
    form.species_id.choices = _species_choices()
    if form.validate_on_submit():
        virus = Virus()
        form.populate_obj(virus)
        if virus.species_id == 0:
            virus.species_id = None
        db.session.add(virus)
        db.session.commit()
        flash("Virus created.", "success")
        return redirect(url_for("virus_detail", id=virus.id))
    return render_template("virus/form.html", form=form, virus=None)


@app.route("/viruses/<int:id>")
def virus_detail(id):
    virus = db.get_or_404(Virus, id)
    return render_template("virus/detail.html", virus=virus)


@app.route("/viruses/<int:id>/edit", methods=["GET", "POST"])
def virus_edit(id):
    virus = db.get_or_404(Virus, id)
    form = VirusForm(obj=virus)
    form.species_id.choices = _species_choices()
    if request.method == "GET" and not virus.species_id:
        form.species_id.data = 0
    if form.validate_on_submit():
        form.populate_obj(virus)
        if virus.species_id == 0:
            virus.species_id = None
        db.session.commit()
        if _is_autosave():
            return _autosave_json(form, True)
        flash("Virus updated.", "success")
        return redirect(url_for("virus_detail", id=virus.id))
    if request.method == "POST" and _is_autosave():
        return _autosave_json(form, False)
    return render_template("virus/form.html", form=form, virus=virus)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
@app.route("/users")
def user_list():
    users = User.query.order_by(User.name).all()
    return render_template("user/list.html", users=users)


@app.route("/users/new", methods=["GET", "POST"])
def user_create():
    form = UserForm()
    if form.validate_on_submit():
        user = User()
        form.populate_obj(user)
        db.session.add(user)
        if _commit_unique(form, "initials", "A user with initials"):
            flash("User created.", "success")
            return redirect(url_for("user_detail", initials=user.initials))
    return render_template("user/form.html", form=form, user=None)


@app.route("/users/<initials>")
def user_detail(initials):
    user = db.get_or_404(User, initials)
    return render_template("user/detail.html", user=user)


@app.route("/users/<initials>/edit", methods=["GET", "POST"])
def user_edit(initials):
    user = db.get_or_404(User, initials)
    form = UserForm(obj=user)
    del form.initials  # primary key — not editable after creation
    if form.validate_on_submit():
        form.populate_obj(user)
        db.session.commit()
        if _is_autosave():
            return _autosave_json(form, True)
        flash("User updated.", "success")
        return redirect(url_for("user_detail", initials=user.initials))
    if request.method == "POST" and _is_autosave():
        return _autosave_json(form, False)
    return render_template("user/form.html", form=form, user=user)


# ---------------------------------------------------------------------------
# Instruments
# ---------------------------------------------------------------------------
@app.route("/instruments")
def instrument_list():
    instruments = Instrument.query.order_by(Instrument.name).all()
    return render_template("instrument/list.html", instruments=instruments)


@app.route("/instruments/new", methods=["GET", "POST"])
def instrument_create():
    form = InstrumentForm()
    if form.validate_on_submit():
        instrument = Instrument()
        form.populate_obj(instrument)
        db.session.add(instrument)
        if _commit_unique(form, "initial", "An instrument with initial"):
            flash("Instrument created.", "success")
            return redirect(url_for("instrument_detail", initial=instrument.initial))
    return render_template("instrument/form.html", form=form, instrument=None)


@app.route("/instruments/<initial>")
def instrument_detail(initial):
    instrument = db.get_or_404(Instrument, initial)
    return render_template("instrument/detail.html", instrument=instrument)


@app.route("/instruments/<initial>/edit", methods=["GET", "POST"])
def instrument_edit(initial):
    instrument = db.get_or_404(Instrument, initial)
    form = InstrumentForm(obj=instrument)
    del form.initial  # primary key — not editable after creation
    if form.validate_on_submit():
        form.populate_obj(instrument)
        db.session.commit()
        if _is_autosave():
            return _autosave_json(form, True)
        flash("Instrument updated.", "success")
        return redirect(url_for("instrument_detail", initial=instrument.initial))
    if request.method == "POST" and _is_autosave():
        return _autosave_json(form, False)
    return render_template("instrument/form.html", form=form, instrument=instrument)


# ---------------------------------------------------------------------------
# Experiment condition helpers (shared by experiment create/edit)
# ---------------------------------------------------------------------------
def _species_multi_choices():
    return [(s.id, s.species_name) for s in Species.query.order_by(Species.species_name).all()]


def _cell_line_multi_choices():
    return [
        (cl.cellosaurus_id, cl.cell_line_name)
        for cl in CellLine.query.order_by(CellLine.cell_line_name).all()
    ]


def _coerce_select_fields(experiment):
    """Convert empty-string values from optional SelectFields to None so they
    satisfy the DB CHECK constraints (which allow NULL but not empty string)."""
    for field in ("synthetic_peptide", "quantitation_method", "crosslinking_type"):
        if getattr(experiment, field) == "":
            setattr(experiment, field, None)


def _nullify_crosslink_fields(experiment):
    """Clear crosslink fields for a non-crosslinking experiment."""
    crosslink_fields = [
        "crosslinker", "crosslinking_type",
        "protein_or_cell_concentration", "protein_or_cell_concentration_unit",
        "crosslinker_or_compound_concentration", "crosslinker_or_compound_concentration_unit",
        "organic_solvent_concentration", "organic_solvent_concentration_unit",
        "reaction_temperature_in_celsius", "reaction_time_in_minutes",
        "quenching_reagent", "uv_source", "uv_time_in_seconds",
        "uv_wavelength_in_nanometers",
    ]

    if experiment.crosslinking_experiment != 1:
        for f in crosslink_fields:
            setattr(experiment, f, None)


# ---------------------------------------------------------------------------
# Variable conditions → combinatorial queue generation
#
# Each variable-condition row is {description, prefix, values, postfix}. The
# generated filename for a combination is
#   {inst}_{YYYYMMDD}-{NNN}_{proj}_{user}_{exp}_{seg1}_{seg2}_...
# where seg = prefix + value + postfix, over the Cartesian product of every
# row's parsed value list. The JS twin of _parse_condition_values lives in
# static/js/variable-conditions.js and must stay in lockstep so the live
# preview matches the CSV this produces.
# ---------------------------------------------------------------------------
_RANGE_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*(?:@\s*(\d+(?:\.\d+)?)\s*)?$"
)
_MAX_RUNS = 10000
# Xcalibur/Windows choke on very long run filenames, so refuse to generate a
# queue containing any. The JS twin (MAX_FILENAME_LEN in variable-conditions.js)
# flags the same limit in the live preview.
_MAX_FILENAME_LEN = 180


def _fmt_num(x):
    """Format a range value: integers without a decimal point, decimals with
    trailing zeros trimmed (so 4.0 -> '4', 0.50 -> '0.5')."""
    if x == int(x):
        return str(int(x))
    return f"{x:g}"


def _parse_condition_values(s):
    """Parse a Values cell into a list of strings.

    A range "x - y @ z" (step optional, default 1) expands to individual items;
    anything else is a comma-separated list. Mirrors parseValues() in the JS.
    """
    if not s:
        return []
    m = _RANGE_RE.match(s)
    if m:
        start, end = float(m.group(1)), float(m.group(2))
        step = float(m.group(3)) if m.group(3) else 1.0
        if step <= 0:
            return [s.strip()]
        out = []
        v = start
        # guard against runaway ranges
        while v <= end + 1e-9 and len(out) <= _MAX_RUNS:
            out.append(_fmt_num(round(v, 10)))
            v += step
        return out
    return [part.strip() for part in s.split(",") if part.strip()]


def _instrument_choices():
    instruments = Instrument.query.order_by(Instrument.name).all()
    return [("", "— none —")] + [(i.initial, f"{i.initial} — {i.name}") for i in instruments]


# 96-well plate assignment (ported from the sequencer.py prototype). The JS twins
# in variable-conditions.js mirror these so the preview matches the CSV.
_WELL_ROWS = list("ABCDEFGH")
_WELL_COLS = list(range(1, 13))


def _make_well_order(mode):
    """rowwise: A1..A12,B1..H12  ·  columnwise: A1,B1..H1,A2..H12."""
    wells = []
    if mode == "columnwise":
        for col in _WELL_COLS:
            for row in _WELL_ROWS:
                wells.append(f"{row}{col}")
    else:  # rowwise (default)
        for row in _WELL_ROWS:
            for col in _WELL_COLS:
                wells.append(f"{row}{col}")
    return wells


def _validate_well(well):
    """Normalise a well like 'a1'/'H12' to 'A1'/'H12', or None if invalid."""
    well = (well or "").strip().upper()
    if len(well) < 2:
        return None
    row, col_text = well[0], well[1:]
    if row not in _WELL_ROWS or not col_text.isdigit():
        return None
    if int(col_text) not in _WELL_COLS:
        return None
    return f"{row}{int(col_text)}"


def _generate_runs(conditions, inst, ymd, proj, user, exp, run_start, well=None):
    """Build the combinatorial run list.

    Returns (rows, error). rows is a list of {run_number, filename, values,
    position}; the Cartesian product is taken in row order (last row varies
    fastest, matching sequencer.py). ``well`` is the optional plate config
    {enable, order, start, in_filename, in_position}: runs are placed into
    96-well positions from ``start`` in ``order``, wrapping onto a new plate every
    96; the plate number is only added when the run set spans more than one plate.
    error is a message string if generation can't proceed.
    """
    value_lists = [_parse_condition_values(c.get("values", "")) for c in conditions]
    value_lists = [vl for vl in value_lists if vl]
    # Keep the prefixes/postfixes aligned with the non-empty value lists. With no
    # variable conditions at all, itertools.product() yields a single empty
    # combination, so the queue gets one entry whose filename is just the base
    # (ending in the experiment code).
    active = [c for c in conditions if _parse_condition_values(c.get("values", ""))]
    total = 1
    for vl in value_lists:
        total *= len(vl)
    if total > _MAX_RUNS:
        return [], f"That would generate {total} runs (limit {_MAX_RUNS}). Narrow the conditions."

    wells = start_index = None
    include_plate = False
    if well and well.get("enable"):
        wells = _make_well_order(well.get("order"))
        start_well = _validate_well(well.get("start"))
        if start_well is None:
            return [], "Invalid well start position — use e.g. A1, B3, H12."
        start_index = wells.index(start_well)
        include_plate = ((start_index + total - 1) // 96 + 1) > 1

    rows = []
    for i, combo in enumerate(itertools.product(*value_lists)):
        nnn = run_start + i
        base = f"{inst}_{ymd}-{nnn:03d}_{proj}_{user}_{exp}"
        segments = [
            f"{active[j].get('prefix', '')}{val}{active[j].get('postfix', '')}"
            for j, val in enumerate(combo)
        ]
        position = ""
        if wells is not None:
            abs_index = start_index + i
            plate = abs_index // 96 + 1
            wname = wells[abs_index % 96]
            if well.get("in_filename"):
                if include_plate:
                    segments.append(f"Plate{plate}")
                segments.append(wname)
            if well.get("in_position"):
                position = f"{plate}:{wname}" if include_plate else wname
        rows.append({
            "run_number": nnn,
            "filename": "_".join([base] + segments),
            "values": list(combo),
            "position": position,
        })
    too_long = [r for r in rows if len(r["filename"]) > _MAX_FILENAME_LEN]
    if too_long:
        longest = max(too_long, key=lambda r: len(r["filename"]))
        return [], (
            f"{len(too_long)} generated filename(s) exceed {_MAX_FILENAME_LEN} "
            f"characters (longest is {len(longest['filename'])}: "
            f"\"{longest['filename']}\"). Shorten the codes, prefixes, or "
            "postfixes and try again."
        )
    return rows, None


def _build_queue_csv(rows, ymd):
    """Thermo Xcalibur sequence CSV for the generated run list.

    Layout is the Xcalibur sequence format: a leading ``Bracket Type=4`` line,
    the column header row, then one row per run. Path is the instrument's data
    folder for the day; the Comment column is left empty.

    Encoded UTF-8 with a BOM (``utf-8-sig``) so spreadsheet apps (Excel /
    LibreOffice) reliably detect the encoding instead of guessing a double-byte
    codepage and mangling the text. If the Xcalibur importer rejects the BOM,
    switch this back to ``.encode("cp1252", errors="replace")``.
    """
    path = f"D:\\Data\\{ymd[0:4]}\\{ymd[2:4]}{ymd[4:6]}\\{ymd[2:8]}"
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["Bracket Type=4", "", "", "", "", ""])
    writer.writerow(["File Name", "Path", "Instrument Method", "Position", "Inj Vol", "Comment"])
    for row in rows:
        writer.writerow([row["filename"], path, "", row.get("position", ""), "", ""])
    return buf.getvalue().encode("utf-8-sig")


def _load_varying_conditions(raw):
    """Parse the hidden field JSON into a clean list of condition dicts."""
    try:
        data = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    cleaned = []
    for item in data:
        if not isinstance(item, dict):
            continue
        cleaned.append({
            "description": str(item.get("description", "")),
            "prefix": str(item.get("prefix", "")),
            "values": str(item.get("values", "")),
            "postfix": str(item.get("postfix", "")),
        })
    return cleaned


def _generate_csv_or_none(form, experiment):
    """If this submit was 'Save and Generate Queue', return the run-list CSV as a
    download Response; otherwise None. Assumes the experiment (including its
    varying_conditions) has already been saved. On a generation error the message
    is flashed and None is returned, so the caller falls back to its normal
    save-and-redirect path.
    """
    if request.form.get("action") != "generate":
        return None
    inst = (form.gen_instrument.data or "").strip()
    user = (form.gen_user.data or "").strip()
    ymd = (form.gen_date.data or "").strip().replace("-", "")
    try:
        run_start = int(form.gen_run_start.data)
    except (TypeError, ValueError):
        run_start = 1
    if not (inst and user and len(ymd) == 8 and ymd.isdigit()):
        flash("Instrument, operator, and a valid date are required to generate the queue.", "error")
        return None
    well = {
        "enable": bool(form.well_enable.data),
        "order": form.well_order.data,
        "start": form.well_start.data,
        "in_filename": bool(form.well_in_filename.data),
        "in_position": bool(form.well_in_position.data),
    }
    conditions = experiment.varying_conditions or []
    rows, error = _generate_runs(
        conditions, inst, ymd, experiment.project_code, user, experiment.code,
        run_start, well=well,
    )
    if error:
        flash(error, "error")
        return None
    csv_bytes = _build_queue_csv(rows, ymd)
    resp = Response(csv_bytes, mimetype="text/csv")
    resp.headers["Content-Disposition"] = (
        f'attachment; filename="{inst}_{ymd}_{experiment.code}_sequence.csv"'
    )
    return resp


# ---------------------------------------------------------------------------
# Files (DB records)
# ---------------------------------------------------------------------------
@app.route("/files")
def file_list():
    files = (
        AcquiredFile.query.options(
            joinedload(AcquiredFile.experiment).joinedload(Experiment.project)
        )
        .order_by(AcquiredFile.file_date.desc(), AcquiredFile.filename)
        .all()
    )
    users = {u.initials: u.name for u in User.query.all()}
    return render_template("file/list.html", files=files, users=users)


@app.route("/projects/<project_code>/experiments/<code>/db-files/new", methods=["GET", "POST"])
def file_create(project_code, code):
    experiment = db.get_or_404(Experiment, (project_code, code))
    form = AcquiredFileForm()
    if form.validate_on_submit():
        f = AcquiredFile(project_code=project_code, experiment_code=code)
        form.populate_obj(f)
        f.meta = form.meta_json.data
        if f.size_bytes is not None:
            f.size_bytes = int(f.size_bytes * 1e9)
        db.session.add(f)
        db.session.commit()
        flash("File record created.", "success")
        return redirect(url_for("file_detail", id=f.id))
    return render_template("file/form.html", form=form, file=None, experiment=experiment)


@app.route("/files/<int:id>")
def file_detail(id):
    f = db.get_or_404(AcquiredFile, id)
    return render_template("file/detail.html", file=f)


def _file_edit_tree():
    experiments = Experiment.query.order_by(Experiment.name).all()
    return {
        # "value" is the encoded token used as the <option> value; "project_code"
        # keys the client-side cascade filter.
        "experiments": [
            {"value": _exp_token(e.project_code, e.code), "project_code": e.project_code,
             "label": f"{e.code} — {e.name}"}
            for e in experiments
        ],
    }


@app.route("/files/<int:id>/edit", methods=["GET", "POST"])
def file_edit(id):
    f = db.get_or_404(AcquiredFile, id)
    form = AcquiredFileEditForm()
    form.project_code.choices = [("", "—")] + [
        (p.code, f"{p.code} — {p.name}")
        for p in Project.query.order_by(Project.name).all()
    ]
    # experiment option list is populated by JS from the embedded tree; the
    # server-side choices only need to contain the currently-selected value so
    # WTForms validates the POST.
    form.experiment_code.choices = [("", "—")]
    if request.method == "GET" and f.experiment is not None:
        e = f.experiment
        exp_token = _exp_token(e.project_code, e.code)
        form.experiment_code.choices.append((exp_token, f"{e.code} — {e.name}"))
        form.project_code.data = e.project_code
        form.experiment_code.data = exp_token
    if form.validate_on_submit():
        # The server only needs the experiment token; the project select is pure
        # UX scaffolding handled client-side. Decode the posted token into the
        # composite key and let the FK constraint reject anything bogus.
        parts = _split_token(request.form.get("experiment_code") or "")
        if len(parts) == 2:
            f.project_code, f.experiment_code = parts
        else:
            f.project_code = f.experiment_code = None
        db.session.commit()
        if _is_autosave():
            return _autosave_json(form, True)
        flash("File association updated.", "success")
        return redirect(url_for("file_detail", id=f.id))
    if request.method == "POST" and _is_autosave():
        return _autosave_json(form, False)
    return render_template("file/edit.html", form=form, file=f, tree=_file_edit_tree())


@app.route("/instrument-usage")
def instrument_usage():
    rows = (
        db.session.query(
            AcquiredFile.instrument_initial,
            AcquiredFile.file_date,
            Project.code.label("project_code"),
            func.sum(AcquiredFile.size_bytes).label("total_bytes"),
        )
        .join(AcquiredFile.experiment)
        .join(Experiment.project)
        .filter(AcquiredFile.instrument_initial.isnot(None))
        .filter(AcquiredFile.file_date.isnot(None))
        .group_by(AcquiredFile.instrument_initial, AcquiredFile.file_date, Project.code)
        .order_by(AcquiredFile.instrument_initial, AcquiredFile.file_date)
        .all()
    )
    instruments = defaultdict(list)
    for row in rows:
        instruments[row.instrument_initial].append({
            "date": row.file_date.isoformat(),
            "project": row.project_code,
            "gb": round((row.total_bytes or 0) / 1e9, 4),
        })
    return render_template("instrument_usage.html", data=dict(instruments))


@app.route("/disk-usage")
def disk_usage():
    rows = (
        db.session.query(
            Project.code.label("project_code"),
            func.sum(AcquiredFile.size_bytes).label("total_bytes"),
        )
        .join(AcquiredFile.experiment)
        .join(Experiment.project)
        .group_by(Project.code)
        .order_by(func.sum(AcquiredFile.size_bytes).desc())
        .all()
    )
    data = [
        {"project": row.project_code, "gb": round((row.total_bytes or 0) / 1e9, 4)}
        for row in rows
    ]
    return render_template("disk_usage.html", data=data)


@app.route("/about")
def about():
    return render_template("about.html")
