-- Sample Tracker SQLite Schema
-- Based on sampleTracker13.dia UML diagram

PRAGMA foreign_keys = ON;

CREATE TABLE project (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    user_initials TEXT,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE experiment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES project(id),
    code TEXT,
    name TEXT NOT NULL,
    description TEXT,
    user_initials TEXT,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE species (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    species_name TEXT NOT NULL,
    species_taxon TEXT
);

CREATE TABLE cell_line (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cell_line_name TEXT NOT NULL,
    cell_line_code TEXT,
    species_id INTEGER REFERENCES species(id)
);

CREATE TABLE mass_spec_sample (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id INTEGER NOT NULL REFERENCES experiment(id),
    name TEXT NOT NULL,
    description TEXT,
    user_initials TEXT,
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

    code TEXT
);

CREATE TABLE virus (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    species_id INTEGER REFERENCES species(id),
    variant TEXT
);

CREATE TABLE cell_line_virus (
    cell_line_id INTEGER NOT NULL REFERENCES cell_line(id),
    virus_id INTEGER NOT NULL REFERENCES virus(id),
    PRIMARY KEY (cell_line_id, virus_id)
);

CREATE TABLE sample_species (
    sample_id INTEGER NOT NULL REFERENCES mass_spec_sample(id),
    species_id INTEGER NOT NULL REFERENCES species(id),
    PRIMARY KEY (sample_id, species_id)
);

CREATE TABLE sample_cell_line (
    sample_id INTEGER NOT NULL REFERENCES mass_spec_sample(id),
    cell_line_id INTEGER NOT NULL REFERENCES cell_line(id),
    PRIMARY KEY (sample_id, cell_line_id)
);

CREATE TABLE user (
    initials TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE mass_spec_acquisition (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id INTEGER REFERENCES mass_spec_sample(id),
    location TEXT,
    filename TEXT,
    size_bytes INTEGER,
    instrument_initial TEXT,
    date DATE,
    user_initials TEXT,
    scan_count INTEGER,
    meta TEXT
);
