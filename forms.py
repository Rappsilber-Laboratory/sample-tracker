from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    FloatField,
    HiddenField,
    IntegerField,
    SelectField,
    SelectMultipleField,
    StringField,
    TextAreaField,
    widgets,
)
from wtforms.validators import AnyOf, DataRequired, Length, Optional, ValidationError


def _strip_text(value):
    return value.strip() if isinstance(value, str) else value


class IssueForm(FlaskForm):
    title = StringField("Title", filters=[_strip_text], validators=[DataRequired(), Length(max=200)])
    author = StringField("Your name", filters=[_strip_text], validators=[DataRequired(), Length(max=100)])
    body = TextAreaField("Description", filters=[_strip_text], validators=[DataRequired(), Length(max=20000)])


class IssueReplyForm(FlaskForm):
    author = StringField("Your name", filters=[_strip_text], validators=[DataRequired(), Length(max=100)])
    body = TextAreaField("Response", filters=[_strip_text], validators=[DataRequired(), Length(max=20000)])


class IssueStatusForm(FlaskForm):
    author = StringField("Your name", filters=[_strip_text], validators=[DataRequired(), Length(max=100)])
    status = HiddenField(validators=[DataRequired(), AnyOf(["open", "closed"])])


def valid_code(form, field):
    if not field.data:
        return
    if len(field.data) > 8:
        raise ValidationError("Code must be at most 8 characters.")
    if not field.data.isascii() or not field.data.isalnum():
        raise ValidationError("Code must contain only letters and digits (a-z, A-Z, 0-9).")


CODE_RENDER_KW = {"maxlength": 8, "pattern": "[A-Za-z0-9]+"}


class MultiCheckboxField(SelectMultipleField):
    widget = widgets.ListWidget(prefix_label=False)
    option_widget = widgets.CheckboxInput()


class ProjectForm(FlaskForm):
    code = StringField(
        "Code", validators=[DataRequired(), valid_code], render_kw=CODE_RENDER_KW
    )
    name = StringField("Name", validators=[DataRequired()])
    description = TextAreaField("Description", validators=[DataRequired()])
    user_initials = SelectField("Contact Person", validators=[DataRequired()], coerce=str)
    active = BooleanField("Active", default=True)


class ExperimentForm(FlaskForm):
    project_code = SelectField("Project", coerce=str, validators=[DataRequired()])
    code = StringField(
        "Code", validators=[DataRequired(), valid_code], render_kw=CODE_RENDER_KW
    )
    name = StringField("Name", validators=[Optional()])
    description = TextAreaField("Description", validators=[Optional()])
    user_initials = SelectField("Contact Person", validators=[Optional()], coerce=str)
    active = BooleanField("Active", default=True)

    # Invariant conditions defined on the experiment.
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
        default="no",
        choices=[("", ""), ("yes", "Yes"), ("no", "No"), ("spiked in", "Spiked In")],
        validators=[Optional()],
    )
    digestion = StringField("Digestion", validators=[Optional()])
    protein_isolation_or_fractionation = StringField(
        "Protein Isolation / Fractionation", validators=[Optional()]
    )
    crosslinking_experiment = BooleanField("Crosslinking Experiment", default=False)
    quantitation = BooleanField("Quantitation", default=False)
    quantitation_scheme = StringField("Quantitation Scheme", validators=[Optional()])
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
        "Protein or Cell Concentration", validators=[Optional()]
    )
    protein_or_cell_concentration_unit = StringField(
        "Protein or Cell Concentration Unit", validators=[Optional()]
    )
    crosslinker_or_compound_concentration = FloatField(
        "Crosslinker or Compound Concentration", validators=[Optional()]
    )
    crosslinker_or_compound_concentration_unit = StringField(
        "Crosslinker or Compound Concentration Unit", validators=[Optional()]
    )
    organic_solvent_concentration = FloatField(
        "Organic Solvent Concentration", validators=[Optional()]
    )
    organic_solvent_concentration_unit = StringField(
        "Organic Solvent Concentration Unit", validators=[Optional()]
    )
    reaction_temperature_in_celsius = FloatField(
        "Reaction Temperature (°C)", validators=[Optional()]
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

    # Many-to-many
    species_ids = MultiCheckboxField("Species", coerce=int)
    cellosaurus_ids = MultiCheckboxField("Cell Lines", coerce=str)

    # Variable conditions: the editable table serialised to JSON by the browser
    # (an array of {description, prefix, values, postfix} rows). Parsed on save.
    varying_conditions = HiddenField()

    # Generation inputs used by "Save and Generate Queue" to build the run
    # filenames/CSV. The instrument is persisted (as experiment.instrument_initial,
    # mapped in the route); operator, date and run start are transient. Choices
    # for the two selects are populated per-request in the route.
    gen_instrument = SelectField(
        "Instrument", coerce=str, validators=[Optional()], validate_choice=False
    )
    gen_user = SelectField(
        "Operator", coerce=str, validators=[Optional()], validate_choice=False
    )
    gen_date = StringField("Date", validators=[Optional()], render_kw={"type": "date"})
    gen_run_start = IntegerField("Run number start", default=1, validators=[Optional()])

    # Well-plate options, persisted via same-named experiment columns. well_enable is the
    # master switch; the two include_* toggles decide where the well/plate lands.
    well_enable = BooleanField("Use 96-well plate")
    well_order = SelectField(
        "Well order",
        choices=[("rowwise", "Row-wise"), ("columnwise", "Column-wise")],
        default="rowwise",
        validators=[Optional()],
    )
    well_start = StringField("Start position", default="A1", validators=[Optional()])
    well_in_filename = BooleanField("Add well to end of filename")
    well_in_position = BooleanField("Put well in Position column")


class ExperimentCreateForm(FlaskForm):
    # The create page asks only for the code (the primary key). Everything else is
    # filled in afterwards on the autosaving edit page. The optional "base on an
    # existing experiment" <select name="copy_from"> is rendered manually and read
    # straight from request.form (its options are grouped composite-key tokens).
    code = StringField(
        "Code", validators=[DataRequired(), valid_code], render_kw=CODE_RENDER_KW
    )


class InstrumentForm(FlaskForm):
    initial = StringField("Initial", validators=[DataRequired()])
    name = StringField("Name", validators=[DataRequired()])
    model = StringField("Model", validators=[Optional()])


class SpeciesForm(FlaskForm):
    species_name = StringField("Species Name", validators=[DataRequired()])
    species_taxon = StringField("Species Taxon", validators=[DataRequired()])


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
    # validate_choice=False: the experiment option list is populated client-side
    # from an embedded tree, so any code present in the DB is valid.
    project_code = SelectField(
        "Project", coerce=_optional_str, validators=[Optional()], validate_choice=False
    )
    experiment_code = SelectField(
        "Experiment", coerce=_optional_str, validators=[Optional()], validate_choice=False
    )
