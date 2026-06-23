-- Mass Spec Acquisition Tracker SQLite Schema
-- Based on sampleTracker13.dia UML diagram

PRAGMA foreign_keys = ON;

CREATE TABLE project (
    code TEXT NOT NULL PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    user_initials TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE experiment (
    project_code TEXT NOT NULL REFERENCES project(code),
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    user_initials TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (project_code, code)
);

CREATE TABLE species (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    species_name TEXT NOT NULL,
    species_taxon TEXT NOT NULL
);

CREATE TABLE cell_line (
    cellosaurus_id TEXT NOT NULL PRIMARY KEY,
    cell_line_name TEXT NOT NULL,
    species_id INTEGER NOT NULL REFERENCES species(id)
);

CREATE TABLE mass_spec_sample (
    project_code TEXT NOT NULL,
    experiment_code TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    user_initials TEXT NOT NULL,
    disease TEXT,
    phenotype TEXT,
    isotope_labeling_channel TEXT,
    chemical_labelling TEXT,
    tissue TEXT,
    organism_age REAL,
    organism_age_unit TEXT,
    organism_sex TEXT,
    enrichment_process TEXT,
    replicate TEXT,
    synthetic_peptide TEXT CHECK(synthetic_peptide IN ('yes', 'no', 'spiked in')),
    digestion TEXT,
    protein_isolation_or_fractionation TEXT,
    crosslinked_sample INTEGER NOT NULL DEFAULT 0,
    quantitation INTEGER DEFAULT 0,
    quantitation_scheme TEXT,
    quantitation_method TEXT CHECK(quantitation_method IN ('LFQ', 'isotope labelled MS1', 'Isobaric MS2')),

    -- CrosslinkSample columns (used when crosslinked_sample = 1)
    crosslinker TEXT,
    crosslinking_type TEXT CHECK(crosslinking_type IN ('in cell', 'in lysate', 'in solution', 'compound')),
    protein_or_cell_concentration REAL,
    protein_or_cell_concentration_unit TEXT,
    crosslinker_or_compound_concentration REAL,
    crosslinker_or_compound_concentration_unit TEXT,
    organic_solvent_concentration REAL,
    organic_solvent_concentration_unit TEXT,
    reaction_temperature_in_celsius REAL,
    reaction_time_in_minutes REAL,
    quenching_reagent TEXT,
    uv_source TEXT,
    uv_time_in_seconds REAL,
    uv_wavelength_in_nanometers REAL,

    -- IdentificationSample columns (used when crosslinked_sample = 0)
    peptide_level_fraction TEXT,

    PRIMARY KEY (project_code, experiment_code, code),
    FOREIGN KEY (project_code, experiment_code)
        REFERENCES experiment(project_code, code)
);

CREATE TABLE virus (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    species_id INTEGER REFERENCES species(id),
    variant TEXT
);

CREATE TABLE cell_line_virus (
    cellosaurus_id TEXT NOT NULL REFERENCES cell_line(cellosaurus_id),
    virus_id INTEGER NOT NULL REFERENCES virus(id),
    PRIMARY KEY (cellosaurus_id, virus_id)
);

CREATE TABLE sample_species (
    project_code TEXT NOT NULL,
    experiment_code TEXT NOT NULL,
    sample_code TEXT NOT NULL,
    species_id INTEGER NOT NULL REFERENCES species(id),
    PRIMARY KEY (project_code, experiment_code, sample_code, species_id),
    FOREIGN KEY (project_code, experiment_code, sample_code)
        REFERENCES mass_spec_sample(project_code, experiment_code, code)
);

CREATE TABLE sample_cell_line (
    project_code TEXT NOT NULL,
    experiment_code TEXT NOT NULL,
    sample_code TEXT NOT NULL,
    cellosaurus_id TEXT NOT NULL REFERENCES cell_line(cellosaurus_id),
    PRIMARY KEY (project_code, experiment_code, sample_code, cellosaurus_id),
    FOREIGN KEY (project_code, experiment_code, sample_code)
        REFERENCES mass_spec_sample(project_code, experiment_code, code)
);

CREATE TABLE user (
    initials TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE acquired_file (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_code TEXT,
    experiment_code TEXT,
    sample_code TEXT,
    location TEXT,
    filename TEXT,
    size_bytes INTEGER,
    instrument_initial TEXT,
    file_date DATE,
    user_initials TEXT,
    scan_count INTEGER,
    meta TEXT,
    FOREIGN KEY (project_code, experiment_code, sample_code)
        REFERENCES mass_spec_sample(project_code, experiment_code, code)
);

CREATE TABLE queued_file (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_code TEXT,
    experiment_code TEXT,
    sample_code TEXT,
    instrument_initial TEXT,
    user_initials TEXT,
    date_queued DATE,
    daily_counter INTEGER,
    postfix TEXT,
    FOREIGN KEY (project_code, experiment_code, sample_code)
        REFERENCES mass_spec_sample(project_code, experiment_code, code)
);
