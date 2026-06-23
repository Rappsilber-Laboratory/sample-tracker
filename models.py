from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# Junction tables
cell_line_virus = db.Table(
    "cell_line_virus",
    db.Column("cellosaurus_id", db.Text, db.ForeignKey("cell_line.cellosaurus_id"), primary_key=True),
    db.Column("virus_id", db.Integer, db.ForeignKey("virus.id"), primary_key=True),
)

sample_species = db.Table(
    "sample_species",
    db.Column("project_code", db.Text, primary_key=True),
    db.Column("experiment_code", db.Text, primary_key=True),
    db.Column("sample_code", db.Text, primary_key=True),
    db.Column("species_id", db.Integer, db.ForeignKey("species.id"), primary_key=True),
    db.ForeignKeyConstraint(
        ["project_code", "experiment_code", "sample_code"],
        ["mass_spec_sample.project_code", "mass_spec_sample.experiment_code", "mass_spec_sample.code"],
    ),
)

sample_cell_line = db.Table(
    "sample_cell_line",
    db.Column("project_code", db.Text, primary_key=True),
    db.Column("experiment_code", db.Text, primary_key=True),
    db.Column("sample_code", db.Text, primary_key=True),
    db.Column("cellosaurus_id", db.Text, db.ForeignKey("cell_line.cellosaurus_id"), primary_key=True),
    db.ForeignKeyConstraint(
        ["project_code", "experiment_code", "sample_code"],
        ["mass_spec_sample.project_code", "mass_spec_sample.experiment_code", "mass_spec_sample.code"],
    ),
)


class Project(db.Model):
    __tablename__ = "project"

    code = db.Column(db.Text, primary_key=True, nullable=False)
    name = db.Column(db.Text, nullable=False)
    description = db.Column(db.Text, nullable=False)
    user_initials = db.Column(db.Text, nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)

    experiments = db.relationship("Experiment", back_populates="project")


class Experiment(db.Model):
    __tablename__ = "experiment"

    project_code = db.Column(db.Text, db.ForeignKey("project.code"), primary_key=True, nullable=False)
    code = db.Column(db.Text, primary_key=True, nullable=False)
    name = db.Column(db.Text, nullable=False)
    description = db.Column(db.Text, nullable=False)
    user_initials = db.Column(db.Text, nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)

    project = db.relationship("Project", back_populates="experiments")
    samples = db.relationship("MassSpecSample", back_populates="experiment")


class Species(db.Model):
    __tablename__ = "species"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    species_name = db.Column(db.Text, nullable=False)
    species_taxon = db.Column(db.Text, nullable=False)

    cell_lines = db.relationship("CellLine", back_populates="species")


class Virus(db.Model):
    __tablename__ = "virus"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.Text, nullable=False)
    species_id = db.Column(db.Integer, db.ForeignKey("species.id"))
    variant = db.Column(db.Text)

    species = db.relationship("Species", backref="viruses")


class CellLine(db.Model):
    __tablename__ = "cell_line"

    cellosaurus_id = db.Column(db.Text, primary_key=True, nullable=False)
    cell_line_name = db.Column(db.Text, nullable=False)
    species_id = db.Column(db.Integer, db.ForeignKey("species.id"), nullable=False)

    species = db.relationship("Species", back_populates="cell_lines")
    viruses = db.relationship("Virus", secondary=cell_line_virus, backref="cell_lines")


