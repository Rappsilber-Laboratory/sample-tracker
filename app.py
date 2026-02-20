import os
import sqlite3

import click
from flask import Flask, flash, redirect, render_template, request, url_for
from flask_wtf import CSRFProtect

from config import Config
from fileInfoScript import SpectraAddressBook
from forms import CellLineForm, ExperimentForm, ProjectForm, SampleForm, SpeciesForm
from models import (
    CellLine,
    CrosslinkSample,
    Experiment,
    IdentificationSample,
    Project,
    Sample,
    Species,
    db,
)

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)
CSRFProtect(app)


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
    conn.executescript(schema_sql)
    conn.close()
    click.echo(f"Initialized database at {db_path}")


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    counts = {
        "projects": db.session.query(Project).count(),
        "experiments": db.session.query(Experiment).count(),
        "samples": db.session.query(Sample).count(),
        "species": db.session.query(Species).count(),
        "cell_lines": db.session.query(CellLine).count(),
    }
    return render_template("index.html", counts=counts)


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
    return render_template(
        "project/list.html", projects=projects, show_archived=show_archived
    )


@app.route("/projects/<int:id>")
def project_detail(id):
    project = db.get_or_404(Project, id)
    experiments = (
        Experiment.query.filter_by(project_id=id).order_by(Experiment.name).all()
    )
    return render_template(
        "project/detail.html", project=project, experiments=experiments
    )


@app.route("/projects/new", methods=["GET", "POST"])
def project_create():
    form = ProjectForm()
    if form.validate_on_submit():
        project = Project()
        form.populate_obj(project)
        db.session.add(project)
        db.session.commit()
        flash("Project created.", "success")
        return redirect(url_for("project_list"))
    return render_template("project/form.html", form=form, project=None)


@app.route("/projects/<int:id>/edit", methods=["GET", "POST"])
def project_edit(id):
    project = db.get_or_404(Project, id)
    form = ProjectForm(obj=project)
    if form.validate_on_submit():
        form.populate_obj(project)
        db.session.commit()
        flash("Project updated.", "success")
        return redirect(url_for("project_detail", id=project.id))
    return render_template("project/form.html", form=form, project=project)


# ---------------------------------------------------------------------------
# Experiments
# ---------------------------------------------------------------------------
def _active_project_choices():
    projects = Project.query.filter_by(active=True).order_by(Project.name).all()
    return [(p.id, f"{p.code} — {p.name}") for p in projects]


@app.route("/experiments")
def experiment_list():
    experiments = (
        Experiment.query.join(Project)
        .filter(Project.active == True)  # noqa: E712
        .order_by(Experiment.name)
        .all()
    )
    return render_template("experiment/list.html", experiments=experiments)


@app.route("/experiments/<int:id>")
def experiment_detail(id):
    experiment = db.get_or_404(Experiment, id)
    samples = Sample.query.filter_by(experiment_id=id).order_by(Sample.name).all()
    return render_template(
        "experiment/detail.html", experiment=experiment, samples=samples
    )


@app.route("/experiments/new", methods=["GET", "POST"])
def experiment_create():
    form = ExperimentForm()
    form.project_id.choices = _active_project_choices()
    if request.method == "GET" and request.args.get("project_id"):
        form.project_id.data = int(request.args["project_id"])
    if form.validate_on_submit():
        experiment = Experiment()
        form.populate_obj(experiment)
        db.session.add(experiment)
        db.session.commit()
        flash("Experiment created.", "success")
        return redirect(url_for("project_detail", id=experiment.project_id))
    return render_template("experiment/form.html", form=form, experiment=None)


@app.route("/experiments/<int:id>/edit", methods=["GET", "POST"])
def experiment_edit(id):
    experiment = db.get_or_404(Experiment, id)
    samples = Sample.query.filter_by(experiment_id=id).order_by(Sample.name).all()
    form = ExperimentForm(obj=experiment)
    form.project_id.choices = _active_project_choices()
    # Ensure current project appears even if archived
    if experiment.project_id not in [c[0] for c in form.project_id.choices]:
        p = experiment.project
        form.project_id.choices.append((p.id, f"{p.code} — {p.name}"))
    if form.validate_on_submit():
        form.populate_obj(experiment)
        db.session.commit()
        flash("Experiment updated.", "success")
        return redirect(url_for("experiment_edit", id=experiment.id))
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


@app.route("/species/<int:id>/edit", methods=["GET", "POST"])
def species_edit(id):
    species = db.get_or_404(Species, id)
    form = SpeciesForm(obj=species)
    if form.validate_on_submit():
        form.populate_obj(species)
        db.session.commit()
        flash("Species updated.", "success")
        return redirect(url_for("species_list"))
    return render_template("species/form.html", form=form, species=species)


# ---------------------------------------------------------------------------
# Cell Lines
# ---------------------------------------------------------------------------
def _species_choices():
    species = Species.query.order_by(Species.species_name).all()
    return [(0, "")] + [(s.id, s.species_name) for s in species]


