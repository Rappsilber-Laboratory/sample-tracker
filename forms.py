from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    FloatField,
    IntegerField,
    SelectField,
    SelectMultipleField,
    StringField,
    TextAreaField,
    widgets,
)
from wtforms.validators import DataRequired, Optional, ValidationError


def no_underscores(form, field):
    if field.data and "_" in field.data:
        raise ValidationError("Code must not contain underscores.")


class ProjectForm(FlaskForm):
    code = StringField("Code", validators=[DataRequired(), no_underscores])
    name = StringField("Name", validators=[DataRequired()])
    description = TextAreaField("Description", validators=[DataRequired()])
    user_initials = SelectField("Contact Person", validators=[DataRequired()], coerce=str)
    active = BooleanField("Active", default=True)


class ExperimentForm(FlaskForm):
    project_code = SelectField("Project", coerce=str, validators=[DataRequired()])
    code = StringField("Code", validators=[DataRequired(), no_underscores])
    name = StringField("Name", validators=[DataRequired()])
    description = TextAreaField("Description", validators=[DataRequired()])
    user_initials = SelectField("Contact Person", validators=[DataRequired()], coerce=str)
    active = BooleanField("Active", default=True)


class SpeciesForm(FlaskForm):
    species_name = StringField("Species Name", validators=[DataRequired()])
    species_taxon = StringField("Species Taxon", validators=[DataRequired()])


class MultiCheckboxField(SelectMultipleField):
    widget = widgets.ListWidget(prefix_label=False)
    option_widget = widgets.CheckboxInput()


class VirusForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired()])
    species_id = SelectField("Species", coerce=int, validators=[Optional()])
    variant = StringField("Variant", validators=[Optional()])


class CellLineForm(FlaskForm):
    cellosaurus_id = StringField("Cellosaurus ID", validators=[DataRequired()])
    cell_line_name = StringField("Cell Line Name", validators=[DataRequired()])
    species_id = SelectField("Species", coerce=int, validators=[DataRequired()])
    virus_ids = MultiCheckboxField("Viruses", coerce=int)


class UserForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired()])
    initials = StringField("Initials", validators=[DataRequired()])
    active = BooleanField("Active", default=True)