class MassSpecSample(db.Model):
    __tablename__ = "mass_spec_sample"

    project_code = db.Column(db.Text, primary_key=True, nullable=False)
    experiment_code = db.Column(db.Text, primary_key=True, nullable=False)
    code = db.Column(db.Text, primary_key=True, nullable=False)
    name = db.Column(db.Text, nullable=False)
    description = db.Column(db.Text, nullable=False)
    user_initials = db.Column(db.Text, nullable=False)
    disease = db.Column(db.Text)
    phenotype = db.Column(db.Text)
    isotope_labeling_channel = db.Column(db.Text)
    chemical_labelling = db.Column(db.Text)
    tissue = db.Column(db.Text)
    organism_age = db.Column(db.Float)
    organism_age_unit = db.Column(db.Text)
    organism_sex = db.Column(db.Text)
    enrichment_process = db.Column(db.Text)
    replicate = db.Column(db.Text)
    synthetic_peptide = db.Column(db.Text)
    digestion = db.Column(db.Text)
    protein_isolation_or_fractionation = db.Column(db.Text)
    crosslinked_sample = db.Column(db.Integer, nullable=False, default=0)
    quantitation = db.Column(db.Integer, default=0)
    quantitation_scheme = db.Column(db.Text)
    quantitation_method = db.Column(db.Text)

    # CrosslinkSample columns (crosslinked_sample = 1)
    crosslinker = db.Column(db.Text)
    crosslinking_type = db.Column(db.Text)
    protein_or_cell_concentration = db.Column(db.Float)
    protein_or_cell_concentration_unit = db.Column(db.Text)
    crosslinker_or_compound_concentration = db.Column(db.Float)
    crosslinker_or_compound_concentration_unit = db.Column(db.Text)
    organic_solvent_concentration = db.Column(db.Float)
    organic_solvent_concentration_unit = db.Column(db.Text)
    reaction_temperature_in_celsius = db.Column(db.Float)
    reaction_time_in_minutes = db.Column(db.Float)
    quenching_reagent = db.Column(db.Text)
    uv_source = db.Column(db.Text)
    uv_time_in_seconds = db.Column(db.Float)
    uv_wavelength_in_nanometers = db.Column(db.Float)

    # IdentificationSample columns (crosslinked_sample = 0)
    peptide_level_fraction = db.Column(db.Text)

    __table_args__ = (
        db.ForeignKeyConstraint(
            ["project_code", "experiment_code"],
            ["experiment.project_code", "experiment.code"],
        ),
    )

    # Polymorphic identity
    __mapper_args__ = {
        "polymorphic_on": crosslinked_sample,
        "polymorphic_identity": -1,
    }

    experiment = db.relationship("Experiment", back_populates="samples")
    species_list = db.relationship(
        "Species", secondary=sample_species, backref="samples"
    )
    cell_lines = db.relationship(
        "CellLine", secondary=sample_cell_line, backref="samples"
    )


class CrosslinkSample(MassSpecSample):
    __mapper_args__ = {"polymorphic_identity": 1}


class IdentificationSample(MassSpecSample):
    __mapper_args__ = {"polymorphic_identity": 0}


class User(db.Model):
    __tablename__ = "user"

    initials = db.Column(db.Text, primary_key=True)
    name = db.Column(db.Text, nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)


class AcquiredFile(db.Model):
    __tablename__ = "acquired_file"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    project_code = db.Column(db.Text, nullable=True)
    experiment_code = db.Column(db.Text, nullable=True)
    sample_code = db.Column(db.Text, nullable=True)
    location = db.Column(db.Text)
    filename = db.Column(db.Text)
    size_bytes = db.Column(db.Integer)
    instrument_initial = db.Column(db.Text)
    file_date = db.Column(db.Date)
    user_initials = db.Column(db.Text)
    scan_count = db.Column(db.Integer)
    meta = db.Column(db.Text)

    __table_args__ = (
        db.ForeignKeyConstraint(
            ["project_code", "experiment_code", "sample_code"],
            ["mass_spec_sample.project_code", "mass_spec_sample.experiment_code", "mass_spec_sample.code"],
        ),
    )

    sample = db.relationship(
        "MassSpecSample",
        backref=db.backref("acquired_files", order_by="desc(AcquiredFile.file_date), AcquiredFile.filename"),
    )


class QueuedFile(db.Model):
    __tablename__ = "queued_file"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    project_code = db.Column(db.Text, nullable=True)
    experiment_code = db.Column(db.Text, nullable=True)
    sample_code = db.Column(db.Text, nullable=True)
    instrument_initial = db.Column(db.Text)
    user_initials = db.Column(db.Text)
    date_queued = db.Column(db.Date)
    daily_counter = db.Column(db.Integer)
    postfix = db.Column(db.Text)

    __table_args__ = (
        db.ForeignKeyConstraint(
            ["project_code", "experiment_code", "sample_code"],
            ["mass_spec_sample.project_code", "mass_spec_sample.experiment_code", "mass_spec_sample.code"],
        ),
    )

    sample = db.relationship(
        "MassSpecSample",
        backref=db.backref("queued_files", order_by="desc(QueuedFile.date_queued)"),
    )
