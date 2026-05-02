import os
import sqlite3

import click
from flask import Flask, flash, jsonify, redirect, render_template, request, url_for
from flask_wtf import CSRFProtect
from sqlalchemy.orm import joinedload

from config import Config
from fileInfoScript import SpectraAddressBook
from forms import (
    CellLineForm,
    ExperimentForm,
    FileEditForm,
    FileForm,
    ProjectForm,
    SampleForm,
    SpeciesForm,
    UserForm,
    VirusForm,
)
from models import (
    CellLine,
    CrosslinkSample,
    Experiment,
    File,
    IdentificationSample,
    Project,
    Sample,
    Species,
    User,
    Virus,
    db,
)

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)
CSRFProtect(app)


@app.context_processor
def inject_nav_counts():
    try:
        return {
            "nav_counts": {
                "projects": db.session.query(Project).count(),
                "experiments": db.session.query(Experiment).count(),
                "samples": db.session.query(Sample).count(),
                "species": db.session.query(Species).count(),
                "cell_lines": db.session.query(CellLine).count(),
                "viruses": db.session.query(Virus).count(),
                "files": db.session.query(File).count(),
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
        "viruses": db.session.query(Virus).count(),
        "users": db.session.query(User).count(),
        "files": db.session.query(File).count(),
    }
    return render_template("index.html", counts=counts)


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
        return {"id": s.id, "name": s.name, "level": "sample", "total_bytes": total,
                "url": url_for("sample_detail", id=s.id), "children": children}

    def experiment_node(e):
        children = sorted([sample_node(s) for s in e.samples], key=lambda n: n["total_bytes"], reverse=True)
        total = sum(c["total_bytes"] for c in children)
        return {"id": e.id, "name": e.name, "level": "experiment", "total_bytes": total,
                "url": url_for("experiment_detail", id=e.id), "children": children}

    def project_node(p):
        children = sorted([experiment_node(e) for e in p.experiments], key=lambda n: n["total_bytes"], reverse=True)
        total = sum(c["total_bytes"] for c in children)
        return {"id": p.id, "name": p.name or p.code, "level": "project", "total_bytes": total,
                "url": url_for("project_detail", id=p.id), "children": children}

    projects = Project.query.filter_by(active=True).all()
    project_nodes = sorted([project_node(p) for p in projects], key=lambda n: n["total_bytes"], reverse=True)
    tree = {
        "name": "File Tracker",
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
        p.id: Experiment.query.filter_by(project_id=p.id).count()
        for p in projects
    }
    projects = sorted(projects, key=lambda p: exp_counts[p.id], reverse=True)
    return render_template(
        "project/list.html", projects=projects, show_archived=show_archived,
        users=users, exp_counts=exp_counts
    )


@app.route("/projects/<int:id>/toggle-active", methods=["POST"])
def project_toggle_active(id):
    project = db.get_or_404(Project, id)
    project.active = not project.active
    db.session.commit()
    show_archived = request.args.get("show_archived", "0")
    return redirect(url_for("project_list", show_archived=show_archived))


@app.route("/projects/<int:id>")
def project_detail(id):
    project = db.get_or_404(Project, id)
    experiments = (
        Experiment.query.filter_by(project_id=id).order_by(Experiment.name).all()
    )
    contact_user = (
        User.query.filter_by(initials=project.contact_person_initials).first()
        if project.contact_person_initials
        else None
    )
    contact_name = contact_user.name if contact_user else project.contact_person_initials
    samples = (
        Sample.query.join(Experiment)
        .options(joinedload(Sample.experiment))
        .filter(Experiment.project_id == id)
        .order_by(Sample.name).all()
    )
    files = (
        File.query.join(Sample).join(Experiment)
        .options(joinedload(File.sample).joinedload(Sample.experiment).joinedload(Experiment.project))
        .filter(Experiment.project_id == id)
        .order_by(File.date.desc(), File.filename).all()
    )
    total_size_gb = sum(f.size_bytes or 0 for f in files) / (1024 ** 3)
    return render_template(
        "project/detail.html", project=project, experiments=experiments,
        contact_name=contact_name, samples=samples, files=files,
        experiment_count=len(experiments), sample_count=len(samples),
        file_count=len(files), total_size_gb=total_size_gb,
    )


@app.route("/projects/new", methods=["GET", "POST"])
def project_create():
    form = ProjectForm()
    form.contact_person_initials.choices = _user_initials_choices()
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
    form.contact_person_initials.choices = _user_initials_choices()
    if form.validate_on_submit():
        form.populate_obj(project)
        db.session.commit()
        flash("Project updated.", "success")
        return redirect(url_for("project_detail", id=project.id))
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
    return [(p.id, f"{p.code} — {p.name}") for p in projects]


@app.route("/experiments")
def experiment_list():
    experiments = (
        Experiment.query.join(Project)
        .options(joinedload(Experiment.project))
        .filter(Project.active == True)  # noqa: E712
        .order_by(Experiment.name)
        .all()
    )
    return render_template("experiment/list.html", experiments=experiments)


@app.route("/experiments/<int:id>")
def experiment_detail(id):
    experiment = db.get_or_404(Experiment, id)
    samples = Sample.query.filter_by(experiment_id=id).order_by(Sample.name).all()
    files = (
        File.query.join(Sample)
        .options(joinedload(File.sample).joinedload(Sample.experiment).joinedload(Experiment.project))
        .filter(Sample.experiment_id == id)
        .order_by(File.date.desc(), File.filename).all()
    )
    total_size_gb = sum(f.size_bytes or 0 for f in files) / (1024 ** 3)
    return render_template(
        "experiment/detail.html", experiment=experiment, samples=samples, files=files,
        sample_count=len(samples), file_count=len(files), total_size_gb=total_size_gb,
    )


@app.route("/experiments/new", methods=["GET", "POST"])
def experiment_create():
    form = ExperimentForm()
    form.project_id.choices = _active_project_choices()
    form.contact_person.choices = _user_name_choices()
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
    form.contact_person.choices = _user_name_choices()
    # Ensure current project appears even if archived
    if experiment.project_id not in [c[0] for c in form.project_id.choices]:
        p = experiment.project
        form.project_id.choices.append((p.id, f"{p.code} — {p.name}"))
    if form.validate_on_submit():
        form.populate_obj(experiment)
        db.session.commit()
        flash("Experiment updated.", "success")
        return redirect(url_for("experiment_detail", id=experiment.id))
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
        if cl.species_id == 0:
            cl.species_id = None
        db.session.add(cl)
        cl.viruses = Virus.query.filter(Virus.id.in_(form.virus_ids.data)).all()
        db.session.commit()
        flash("Cell line created.", "success")
        return redirect(url_for("cell_line_list"))
    return render_template("cell_line/form.html", form=form, cell_line=None)


@app.route("/cell-lines/<int:id>")
def cell_line_detail(id):
    cl = db.get_or_404(CellLine, id)
    return render_template("cell_line/detail.html", cell_line=cl)


@app.route("/cell-lines/<int:id>/edit", methods=["GET", "POST"])
def cell_line_edit(id):
    cl = db.get_or_404(CellLine, id)
    form = CellLineForm(obj=cl)
    form.species_id.choices = _species_choices()
    form.virus_ids.choices = _virus_multi_choices()
    if not cl.species_id:
        form.species_id.data = 0
    if request.method == "GET":
        form.virus_ids.data = [v.id for v in cl.viruses]
    if form.validate_on_submit():
        form.populate_obj(cl)
        if cl.species_id == 0:
            cl.species_id = None
        cl.viruses = Virus.query.filter(Virus.id.in_(form.virus_ids.data)).all()
        db.session.commit()
        flash("Cell line updated.", "success")
        return redirect(url_for("cell_line_detail", id=cl.id))
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


@app.route("/users/<int:id>")
def user_detail(id):
    user = db.get_or_404(User, id)
    return render_template("user/detail.html", user=user)


@app.route("/users/<int:id>/edit", methods=["GET", "POST"])
def user_edit(id):
    user = db.get_or_404(User, id)
    form = UserForm(obj=user)
    if form.validate_on_submit():
        form.populate_obj(user)
        db.session.commit()
        flash("User updated.", "success")
        return redirect(url_for("user_detail", id=user.id))
    return render_template("user/form.html", form=form, user=user)


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
        .options(joinedload(Sample.experiment).joinedload(Experiment.project))
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


@app.route("/samples/<int:id>")
def sample_detail(id):
    sample = db.get_or_404(Sample, id)
    total_size_gb = sum(f.size_bytes or 0 for f in sample.files) / (1024 ** 3)
    return render_template(
        "sample/detail.html", sample=sample,
        file_count=len(sample.files), total_size_gb=total_size_gb,
    )


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
        return redirect(url_for("sample_detail", id=sample.id))
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


# ---------------------------------------------------------------------------
# Files (DB records)
# ---------------------------------------------------------------------------
@app.route("/files")
def file_list():
    files = (
        File.query.options(
            joinedload(File.sample).joinedload(Sample.experiment).joinedload(Experiment.project)
        )
        .order_by(File.date.desc(), File.filename)
        .all()
    )
    return render_template("file/list.html", files=files)


@app.route("/samples/<int:sample_id>/db-files/new", methods=["GET", "POST"])
def file_create(sample_id):
    sample = db.get_or_404(Sample, sample_id)
    form = FileForm()
    if form.validate_on_submit():
        f = File(sample_id=sample_id)
        form.populate_obj(f)
        f.meta = form.meta_json.data
        if f.size_bytes is not None:
            f.size_bytes = f.size_bytes * 1e9
        db.session.add(f)
        db.session.commit()
        flash("File record created.", "success")
        return redirect(url_for("sample_edit", id=sample_id))
    return render_template("file/form.html", form=form, file=None, sample=sample)


@app.route("/files/<int:id>")
def file_detail(id):
    f = db.get_or_404(File, id)
    return render_template("file/detail.html", file=f)


def _file_edit_tree():
    experiments = Experiment.query.order_by(Experiment.name).all()
    samples = Sample.query.order_by(Sample.name).all()
    return {
        "experiments": [
            {"id": e.id, "project_id": e.project_id,
             "label": (f"{e.code} — {e.name}" if e.code else e.name)}
            for e in experiments
        ],
        "samples": [
            {"id": s.id, "experiment_id": s.experiment_id,
             "label": (f"{s.code} — {s.name}" if s.code else s.name)}
            for s in samples
        ],
    }


@app.route("/files/<int:id>/edit", methods=["GET", "POST"])
def file_edit(id):
    f = db.get_or_404(File, id)
    form = FileEditForm()
    form.project_id.choices = [("", "—")] + [
        (p.id, f"{p.code} — {p.name}")
        for p in Project.query.order_by(Project.name).all()
    ]
    # experiment/sample option lists are populated by JS from the embedded tree;
    # the server-side choices only need to contain the currently-selected value
    # so WTForms validates the POST.
    form.experiment_id.choices = [("", "—")]
    form.sample_id.choices = [("", "—")]
    if request.method == "GET" and f.sample is not None:
        e = f.sample.experiment
        form.experiment_id.choices.append(
            (e.id, f"{e.code} — {e.name}" if e.code else e.name)
        )
        form.sample_id.choices.append(
            (f.sample.id, f"{f.sample.code} — {f.sample.name}" if f.sample.code else f.sample.name)
        )
        form.project_id.data = e.project_id
        form.experiment_id.data = e.id
        form.sample_id.data = f.sample.id
    if form.validate_on_submit():
        # The server only needs sample_id; project/experiment selects are pure
        # UX scaffolding handled client-side. Accept whatever sample id the
        # client posted and let the FK constraint reject anything bogus.
        posted_sample_id = request.form.get("sample_id") or None
        f.sample_id = int(posted_sample_id) if posted_sample_id else None
        db.session.commit()
        flash("File association updated.", "success")
        return redirect(url_for("file_detail", id=f.id))
    return render_template("file/edit.html", form=form, file=f, tree=_file_edit_tree())


@app.route("/files/<int:id>/delete", methods=["POST"])
def file_delete(id):
    f = db.get_or_404(File, id)
    sample_id = f.sample_id
    db.session.delete(f)
    db.session.commit()
    flash("File record deleted.", "success")
    return redirect(url_for("sample_detail", id=sample_id))