@app.route("/cell-lines")
def cell_line_list():
    cell_lines = CellLine.query.order_by(CellLine.cell_line_name).all()
    return render_template("cell_line/list.html", cell_lines=cell_lines)


@app.route("/cell-lines/new", methods=["GET", "POST"])
def cell_line_create():
    form = CellLineForm()
    form.species_id.choices = _species_choices()
    if form.validate_on_submit():
        cl = CellLine()
        form.populate_obj(cl)
        if cl.species_id == 0:
            cl.species_id = None
        db.session.add(cl)
        db.session.commit()
        flash("Cell line created.", "success")
        return redirect(url_for("cell_line_list"))
    return render_template("cell_line/form.html", form=form, cell_line=None)


@app.route("/cell-lines/<int:id>/edit", methods=["GET", "POST"])
def cell_line_edit(id):
    cl = db.get_or_404(CellLine, id)
    form = CellLineForm(obj=cl)
    form.species_id.choices = _species_choices()
    if not cl.species_id:
        form.species_id.data = 0
    if form.validate_on_submit():
        form.populate_obj(cl)
        if cl.species_id == 0:
            cl.species_id = None
        db.session.commit()
        flash("Cell line updated.", "success")
        return redirect(url_for("cell_line_list"))
    return render_template("cell_line/form.html", form=form, cell_line=cl)


# ---------------------------------------------------------------------------
# Samples
# ---------------------------------------------------------------------------
def _experiment_choices():
    experiments = (
        Experiment.query.join(Project)
        .filter(Project.active == True)  # noqa: E712
        .order_by(Experiment.name)
        .all()
    )
    return [(e.id, f"{e.project.code} — {e.name}") for e in experiments]


def _species_multi_choices():
    return [(s.id, s.species_name) for s in Species.query.order_by(Species.species_name).all()]


def _cell_line_multi_choices():
    return [
        (cl.id, cl.cell_line_name)
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
        Sample.query.join(Experiment)
        .join(Project)
        .filter(Project.active == True)  # noqa: E712
        .order_by(Sample.name)
        .all()
    )
    return render_template("sample/list.html", samples=samples)


@app.route("/samples/new", methods=["GET", "POST"])
def sample_create():
    form = SampleForm()
    form.experiment_id.choices = _experiment_choices()
    form.species_ids.choices = _species_multi_choices()
    form.cell_line_ids.choices = _cell_line_multi_choices()
    if request.method == "GET" and request.args.get("experiment_id"):
        form.experiment_id.data = int(request.args["experiment_id"])
    if form.validate_on_submit():
        is_crosslinked = form.crosslinked_sample.data
        if is_crosslinked:
            sample = CrosslinkSample()
        else:
            sample = IdentificationSample()
        form.populate_obj(sample)
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
            CellLine.id.in_(form.cell_line_ids.data)
        ).all()
        db.session.commit()
        flash("Sample created.", "success")
        return redirect(url_for("experiment_detail", id=sample.experiment_id))
    return render_template("sample/form.html", form=form, sample=None)


@app.route("/samples/<int:id>/edit", methods=["GET", "POST"])
def sample_edit(id):
    sample = db.get_or_404(Sample, id)
    form = SampleForm(obj=sample)
    form.experiment_id.choices = _experiment_choices()
    # Ensure current experiment appears even if its project is archived
    if sample.experiment_id not in [c[0] for c in form.experiment_id.choices]:
        e = sample.experiment
        form.experiment_id.choices.append((e.id, f"{e.project.code} — {e.name}"))
    form.species_ids.choices = _species_multi_choices()
    form.cell_line_ids.choices = _cell_line_multi_choices()

    if request.method == "GET":
        form.crosslinked_sample.data = bool(sample.crosslinked_sample)
        form.quantitation.data = bool(sample.quantitation)
        form.species_ids.data = [s.id for s in sample.species_list]
        form.cell_line_ids.data = [cl.id for cl in sample.cell_lines]

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
            CellLine.id.in_(form.cell_line_ids.data)
        ).all()
        _nullify_crosslink_fields(sample)
        db.session.commit()
        flash("Sample updated.", "success")
        return redirect(url_for("experiment_detail", id=sample.experiment_id))
    return render_template("sample/form.html", form=form, sample=sample)


@app.route("/samples/<int:id>/files")
def sample_files(id):
    sample = db.get_or_404(Sample, id)
    files = []
    error = None
    path = sample.file_name_root
    if not path:
        error = "No file path is set for this sample."
    else:
        try:
            files = [(name, loc, size / 1024 ** 3)
                     for name, loc, size in SpectraAddressBook(path).collect()]
        except Exception as e:
            error = f"Error scanning path: {e}"
    return render_template("sample/files.html", sample=sample, files=files, error=error)