class MassSpecSampleForm(FlaskForm):
    experiment_code = SelectField("Experiment", coerce=str, validators=[DataRequired()])
    code = StringField("Code", validators=[DataRequired(), no_underscores])
    name = StringField("Name", validators=[DataRequired()])
    description = TextAreaField("Description", validators=[DataRequired()])
    user_initials = SelectField("Contact Person", coerce=str, validators=[DataRequired()])
    disease = StringField("Disease", default="N/A", validators=[Optional()])
    phenotype = StringField("Phenotype", default="N/A", validators=[Optional()])
    isotope_labeling_channel = StringField(
        "Isotope Labeling Channel", default="N/A", validators=[Optional()]
    )
    chemical_labelling = StringField("Chemical Labelling", default="N/A", validators=[Optional()])
    tissue = StringField("Tissue", default="N/A", validators=[Optional()])
    organism_age = FloatField("Organism Age", validators=[Optional()])
    organism_age_unit = StringField("Organism Age Unit", default="N/A", validators=[Optional()])
    organism_sex = StringField("Organism Sex", default="N/A", validators=[Optional()])
    enrichment_process = StringField("Enrichment Process", default="N/A", validators=[Optional()])
    replicate = StringField("Replicate", default="N/A", validators=[Optional()])
    synthetic_peptide = SelectField(
        "Synthetic Peptide",
        default="no",
        choices=[("", ""), ("yes", "Yes"), ("no", "No"), ("spiked in", "Spiked In")],
        validators=[Optional()],
    )
    digestion = StringField("Digestion", default="N/A", validators=[Optional()])
    protein_isolation_or_fractionation = StringField(
        "Protein Isolation / Fractionation", default="N/A", validators=[Optional()]
    )
    crosslinked_sample = BooleanField("Crosslinked Sample", default=False)
    quantitation = BooleanField("Quantitation", default=False)
    quantitation_scheme = StringField("Quantitation Scheme", default="N/A", validators=[Optional()])
    quantitation_method = SelectField(
        "Quantitation Method",
        default="",
        choices=[
            ("", "N/A"),
            ("LFQ", "LFQ"),
            ("isotope labelled MS1", "Isotope Labelled MS1"),
            ("Isobaric MS2", "Isobaric MS2"),
        ],
        validators=[Optional()],
    )

    # Crosslink fields
    crosslinker = StringField("Crosslinker", default="N/A", validators=[Optional()])
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
        "Protein or Cell Concentration", validators=[Optional()]
    )
    protein_or_cell_concentration_unit = StringField(
        "Protein or Cell Concentration Unit", default="N/A", validators=[Optional()]
    )
    crosslinker_or_compound_concentration = FloatField(
        "Crosslinker or Compound Concentration", validators=[Optional()]
    )
    crosslinker_or_compound_concentration_unit = StringField(
        "Crosslinker or Compound Concentration Unit", default="N/A", validators=[Optional()]
    )
    organic_solvent_concentration = FloatField(
        "Organic Solvent Concentration", validators=[Optional()]
    )
    organic_solvent_concentration_unit = StringField(
        "Organic Solvent Concentration Unit", default="N/A", validators=[Optional()]
    )
    reaction_temperature_in_celsius = FloatField(
        "Reaction Temperature (°C)", validators=[Optional()]
    )
    reaction_time_in_minutes = FloatField(
        "Reaction Time (min)", validators=[Optional()]
    )
    quenching_reagent = StringField("Quenching Reagent", default="N/A", validators=[Optional()])
    uv_source = StringField("UV Source", default="N/A", validators=[Optional()])
    uv_time_in_seconds = FloatField("UV Time (s)", validators=[Optional()])
    uv_wavelength_in_nanometers = FloatField(
        "UV Wavelength (nm)", validators=[Optional()]
    )

    # Identification fields
    peptide_level_fraction = StringField(
        "Peptide Level Fraction", default="N/A", validators=[Optional()]
    )

    # Many-to-many
    species_ids = MultiCheckboxField("Species", coerce=int)
    cellosaurus_ids = MultiCheckboxField("Cell Lines", coerce=str)

    def validate_protein_or_cell_concentration(self, field):
        if self.crosslinked_sample.data and field.data is None:
            raise ValidationError('Required for crosslinked samples.')

    def validate_protein_or_cell_concentration_unit(self, field):
        if self.crosslinked_sample.data and (not field.data or field.data.strip() == 'N/A'):
            raise ValidationError('Required for crosslinked samples — enter a real unit.')

    def validate_crosslinker_or_compound_concentration(self, field):
        if self.crosslinked_sample.data and field.data is None:
            raise ValidationError('Required for crosslinked samples.')

    def validate_crosslinker_or_compound_concentration_unit(self, field):
        if self.crosslinked_sample.data and (not field.data or field.data.strip() == 'N/A'):
            raise ValidationError('Required for crosslinked samples — enter a real unit.')


class AcquiredFileForm(FlaskForm):
    location = StringField("Location", validators=[Optional()])
    filename = StringField("Filename", validators=[Optional()])
    size_bytes = FloatField("Size (GB)", validators=[Optional()])
    instrument_initial = StringField("Instrument Initial", validators=[Optional()])
    file_date = StringField("Date", validators=[Optional()])
    user_initials = StringField("User Initials", validators=[Optional()])
    scan_count = IntegerField("Scan Count", validators=[Optional()])
    meta_json = TextAreaField("Meta (JSON)", validators=[Optional()])


def _optional_int(value):
    if value in (None, "", "None"):
        return None
    return int(value)


def _optional_str(value):
    if value in (None, "", "None"):
        return None
    return str(value)


class AcquiredFileEditForm(FlaskForm):
    # validate_choice=False: the experiment/sample option lists are populated
    # client-side from an embedded tree, so any code present in the DB is valid.
    project_code = SelectField(
        "Project", coerce=_optional_str, validators=[Optional()], validate_choice=False
    )
    experiment_code = SelectField(
        "Experiment", coerce=_optional_str, validators=[Optional()], validate_choice=False
    )
    sample_code = SelectField(
        "Sample", coerce=_optional_str, validators=[Optional()], validate_choice=False
    )
