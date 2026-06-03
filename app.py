import base64
import csv
import io
import os
import re
import sqlite3
from datetime import datetime
from string import ascii_letters, digits

import click
from collections import defaultdict

from flask import Flask, Response, abort, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import event, func
from sqlalchemy.engine import Engine
from flask_wtf import CSRFProtect
from sqlalchemy.orm import joinedload

from config import Config
from forms import (
    CellLineForm,
    ExperimentForm,
    MassSpecAcquisitionEditForm,
    MassSpecAcquisitionForm,
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
    IdentificationSample,
    MassSpecAcquisition,
    MassSpecSample,
    Project,
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
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
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
    try:
        return {
            "nav_counts": {
                "projects": db.session.query(Project).count(),
                "experiments": db.session.query(Experiment).count(),
                "samples": db.session.query(MassSpecSample).count(),
                "species": db.session.query(Species).count(),
                "cell_lines": db.session.query(CellLine).count(),
                "viruses": db.session.query(Virus).count(),
                "files": db.session.query(MassSpecAcquisition).count(),
                "users": db.session.query(User).count(),
            }
        }
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
            for f in sample.files
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
    exp_counts = {
        p.code: Experiment.query.filter_by(project_code=p.code).count()
        for p in projects
    }
    sample_counts = {
        p.code: MassSpecSample.query.join(Experiment).filter(Experiment.project_code == p.code).count()
        for p in projects
    }
    file_counts = {
        p.code: MassSpecAcquisition.query.join(MassSpecSample).join(Experiment).filter(Experiment.project_code == p.code).count()
        for p in projects
    }
    projects = sorted(projects, key=lambda p: exp_counts[p.code], reverse=True)
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
        MassSpecAcquisition.query.join(MassSpecSample).join(Experiment)
        .options(joinedload(MassSpecAcquisition.sample).joinedload(MassSpecSample.experiment).joinedload(Experiment.project))
        .filter(Experiment.project_code == code)
        .order_by(MassSpecAcquisition.date.desc(), MassSpecAcquisition.filename).all()
    )
    total_size_gb = sum(f.size_bytes or 0 for f in files) / (1024 ** 3)
    chart_rows = (
        db.session.query(
            MassSpecAcquisition.date,
            Experiment.code.label("experiment_code"),
            func.sum(MassSpecAcquisition.size_bytes).label("total_bytes"),
        )
        .join(MassSpecAcquisition.sample)
        .join(MassSpecSample.experiment)
        .filter(Experiment.project_code == code)
        .filter(MassSpecAcquisition.date.isnot(None))
        .group_by(MassSpecAcquisition.date, Experiment.code)
        .order_by(MassSpecAcquisition.date)
        .all()
    )
    chart_data = [
        {"date": row.date.isoformat(), "experiment": row.experiment_code, "gb": round((row.total_bytes or 0) / 1e9, 4)}
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
        db.session.commit()
        flash("Project created.", "success")
        return redirect(url_for("project_list"))
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
def _active_project_choices():
    projects = Project.query.filter_by(active=True).order_by(Project.name).all()
    return [(p.code, f"{p.code} — {p.name}") for p in projects]


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
        MassSpecAcquisition.query.join(MassSpecSample)
        .options(joinedload(MassSpecAcquisition.sample).joinedload(MassSpecSample.experiment).joinedload(Experiment.project))
        .filter(MassSpecSample.project_code == project_code, MassSpecSample.experiment_code == code)
        .order_by(MassSpecAcquisition.date.desc(), MassSpecAcquisition.filename).all()
    )
    total_size_gb = sum(f.size_bytes or 0 for f in files) / (1024 ** 3)
    users = {u.initials: u.name for u in User.query.all()}
    return render_template(
        "experiment/detail.html", experiment=experiment, samples=samples, files=files,
        sample_count=len(samples), file_count=len(files), total_size_gb=total_size_gb, users=users,
    )


