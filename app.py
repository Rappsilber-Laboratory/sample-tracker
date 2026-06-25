import csv
import io
import os
import re
import sqlite3
from datetime import date, datetime

import click
from collections import defaultdict

from flask import Flask, Response, abort, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import event, func
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from flask_wtf import CSRFProtect
from sqlalchemy.orm import joinedload

from config import Config
from forms import (
    CellLineForm,
    ExperimentForm,
    AcquiredFileEditForm,
    AcquiredFileForm,
    MassSpecSampleForm,
    ProjectForm,
    SpeciesForm,
    UserForm,
    VirusForm,
)
from models import (
    CellLine,
    CrosslinkSample,
    Experiment,
    AcquiredFile,
    IdentificationSample,
    MassSpecSample,
    Project,
    QueuedFile,
    Species,
    User,
    Virus,
    db,
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
# experiment or sample encode the full parent chain, joined by \x1f (a char
# that never appears in a code).
# ---------------------------------------------------------------------------
TOKEN_SEP = "\x1f"


def _exp_token(project_code, experiment_code):
    return f"{project_code}{TOKEN_SEP}{experiment_code}"


def _sample_token(project_code, experiment_code, code):
    return TOKEN_SEP.join([project_code, experiment_code, code])


def _next_experiment_code(project_code):
    """Suggested code for a new experiment: 'E' + zero-padded (count + 1)."""
    n = Experiment.query.filter_by(project_code=project_code).count() + 1
    return f"E{n:02d}"


def _next_sample_code(project_code, experiment_code):
    """Suggested code for a new sample: 'S' + zero-padded (count + 1)."""
    n = MassSpecSample.query.filter_by(
        project_code=project_code, experiment_code=experiment_code
    ).count() + 1
    return f"S{n:02d}"


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
        "samples": MassSpecSample,
        "species": Species,
        "cell_lines": CellLine,
        "viruses": Virus,
        "files": AcquiredFile,
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


@app.route("/api/tree")
def api_tree():
    def file_nodes(sample):
        nodes = [
            {
                "id": f.id,
                "name": f.filename or f.location or str(f.id),
                "level": "file",
                "total_bytes": int(f.size_bytes or 0),
                "url": url_for("file_detail", id=f.id),
            }
            for f in sample.acquired_files
        ]
        nodes.sort(key=lambda n: n["total_bytes"], reverse=True)
        return nodes

    def sample_node(s):
        children = file_nodes(s)
        total = sum(c["total_bytes"] for c in children)
        return {"code": s.code, "name": s.name, "level": "sample", "total_bytes": total,
                "url": url_for("sample_detail", project_code=s.project_code,
                               experiment_code=s.experiment_code, code=s.code),
                "children": children}

    def experiment_node(e):
        children = sorted([sample_node(s) for s in e.samples], key=lambda n: n["total_bytes"], reverse=True)
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
        "name": "Mass Spec Acquisition Tracker",
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
    # One grouped aggregate per entity instead of a query per project. Samples
    # and files carry project_code on their own composite key, so neither needs
    # to join through Experiment. Projects with zero rows are simply absent from
    # the result (the template defaults missing codes to 0).
    exp_counts = dict(
        db.session.query(Experiment.project_code, func.count())
        .group_by(Experiment.project_code).all()
    )
    sample_counts = dict(
        db.session.query(MassSpecSample.project_code, func.count())
        .group_by(MassSpecSample.project_code).all()
    )
    file_counts = dict(
        db.session.query(AcquiredFile.project_code, func.count())
        .group_by(AcquiredFile.project_code).all()
    )
    return render_template(
        "project/list.html", projects=projects, show_archived=show_archived,
        users=users, exp_counts=exp_counts, sample_counts=sample_counts, file_counts=file_counts
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
    samples = (
        MassSpecSample.query.join(Experiment)
        .options(joinedload(MassSpecSample.experiment))
        .filter(Experiment.project_code == code)
        .order_by(MassSpecSample.name).all()
    )
    files = (
        AcquiredFile.query.join(MassSpecSample).join(Experiment)
        .options(joinedload(AcquiredFile.sample).joinedload(MassSpecSample.experiment).joinedload(Experiment.project))
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
        .join(AcquiredFile.sample)
        .join(MassSpecSample.experiment)
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
        contact_name=contact_name, samples=samples, files=files,
        experiment_count=len(experiments), sample_count=len(samples),
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
        flash("Project updated.", "success")
        return redirect(url_for("project_detail", code=project.code))
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


@app.route("/projects/<project_code>/experiments/<code>")
def experiment_detail(project_code, code):
    experiment = db.get_or_404(Experiment, (project_code, code))
    samples = (
        MassSpecSample.query
        .filter_by(project_code=project_code, experiment_code=code)
        .order_by(MassSpecSample.name).all()
    )
    files = (
        AcquiredFile.query.join(MassSpecSample)
        .options(joinedload(AcquiredFile.sample).joinedload(MassSpecSample.experiment).joinedload(Experiment.project))
        .filter(MassSpecSample.project_code == project_code, MassSpecSample.experiment_code == code)
        .order_by(AcquiredFile.file_date.desc(), AcquiredFile.filename).all()
    )
    total_size_gb = sum(f.size_bytes or 0 for f in files) / 1e9
    users = {u.initials: u.name for u in User.query.all()}
    return render_template(
        "experiment/detail.html", experiment=experiment, samples=samples, files=files,
        sample_count=len(samples), file_count=len(files), total_size_gb=total_size_gb, users=users,
    )


@app.route("/projects/<project_code>/experiments/new", methods=["GET", "POST"])
def experiment_create(project_code):
    # The parent project is fixed by the URL (you reach this page from a project),
    # so there's no project selector — it's shown read-only on the form.
    project = db.get_or_404(Project, project_code)
    form = ExperimentForm()
    del form.project_code
    form.user_initials.choices = _user_initials_choices()
    if request.method == "GET":
        if project.user_initials:
            form.user_initials.data = project.user_initials
        form.code.data = _next_experiment_code(project_code)
    if form.validate_on_submit():
        experiment = Experiment()
        form.populate_obj(experiment)
        experiment.project_code = project_code
        db.session.add(experiment)
        if _commit_unique(form, "code", "An experiment with code"):
            flash("Experiment created.", "success")
            return redirect(url_for("experiment_detail", project_code=experiment.project_code, code=experiment.code))
    cancel_url = url_for("project_detail", code=project_code)
    return render_template(
        "experiment/form.html", form=form, experiment=None, samples=[],
        project=project, cancel_url=cancel_url,
    )


@app.route("/projects/<project_code>/experiments/<code>/edit", methods=["GET", "POST"])
def experiment_edit(project_code, code):
    experiment = db.get_or_404(Experiment, (project_code, code))
    samples = (
        MassSpecSample.query
        .filter_by(project_code=project_code, experiment_code=code)
        .order_by(MassSpecSample.name).all()
    )
    form = ExperimentForm(obj=experiment)
    del form.code
    # project_code is part of the primary key — re-parenting is not supported.
    del form.project_code
    form.user_initials.choices = _user_initials_choices()
    if form.validate_on_submit():
        form.populate_obj(experiment)
        db.session.commit()
        flash("Experiment updated.", "success")
        return redirect(url_for("experiment_detail", project_code=experiment.project_code, code=experiment.code))
    if request.method == "POST":
        flash("Could not save — please check the fields below.", "error")
    return render_template("experiment/form.html", form=form, experiment=experiment, samples=samples)


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
        flash("Species updated.", "success")
        return redirect(url_for("species_detail", id=species.id))
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
        flash("Cell line updated.", "success")
        return redirect(url_for("cell_line_detail", cellosaurus_id=cl.cellosaurus_id))
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
        flash("Virus updated.", "success")
        return redirect(url_for("virus_detail", id=virus.id))
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
        flash("User updated.", "success")
        return redirect(url_for("user_detail", initials=user.initials))
    return render_template("user/form.html", form=form, user=user)


# ---------------------------------------------------------------------------
# Samples
# ---------------------------------------------------------------------------
def _sample_copy_choices():
    """Options for the 'copy from existing sample' dropdown on the new-sample form."""
    samples = (
        MassSpecSample.query.join(Experiment).join(Project)
        .filter(Project.active == True)  # noqa: E712
        .order_by(Project.code, Experiment.code, MassSpecSample.code)
        .all()
    )
    return [
        (
            _sample_token(s.project_code, s.experiment_code, s.code),
            f"{s.project_code} / {s.experiment_code} / {s.code} — {s.name}",
        )
        for s in samples
    ]


def _species_multi_choices():
    return [(s.id, s.species_name) for s in Species.query.order_by(Species.species_name).all()]


def _cell_line_multi_choices():
    return [
        (cl.cellosaurus_id, cl.cell_line_name)
        for cl in CellLine.query.order_by(CellLine.cell_line_name).all()
    ]


def _coerce_select_fields(sample):
    """Convert empty-string values from optional SelectFields to None so they
    satisfy the DB CHECK constraints (which allow NULL but not empty string)."""
    for field in ("synthetic_peptide", "quantitation_method", "crosslinking_type"):
        if getattr(sample, field) == "":
            setattr(sample, field, None)


def _nullify_crosslink_fields(sample):
    """Clear crosslink fields when sample is identification type, and vice versa."""
    crosslink_fields = [
        "crosslinker", "crosslinking_type",
        "protein_or_cell_concentration", "protein_or_cell_concentration_unit",
        "crosslinker_or_compound_concentration", "crosslinker_or_compound_concentration_unit",
        "organic_solvent_concentration", "organic_solvent_concentration_unit",
        "reaction_temperature_in_celsius", "reaction_time_in_minutes",
        "quenching_reagent", "uv_source", "uv_time_in_seconds",
        "uv_wavelength_in_nanometers",
    ]
    identification_fields = ["peptide_level_fraction"]

    if sample.crosslinked_sample:
        for f in identification_fields:
            setattr(sample, f, None)
    else:
        for f in crosslink_fields:
            setattr(sample, f, None)


@app.route("/samples")
def sample_list():
    samples = (
        MassSpecSample.query.join(Experiment)
        .join(Project)
        .options(joinedload(MassSpecSample.experiment).joinedload(Experiment.project))
        .filter(Project.active == True)  # noqa: E712
        .order_by(MassSpecSample.name)
        .all()
    )
    users = {u.initials: u.name for u in User.query.all()}
    return render_template("sample/list.html", samples=samples, users=users)


@app.route("/projects/<project_code>/experiments/<experiment_code>/samples/new", methods=["GET", "POST"])
def sample_create(project_code, experiment_code):
    # The parent experiment is fixed by the URL (you reach this page from an
    # experiment), so there's no experiment selector — it's shown read-only.
    experiment = db.get_or_404(Experiment, (project_code, experiment_code))

    # Optionally pre-fill the form from an existing sample (everything except the
    # identity fields code/name and the parent experiment). Only relevant on GET —
    # on POST the values come from the submitted form.
    copy_source = None
    copy_from = request.values.get("copy_from")
    if copy_from:
        parts = _split_token(copy_from)
        if len(parts) == 3:
            copy_source = db.session.get(MassSpecSample, tuple(parts))

    if request.method == "GET" and copy_source:
        form = MassSpecSampleForm(obj=copy_source)
        # Identity fields must stay unique — don't copy them.
        form.code.data = ""
        form.name.data = ""
        form.crosslinked_sample.data = bool(copy_source.crosslinked_sample)
        form.quantitation.data = bool(copy_source.quantitation)
        form.species_ids.data = [s.id for s in copy_source.species_list]
        form.cellosaurus_ids.data = [cl.cellosaurus_id for cl in copy_source.cell_lines]
    else:
        form = MassSpecSampleForm()

    del form.experiment_code
    form.user_initials.choices = _user_initials_choices()
    form.species_ids.choices = _species_multi_choices()
    form.cellosaurus_ids.choices = _cell_line_multi_choices()
    if request.method == "GET":
        if not copy_source and experiment.user_initials:
            form.user_initials.data = experiment.user_initials
        form.code.data = _next_sample_code(project_code, experiment_code)
    if form.validate_on_submit():
        is_crosslinked = form.crosslinked_sample.data
        if is_crosslinked:
            sample = CrosslinkSample()
        else:
            sample = IdentificationSample()
        form.populate_obj(sample)
        sample.project_code = project_code
        sample.experiment_code = experiment_code
        sample.crosslinked_sample = 1 if is_crosslinked else 0
        sample.quantitation = 1 if form.quantitation.data else 0
        _coerce_select_fields(sample)
        _nullify_crosslink_fields(sample)
        db.session.add(sample)
        # Resolve the many-to-many selections with autoflush suppressed: the
        # sample is already pending, so an autoflush here would try to INSERT it
        # mid-query and a duplicate code would raise IntegrityError from the
        # query (not the commit), escaping _commit_unique below. Deferring the
        # flush to commit keeps duplicate handling in one place.
        with db.session.no_autoflush:
            sample.species_list = Species.query.filter(
                Species.id.in_(form.species_ids.data)
            ).all()
            sample.cell_lines = CellLine.query.filter(
                CellLine.cellosaurus_id.in_(form.cellosaurus_ids.data)
            ).all()
        if _commit_unique(form, "code", "A sample with code"):
            flash("Sample created.", "success")
            return redirect(url_for("sample_detail", project_code=sample.project_code, experiment_code=sample.experiment_code, code=sample.code))
    cancel_url = url_for("experiment_detail", project_code=project_code, code=experiment_code)
    return render_template(
        "sample/form.html", form=form, sample=None, experiment=experiment,
        copy_samples=_sample_copy_choices(), copy_from=copy_from, cancel_url=cancel_url,
    )


@app.route("/projects/<project_code>/experiments/<experiment_code>/samples/<code>")
def sample_detail(project_code, experiment_code, code):
    sample = db.get_or_404(MassSpecSample, (project_code, experiment_code, code))
    total_size_gb = sum(f.size_bytes or 0 for f in sample.acquired_files) / 1e9
    users = {u.initials: u.name for u in User.query.all()}
    active_users = User.query.filter_by(active=True).order_by(User.name).all()
    return render_template(
        "sample/detail.html", sample=sample,
        file_count=len(sample.acquired_files), total_size_gb=total_size_gb, users=users,
        active_users=active_users,
    )


# ---------------------------------------------------------------------------
# Queue (queued_file)
# ---------------------------------------------------------------------------
def queued_filename(qf):
    """Build the run filename for a QueuedFile row.

    Sample run: {inst}_{YYYYMMDD}-{NN}_{proj}_{user}_{exp}_{samp}_{postfix}
    Blank run:  {inst}_{YYYYMMDD}-{NN}_BLANK-AND-CLEANING
    NN = daily_counter (the per-day, per-instrument run order) padded to 2 digits.

    file_name_root is the DB-generated part up to and including the '_' before the
    postfix, so the full filename is just root + postfix.
    """
    root = qf.file_name_root  # ends with the '_' separator before the postfix
    if qf.sample_code:
        return f"{root}{qf.postfix}" if qf.postfix else root[:-1]
    # Blank / cleaning run — postfix carries the label.
    return f"{root}{qf.postfix or 'BLANK-AND-CLEANING'}"


def _parse_queue_date(date_str):
    try:
        return datetime.strptime(date_str, "%Y%m%d").date()
    except (ValueError, TypeError):
        abort(400, "date must be YYYYMMDD")


def _next_daily_counter(instrument, day):
    """Next run-order slot for an instrument on a day (append to the end).

    Counts *all* rows for the day, including exported ones, on purpose: clearing
    the queue marks rows exported instead of deleting them, so the counter keeps
    climbing and never re-uses a run number already burned into an exported CSV.
    """
    current_max = (
        db.session.query(func.max(QueuedFile.daily_counter))
        .filter_by(instrument_initial=instrument, date_queued=day)
        .scalar()
    )
    return (current_max or 0) + 1


def _append_to_queue(instrument, day, build_rows, attempts=5):
    """Append rows to an instrument's day queue, race-safe.

    ``build_rows(start)`` returns the ``QueuedFile`` rows to insert, numbered from
    the first free ``daily_counter``. Two concurrent requests can read the same
    max counter and pick the same slot; the loser's commit then violates the
    composite PK. We catch that, roll back, recompute the next free slot, and
    retry the whole (re-numbered) batch — each attempt commits atomically, so
    there are never partial inserts.
    """
    for _ in range(attempts):
        rows = build_rows(_next_daily_counter(instrument, day))
        db.session.add_all(rows)
        try:
            db.session.commit()
            return rows
        except IntegrityError:
            db.session.rollback()
    abort(409, "Could not assign a queue slot — please retry.")


def _day_queue_json(day):
    """Snapshot of the queue for one day: tab list + rows grouped by instrument."""
    instruments = [
        r[0] for r in db.session.query(QueuedFile.instrument_initial)
        .filter(QueuedFile.exported.is_(False))
        .distinct().order_by(QueuedFile.instrument_initial).all()
    ]
    rows = (
        QueuedFile.query.options(joinedload(QueuedFile.sample))
        .filter_by(date_queued=day, exported=False)
        .order_by(QueuedFile.instrument_initial, QueuedFile.daily_counter)
        .all()
    )
    queues = defaultdict(list)
    for qf in rows:
        queues[qf.instrument_initial].append({
            "daily_counter": qf.daily_counter,
            "filename": queued_filename(qf),
            "postfix": qf.postfix,
            "user_initials": qf.user_initials,
            "project_code": qf.project_code,
            "experiment_code": qf.experiment_code,
            "sample_code": qf.sample_code,
            "sample_url": (
                url_for("sample_detail", project_code=qf.project_code,
                        experiment_code=qf.experiment_code, code=qf.sample_code)
                if qf.sample_code else None
            ),
            "is_blank": not qf.sample_code,
        })
    return {"date": day.isoformat(), "instruments": instruments, "queues": dict(queues)}


def build_day_csv(rows, day):
    """Build a Thermo Xcalibur sequence CSV for one instrument's ordered day queue.

    Xcalibur sequence files are plain Windows-ANSI (cp1252) text, not UTF-7 — so
    codes, the data path, etc. are written literally. One row per queued run, in
    daily_counter order; blanks get a BLANK-AND-CLEANING comment. cp1252 keeps all
    ASCII as-is and still handles the occasional µ/°/etc. in a sample name;
    anything it can't represent is replaced rather than crashing the export.
    """
    path = f"D:\\Data\\{day:%Y}\\{day:%y}{day:%m}\\{day:%y%m%d}"
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["Bracket Type=4", "", "", "", "", ""])
    writer.writerow(["File Name", "Path", "Instrument Method", "Position", "Inj Vol", "Comment"])
    for qf in rows:
        if qf.sample_code:
            sample_name = qf.sample.name if qf.sample else ""
            comment = f"{qf.project_code}_{qf.experiment_code}_{qf.sample_code} - {sample_name}"
        else:
            comment = "BLANK-AND-CLEANING"
        writer.writerow([queued_filename(qf), path, "", "", "", comment])
    return buf.getvalue().encode("cp1252", errors="replace")


@app.route(
    "/projects/<project_code>/experiments/<experiment_code>/samples/<code>/queue",
    methods=["POST"],
)
def sample_queue(project_code, experiment_code, code):
    """Append a batch of runs for one sample to an instrument's day queue."""
    db.get_or_404(MassSpecSample, (project_code, experiment_code, code))
    data = request.get_json(silent=True) or {}
    inst = (data.get("instrument_initial") or "").strip()
    user = (data.get("user_initials") or "").strip()
    day = _parse_queue_date((data.get("date") or "").strip())
    try:
        count = int(data.get("count"))
    except (TypeError, ValueError):
        abort(400, "count must be an integer")
    if not inst or not user:
        abort(400, "instrument_initial and user_initials are required")
    if count < 1:
        abort(400, "count must be >= 1")

    _append_to_queue(inst, day, lambda start: [
        QueuedFile(
            instrument_initial=inst,
            date_queued=day,
            daily_counter=start + i,
            project_code=project_code,
            experiment_code=experiment_code,
            sample_code=code,
            user_initials=user,
            postfix=f"f{i + 1:02d}",
        )
        for i in range(count)
    ])
    return jsonify(_day_queue_json(day))


@app.route("/api/queue")
def api_queue():
    date_str = (request.args.get("date") or "").strip()
    day = _parse_queue_date(date_str) if date_str else date.today()
    return jsonify(_day_queue_json(day))


@app.route("/api/queue/blank", methods=["POST"])
def api_queue_blank():
    data = request.get_json(silent=True) or {}
    inst = (data.get("instrument_initial") or "").strip()
    user = (data.get("user_initials") or "").strip() or None
    day = _parse_queue_date((data.get("date") or "").strip())
    if not inst:
        abort(400, "instrument_initial is required")
    _append_to_queue(inst, day, lambda start: [QueuedFile(
        instrument_initial=inst,
        date_queued=day,
        daily_counter=start,
        user_initials=user,
        postfix="BLANK-AND-CLEANING",
    )])
    return jsonify(_day_queue_json(day))


def _queue_row_or_404(data):
    inst = (data.get("instrument_initial") or "").strip()
    day = _parse_queue_date((data.get("date") or "").strip())
    try:
        counter = int(data.get("daily_counter"))
    except (TypeError, ValueError):
        abort(400, "daily_counter must be an integer")
    qf = db.session.get(QueuedFile, (inst, day, counter))
    if qf is None:
        abort(404)
    return qf, day


@app.route("/api/queue/update", methods=["POST"])
def api_queue_update():
    data = request.get_json(silent=True) or {}
    qf, day = _queue_row_or_404(data)
    qf.postfix = (data.get("postfix") or "").strip() or None
    db.session.commit()
    return jsonify(_day_queue_json(day))


@app.route("/api/queue/delete", methods=["POST"])
def api_queue_delete():
    data = request.get_json(silent=True) or {}
    qf, day = _queue_row_or_404(data)
    db.session.delete(qf)
    db.session.commit()
    return jsonify(_day_queue_json(day))


@app.route("/api/queue/clear", methods=["POST"])
def api_queue_clear():
    """Hide an instrument's day queue by marking its runs exported (not deleted),
    so the daily_counter keeps climbing for any runs re-queued the same day."""
    data = request.get_json(silent=True) or {}
    inst = (data.get("instrument_initial") or "").strip()
    day = _parse_queue_date((data.get("date") or "").strip())
    if not inst:
        abort(400, "instrument_initial is required")
    QueuedFile.query.filter_by(
        instrument_initial=inst, date_queued=day, exported=False
    ).update({"exported": True})
    db.session.commit()
    return jsonify(_day_queue_json(day))


@app.route("/api/queue/csv")
def api_queue_csv():
    inst = (request.args.get("instrument") or "").strip()
    day = _parse_queue_date((request.args.get("date") or "").strip())
    if not inst:
        abort(400, "instrument is required")
    rows = (
        QueuedFile.query.options(joinedload(QueuedFile.sample))
        .filter_by(instrument_initial=inst, date_queued=day)
        .order_by(QueuedFile.daily_counter)
        .all()
    )
    csv_bytes = build_day_csv(rows, day)
    resp = Response(csv_bytes, mimetype="text/csv")
    resp.headers["Content-Disposition"] = (
        f'attachment; filename="{inst}_{day:%Y%m%d}_sequence.csv"'
    )
    return resp


@app.route("/projects/<project_code>/experiments/<experiment_code>/samples/<code>/edit", methods=["GET", "POST"])
def sample_edit(project_code, experiment_code, code):
    sample = db.get_or_404(MassSpecSample, (project_code, experiment_code, code))
    form = MassSpecSampleForm(obj=sample)
    del form.code
    # experiment_code/project_code are part of the primary key — re-parenting
    # is not supported, so the experiment selector is omitted on edit.
    del form.experiment_code
    form.user_initials.choices = _user_initials_choices()
    if sample.user_initials and sample.user_initials not in [c[0] for c in form.user_initials.choices]:
        form.user_initials.choices.append((sample.user_initials, sample.user_initials))
    form.species_ids.choices = _species_multi_choices()
    form.cellosaurus_ids.choices = _cell_line_multi_choices()

    if request.method == "GET":
        form.crosslinked_sample.data = bool(sample.crosslinked_sample)
        form.quantitation.data = bool(sample.quantitation)
        form.species_ids.data = [s.id for s in sample.species_list]
        form.cellosaurus_ids.data = [cl.cellosaurus_id for cl in sample.cell_lines]

    if form.validate_on_submit():
        is_crosslinked = form.crosslinked_sample.data
        form.populate_obj(sample)
        sample.crosslinked_sample = 1 if is_crosslinked else 0
        sample.quantitation = 1 if form.quantitation.data else 0
        _coerce_select_fields(sample)
        # Many-to-many sync
        sample.species_list = Species.query.filter(
            Species.id.in_(form.species_ids.data)
        ).all()
        sample.cell_lines = CellLine.query.filter(
            CellLine.cellosaurus_id.in_(form.cellosaurus_ids.data)
        ).all()
        _nullify_crosslink_fields(sample)
        key = (sample.project_code, sample.experiment_code, sample.code)
        db.session.commit()
        flash("Sample updated.", "success")
        return redirect(url_for("sample_detail", project_code=key[0], experiment_code=key[1], code=key[2]))
    if request.method == "POST":
        flash("Could not save — please check the fields below.", "error")
    return render_template("sample/form.html", form=form, sample=sample)


# ---------------------------------------------------------------------------
# Files (DB records)
# ---------------------------------------------------------------------------
@app.route("/files")
def file_list():
    files = (
        AcquiredFile.query.options(
            joinedload(AcquiredFile.sample).joinedload(MassSpecSample.experiment).joinedload(Experiment.project)
        )
        .order_by(AcquiredFile.file_date.desc(), AcquiredFile.filename)
        .all()
    )
    users = {u.initials: u.name for u in User.query.all()}
    return render_template("file/list.html", files=files, users=users)


@app.route("/projects/<project_code>/experiments/<experiment_code>/samples/<code>/db-files/new", methods=["GET", "POST"])
def file_create(project_code, experiment_code, code):
    sample = db.get_or_404(MassSpecSample, (project_code, experiment_code, code))
    form = AcquiredFileForm()
    if form.validate_on_submit():
        f = AcquiredFile(
            project_code=project_code, experiment_code=experiment_code, sample_code=code
        )
        form.populate_obj(f)
        f.meta = form.meta_json.data
        if f.size_bytes is not None:
            f.size_bytes = int(f.size_bytes * 1e9)
        db.session.add(f)
        db.session.commit()
        flash("File record created.", "success")
        return redirect(url_for("file_detail", id=f.id))
    return render_template("file/form.html", form=form, file=None, sample=sample)


@app.route("/files/<int:id>")
def file_detail(id):
    f = db.get_or_404(AcquiredFile, id)
    return render_template("file/detail.html", file=f)


def _file_edit_tree():
    experiments = Experiment.query.order_by(Experiment.name).all()
    samples = MassSpecSample.query.order_by(MassSpecSample.name).all()
    return {
        # "value" is the encoded token used as the <option> value; "project_code"
        # / "experiment_value" key the client-side cascade filters.
        "experiments": [
            {"value": _exp_token(e.project_code, e.code), "project_code": e.project_code,
             "label": f"{e.code} — {e.name}"}
            for e in experiments
        ],
        "samples": [
            {"value": _sample_token(s.project_code, s.experiment_code, s.code),
             "experiment_value": _exp_token(s.project_code, s.experiment_code),
             "label": f"{s.code} — {s.name}"}
            for s in samples
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
    # experiment/sample option lists are populated by JS from the embedded tree;
    # the server-side choices only need to contain the currently-selected value
    # so WTForms validates the POST.
    form.experiment_code.choices = [("", "—")]
    form.sample_code.choices = [("", "—")]
    if request.method == "GET" and f.sample is not None:
        e = f.sample.experiment
        exp_token = _exp_token(e.project_code, e.code)
        sample_token = _sample_token(f.sample.project_code, f.sample.experiment_code, f.sample.code)
        form.experiment_code.choices.append((exp_token, f"{e.code} — {e.name}"))
        form.sample_code.choices.append((sample_token, f"{f.sample.code} — {f.sample.name}"))
        form.project_code.data = e.project_code
        form.experiment_code.data = exp_token
        form.sample_code.data = sample_token
    if form.validate_on_submit():
        # The server only needs the sample token; project/experiment selects are
        # pure UX scaffolding handled client-side. Decode the posted token into
        # the composite key and let the FK constraint reject anything bogus.
        parts = _split_token(request.form.get("sample_code") or "")
        if len(parts) == 3:
            f.project_code, f.experiment_code, f.sample_code = parts
        else:
            f.project_code = f.experiment_code = f.sample_code = None
        db.session.commit()
        flash("File association updated.", "success")
        return redirect(url_for("file_detail", id=f.id))
    return render_template("file/edit.html", form=form, file=f, tree=_file_edit_tree())


@app.route("/files/<int:id>/delete", methods=["POST"])
def file_delete(id):
    f = db.get_or_404(AcquiredFile, id)
    key = (f.project_code, f.experiment_code, f.sample_code)
    db.session.delete(f)
    db.session.commit()
    flash("File record deleted.", "success")
    if all(key):
        return redirect(url_for("sample_detail", project_code=key[0], experiment_code=key[1], code=key[2]))
    return redirect(url_for("file_list"))


@app.route("/instrument-usage")
def instrument_usage():
    rows = (
        db.session.query(
            AcquiredFile.instrument_initial,
            AcquiredFile.file_date,
            Project.code.label("project_code"),
            func.sum(AcquiredFile.size_bytes).label("total_bytes"),
        )
        .join(AcquiredFile.sample)
        .join(MassSpecSample.experiment)
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
        .join(AcquiredFile.sample)
        .join(MassSpecSample.experiment)
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
