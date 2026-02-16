from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# Junction tables
sample_species = db.Table(
    "sample_species",
    db.Column("sample_id", db.Integer, db.ForeignKey("sample.id"), primary_key=True),
    db.Column("species_id", db.Integer, db.ForeignKey("species.id"), primary_key=True),
)

sample_cell_line = db.Table(
    "sample_cell_line",
    db.Column("sample_id", db.Integer, db.ForeignKey("sample.id"), primary_key=True),
    db.Column(
        "cell_line_id", db.Integer, db.ForeignKey("cell_line.id"), primary_key=True
    ),
)


class Project(db.Model):
    __tablename__ = "project"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    code = db.Column(db.Text, nullable=False)
    name = db.Column(db.Text, nullable=False)
    description = db.Column(db.Text)
    contact_person = db.Column(db.Text)
    active = db.Column(db.Boolean, nullable=False, default=True)

    experiments = db.relationship("Experiment", back_populates="project")


class Experiment(db.Model):
    __tablename__ = "experiment"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), nullable=False)
    name = db.Column(db.Text, nullable=False)
    description = db.Column(db.Text)
    contact_person = db.Column(db.Text)

    project = db.relationship("Project", back_populates="experiments")
    samples = db.relationship("Sample", back_populates="experiment")


class Species(db.Model):
    __tablename__ = "species"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    species_name = db.Column(db.Text, nullable=False)
    species_taxon = db.Column(db.Text)

    cell_lines = db.relationship("CellLine", back_populates="species")


class CellLine(db.Model):
    __tablename__ = "cell_line"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    cell_line_name = db.Column(db.Text, nullable=False)
    cell_line_code = db.Column(db.Text)
    species_id = db.Column(db.Integer, db.ForeignKey("species.id"))

    species = db.relationship("Species", back_populates="cell_lines")


class Sample(db.Model):
    __tablename__ = "sample"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    experiment_id = db.Column(
        db.Integer, db.ForeignKey("experiment.id"), nullable=False
    )
    name = db.Column(db.Text, nullable=False)
    comment = db.Column(db.Text)
    file_name_root = db.Column(db.Text)
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


class CrosslinkSample(Sample):
    __mapper_args__ = {"polymorphic_identity": 1}


class IdentificationSample(Sample):
    __mapper_args__ = {"polymorphic_identity": 0}