@app.route("/experiments/new", methods=["GET", "POST"])
def experiment_create():
    form = ExperimentForm()
    form.project_code.choices = _active_project_choices()
    form.user_initials.choices = _user_initials_choices()
    if request.method == "GET":
        if request.args.get("project_code"):
            form.project_code.data = request.args["project_code"]
            project = db.session.get(Project, request.args["project_code"])
            if project and project.user_initials:
                form.user_initials.data = project.user_initials
        # Default code from the count of experiments in the selected project.
        selected_project = form.project_code.data or (
            form.project_code.choices[0][0] if form.project_code.choices else None
        )
        if selected_project:
            form.code.data = _next_experiment_code(selected_project)
    if form.validate_on_submit():
        experiment = Experiment()
        form.populate_obj(experiment)
        db.session.add(experiment)
        db.session.commit()
        flash("Experiment created.", "success")
        return redirect(url_for("project_detail", code=experiment.project_code))
    return render_template("experiment/form.html", form=form, experiment=None, samples=[])


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
        return redirect(url_for("species_list"))
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
        cl.viruses = Virus.query.filter(Virus.id.in_(form.virus_ids.data)).all()
        db.session.commit()
        flash("Cell line created.", "success")
        return redirect(url_for("cell_line_list"))
    return render_template("cell_line/form.html", form=form, cell_line=None)


@app.route("/cell-lines/<cellosaurus_id>")
def cell_line_detail(cellosaurus_id):
    cl = db.get_or_404(CellLine, cellosaurus_id)
    return render_template("cell_line/detail.html", cell_line=cl)


@app.route("/cell-lines/<cellosaurus_id>/edit", methods=["GET", "POST"])
def cell_line_edit(cellosaurus_id):
    cl = db.get_or_404(CellLine, cellosaurus_id)
    form = CellLineForm(obj=cl)
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
        return redirect(url_for("virus_list"))
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
        db.session.commit()
        flash("User created.", "success")
        return redirect(url_for("user_list"))
    return render_template("user/form.html", form=form, user=None)


@app.route("/users/<initials>")
def user_detail(initials):
    user = db.get_or_404(User, initials)
    return render_template("user/detail.html", user=user)


@app.route("/users/<initials>/edit", methods=["GET", "POST"])
def user_edit(initials):
    user = db.get_or_404(User, initials)
    form = UserForm(obj=user)
    if form.validate_on_submit():
        form.populate_obj(user)
        db.session.commit()
        flash("User updated.", "success")
        return redirect(url_for("user_detail", initials=user.initials))
    return render_template("user/form.html", form=form, user=user)


# ---------------------------------------------------------------------------
# Samples
# ---------------------------------------------------------------------------
def _experiment_choices():
    experiments = (
        Experiment.query.join(Project)
        .filter(Project.active == True, Experiment.active == True)  # noqa: E712
        .order_by(Experiment.name)
        .all()
    )
    return [(_exp_token(e.project_code, e.code), f"{e.project.code} — {e.name}") for e in experiments]


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


@app.route("/samples/new", methods=["GET", "POST"])
def sample_create():
    # Optionally pre-fill the form from an existing sample (everything except
    # the identity fields code/name). Only relevant on GET — on POST the values
    # come from the submitted form.
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
        form.experiment_code.data = _exp_token(copy_source.project_code, copy_source.experiment_code)
        form.species_ids.data = [s.id for s in copy_source.species_list]
        form.cellosaurus_ids.data = [cl.cellosaurus_id for cl in copy_source.cell_lines]
    else:
        form = MassSpecSampleForm()

    form.experiment_code.choices = _experiment_choices()
    form.user_initials.choices = _user_initials_choices()
    form.species_ids.choices = _species_multi_choices()
    form.cellosaurus_ids.choices = _cell_line_multi_choices()
    if (
        request.method == "GET" and not copy_source
        and request.args.get("project_code") and request.args.get("experiment_code")
    ):
        project_code = request.args["project_code"]
        experiment_code = request.args["experiment_code"]
        form.experiment_code.data = _exp_token(project_code, experiment_code)
        experiment = db.session.get(Experiment, (project_code, experiment_code))
        if experiment and experiment.user_initials:
            form.user_initials.data = experiment.user_initials
    if request.method == "GET":
        # Default code from the count of samples in the selected experiment.
        selected_exp = form.experiment_code.data or (
            form.experiment_code.choices[0][0] if form.experiment_code.choices else None
        )
        parts = _split_token(selected_exp) if selected_exp else ()
        if len(parts) == 2:
            form.code.data = _next_sample_code(parts[0], parts[1])
    if form.validate_on_submit():
        is_crosslinked = form.crosslinked_sample.data
        if is_crosslinked:
            sample = CrosslinkSample()
        else:
            sample = IdentificationSample()
        form.populate_obj(sample)
        sample.project_code, sample.experiment_code = _split_token(form.experiment_code.data)
        sample.crosslinked_sample = 1 if is_crosslinked else 0
        sample.quantitation = 1 if form.quantitation.data else 0
        _coerce_select_fields(sample)
        _nullify_crosslink_fields(sample)
        db.session.add(sample)
        # Many-to-many (after add to avoid autoflush warning)
        sample.species_list = Species.query.filter(
            Species.id.in_(form.species_ids.data)
        ).all()
        sample.cell_lines = CellLine.query.filter(
            CellLine.cellosaurus_id.in_(form.cellosaurus_ids.data)
        ).all()
        db.session.commit()
        flash("Sample created.", "success")
        return redirect(url_for("experiment_detail", project_code=sample.project_code, code=sample.experiment_code))
    return render_template(
        "sample/form.html", form=form, sample=None,
        copy_samples=_sample_copy_choices(), copy_from=copy_from,
    )


