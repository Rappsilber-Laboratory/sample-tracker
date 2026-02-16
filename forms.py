from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    FloatField,
    SelectField,
    SelectMultipleField,
    StringField,
    TextAreaField,
    widgets,
)
from wtforms.validators import DataRequired, Optional


class ProjectForm(FlaskForm):
    code = StringField("Code", validators=[DataRequired()])
    name = StringField("Name", validators=[DataRequired()])
    description = TextAreaField("Description", validators=[Optional()])
    contact_person = StringField("Contact Person", validators=[Optional()])
    active = BooleanField("Active", default=True)


class ExperimentForm(FlaskForm):
    project_id = SelectField("Project", coerce=int, validators=[DataRequired()])
    name = StringField("Name", validators=[DataRequired()])
    description = TextAreaField("Description", validators=[Optional()])
    contact_person = StringField("Contact Person", validators=[Optional()])


class SpeciesForm(FlaskForm):
    species_name = StringField("Species Name", validators=[DataRequired()])
    species_taxon = StringField("Species Taxon", validators=[Optional()])


class CellLineForm(FlaskForm):
    cell_line_name = StringField("Cell Line Name", validators=[DataRequired()])
    cell_line_code = StringField("Cell Line Code", validators=[Optional()])
    species_id = SelectField("Species", coerce=int, validators=[Optional()])


class MultiCheckboxField(SelectMultipleField):
    widget = widgets.ListWidget(prefix_label=False)
    option_widget = widgets.CheckboxInput()


class SampleForm(FlaskForm):
    experiment_id = SelectField("Experiment", coerce=int, validators=[DataRequired()])
    name = StringField("Name", validators=[DataRequired()])
    comment = TextAreaField("Comment", validators=[Optional()])
    file_name_root = StringField("File Name Root", validators=[Optional()])
    disease = StringField("Disease", validators=[Optional()])
    phenotype = StringField("Phenotype", validators=[Optional()])
    isotope_labeling_channel = StringField(
        "Isotope Labeling Channel", validators=[Optional()]
    )
    chemical_labelling = StringField("Chemical Labelling", validators=[Optional()])
    tissue = StringField("Tissue", validators=[Optional()])
    organism_age = FloatField("Organism Age", validators=[Optional()])
    organism_age_unit = StringField("Organism Age Unit", validators=[Optional()])
    organism_sex = StringField("Organism Sex", validators=[Optional()])
    enrichment_process = StringField("Enrichment Process", validators=[Optional()])
    replicate = StringField("Replicate", validators=[Optional()])
    synthetic_peptide = SelectField(
        "Synthetic Peptide",
        choices=[("", ""), ("yes", "Yes"), ("no", "No"), ("spiked in", "Spiked In")],
        validators=[Optional()],
    )
    digestion = StringField("Digestion", validators=[Optional()])
    protein_isolation_or_fractionation = StringField(
        "Protein Isolation / Fractionation", validators=[Optional()]
    )
    crosslinked_sample = BooleanField("Crosslinked Sample", default=False)
    quantitation = BooleanField("Quantitation", default=False)
    quantitation_scheme = StringField("Quantitation Scheme", validators=[Optional()])
    quantitation_method = SelectField(
        "Quantitation Method",
        choices=[
            ("", ""),
            ("LFQ", "LFQ"),
            ("isotope labelled MS1", "Isotope Labelled MS1"),
            ("Isobaric MS2", "Isobaric MS2"),
        ],
        validators=[Optional()],
    )

    # Crosslink fields
    crosslinker = StringField("Crosslinker", validators=[Optional()])
    crosslinking_type = SelectField(
        "Crosslinking Type",
        choices=[
            ("", ""),
            ("in cell", "In Cell"),
            ("in lysate", "In Lysate"),
            ("in solution", "In Solution"),
            ("compound", "Compound"),
        ],
        validators=[Optional()],
    )
    protein_or_cell_concentration = FloatField(
        "Protein/Cell Concentration", validators=[Optional()]
    )
    protein_or_cell_concentration_unit = StringField(
        "Protein/Cell Concentration Unit", validators=[Optional()]
    )
    crosslinker_or_compound_concentration = FloatField(
        "Crosslinker/Compound Concentration", validators=[Optional()]
    )
    crosslinker_or_compound_concentration_unit = StringField(
        "Crosslinker/Compound Concentration Unit", validators=[Optional()]
    )
    organic_solvent_concentration = FloatField(
        "Organic Solvent Concentration", validators=[Optional()]
    )
    organic_solvent_concentration_unit = StringField(
        "Organic Solvent Concentration Unit", validators=[Optional()]
    )
    reaction_temperature_in_celsius = FloatField(
        "Reaction Temperature (\u00b0C)", validators=[Optional()]
    )
    reaction_time_in_minutes = FloatField(
        "Reaction Time (min)", validators=[Optional()]
    )
    quenching_reagent = StringField("Quenching Reagent", validators=[Optional()])
    uv_source = StringField("UV Source", validators=[Optional()])
    uv_time_in_seconds = FloatField("UV Time (s)", validators=[Optional()])
    uv_wavelength_in_nanometers = FloatField(
        "UV Wavelength (nm)", validators=[Optional()]
    )

    # Identification fields
    peptide_level_fraction = StringField(
        "Peptide Level Fraction", validators=[Optional()]
    )

    # Many-to-many
    species_ids = MultiCheckboxField("Species", coerce=int)
    cell_line_ids = MultiCheckboxField("Cell Lines", coerce=int)