@app.route("/projects/<project_code>/experiments/<experiment_code>/samples/<code>")
def sample_detail(project_code, experiment_code, code):
    sample = db.get_or_404(MassSpecSample, (project_code, experiment_code, code))
    total_size_gb = sum(f.size_bytes or 0 for f in sample.files) / (1024 ** 3)
    users = {u.initials: u.name for u in User.query.all()}
    active_users = User.query.filter_by(active=True).order_by(User.name).all()
    return render_template(
        "sample/detail.html", sample=sample,
        file_count=len(sample.files), total_size_gb=total_size_gb, users=users,
        active_users=active_users,
    )


# Direct (unescaped) characters in the Xcalibur sequence file's modified UTF-7:
# letters, digits, space, the structural punctuation that appears literally, and
# the newline. Every other character (notably _ - = " \) is emitted as a base64 run.
_XCALIBUR_DIRECT = set(ascii_letters + digits + " ,:/.()\n")


def xcalibur_encode(text):
    """Encode text as the modified UTF-7 dialect Thermo Xcalibur sequence files use.

    Reproduces ExampleQueue.csv byte-for-byte: runs of non-direct characters are
    written as ``+<base64(UTF-16BE), '=' stripped>-``.
    """
    out = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] in _XCALIBUR_DIRECT:
            out.append(text[i])
            i += 1
        else:
            j = i
            while j < n and text[j] not in _XCALIBUR_DIRECT:
                j += 1
            run = text[i:j].encode("utf-16-be")
            out.append("+" + base64.b64encode(run).decode("ascii").rstrip("=") + "-")
            i = j
    return "".join(out).encode("ascii")


def build_batch_csv(names, day, project_code, experiment_code, sample_code, sample_name):
    """Build a Thermo Xcalibur sequence CSV (UTF-7 bytes) for the given file names."""
    path = f"D:\\Data\\{day:%Y}\\{day:%y}{day:%m}\\{day:%y%m%d}"
    comment = f"{project_code}_{experiment_code}_{sample_code} - {sample_name}"
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["Bracket Type=4", "", "", "", "", ""])
    writer.writerow(["File Name", "Path", "Instrument Method", "Position", "Inj Vol", "Comment"])
    for name in names:
        writer.writerow([name, path, "", "", "", comment])
    return xcalibur_encode(buf.getvalue())


def _batch_payload(project_code, experiment_code, code):
    """Validate a batch POST and return (sample, names, instrument, user, day).

    Aborts with 400 if the payload is missing fields or malformed.
    """
    sample = db.get_or_404(MassSpecSample, (project_code, experiment_code, code))
    data = request.get_json(silent=True) or {}
    names = data.get("names")
    inst = (data.get("instrument_initial") or "").strip()
    user = (data.get("user_initials") or "").strip()
    date_str = (data.get("date") or "").strip()
    if not isinstance(names, list) or not names or not all(isinstance(n, str) and n for n in names):
        abort(400, "names must be a non-empty list of strings")
    if not inst or not user:
        abort(400, "instrument_initial and user_initials are required")
    try:
        day = datetime.strptime(date_str, "%Y%m%d").date()
    except ValueError:
        abort(400, "date must be YYYYMMDD")
    return sample, names, inst, user, day


@app.route(
    "/projects/<project_code>/experiments/<experiment_code>/samples/<code>/batch/csv",
    methods=["POST"],
)
def sample_batch_csv(project_code, experiment_code, code):
    sample, names, _inst, _user, day = _batch_payload(project_code, experiment_code, code)
    csv_bytes = build_batch_csv(
        names, day, project_code, experiment_code, code, sample.name
    )
    resp = Response(csv_bytes, mimetype="text/csv")
    resp.headers["Content-Disposition"] = (
        f'attachment; filename="{project_code}_{experiment_code}_{code}_queue.csv"'
    )
    return resp


@app.route(
    "/projects/<project_code>/experiments/<experiment_code>/samples/<code>/batch/queue",
    methods=["POST"],
)
def sample_batch_queue(project_code, experiment_code, code):
    _sample, names, inst, user, day = _batch_payload(project_code, experiment_code, code)
    for name in names:
        db.session.add(MassSpecAcquisition(
            project_code=project_code,
            experiment_code=experiment_code,
            sample_code=code,
            location="QUEUED",
            filename=name,
            instrument_initial=inst,
            user_initials=user,
            date=day,
        ))
    db.session.commit()
    return jsonify({"count": len(names)})


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
        MassSpecAcquisition.query.options(
            joinedload(MassSpecAcquisition.sample).joinedload(MassSpecSample.experiment).joinedload(Experiment.project)
        )
        .order_by(MassSpecAcquisition.date.desc(), MassSpecAcquisition.filename)
        .all()
    )
    users = {u.initials: u.name for u in User.query.all()}
    return render_template("file/list.html", files=files, users=users)


@app.route("/projects/<project_code>/experiments/<experiment_code>/samples/<code>/db-files/new", methods=["GET", "POST"])
def file_create(project_code, experiment_code, code):
    sample = db.get_or_404(MassSpecSample, (project_code, experiment_code, code))
    form = MassSpecAcquisitionForm()
    if form.validate_on_submit():
        f = MassSpecAcquisition(
            project_code=project_code, experiment_code=experiment_code, sample_code=code
        )
        form.populate_obj(f)
        f.meta = form.meta_json.data
        if f.size_bytes is not None:
            f.size_bytes = int(f.size_bytes * 1e9)
        db.session.add(f)
        db.session.commit()
        flash("File record created.", "success")
        return redirect(url_for("sample_edit", project_code=project_code, experiment_code=experiment_code, code=code))
    return render_template("file/form.html", form=form, file=None, sample=sample)


@app.route("/files/<int:id>")
def file_detail(id):
    f = db.get_or_404(MassSpecAcquisition, id)
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
    f = db.get_or_404(MassSpecAcquisition, id)
    form = MassSpecAcquisitionEditForm()
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
    f = db.get_or_404(MassSpecAcquisition, id)
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
            MassSpecAcquisition.instrument_initial,
            MassSpecAcquisition.date,
            Project.code.label("project_code"),
            func.sum(MassSpecAcquisition.size_bytes).label("total_bytes"),
        )
        .join(MassSpecAcquisition.sample)
        .join(MassSpecSample.experiment)
        .join(Experiment.project)
        .filter(MassSpecAcquisition.instrument_initial.isnot(None))
        .filter(MassSpecAcquisition.date.isnot(None))
        .group_by(MassSpecAcquisition.instrument_initial, MassSpecAcquisition.date, Project.code)
        .order_by(MassSpecAcquisition.instrument_initial, MassSpecAcquisition.date)
        .all()
    )
    instruments = defaultdict(list)
    for row in rows:
        instruments[row.instrument_initial].append({
            "date": row.date.isoformat(),
            "project": row.project_code,
            "gb": round((row.total_bytes or 0) / 1e9, 4),
        })
    return render_template("instrument_usage.html", data=dict(instruments))


@app.route("/disk-usage")
def disk_usage():
    rows = (
        db.session.query(
            Project.code.label("project_code"),
            func.sum(MassSpecAcquisition.size_bytes).label("total_bytes"),
        )
        .join(MassSpecAcquisition.sample)
        .join(MassSpecSample.experiment)
        .join(Experiment.project)
        .group_by(Project.code)
        .order_by(func.sum(MassSpecAcquisition.size_bytes).desc())
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
