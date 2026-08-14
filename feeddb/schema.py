SCHEMA_VERSION = "1.0.0"


SCHEMA_SQL = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_metadata (
    metadata_key TEXT PRIMARY KEY,
    metadata_value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS studies (
    study_id INTEGER PRIMARY KEY,
    doi TEXT UNIQUE COLLATE NOCASE,
    title TEXT NOT NULL COLLATE NOCASE,
    journal TEXT,
    publication_year INTEGER,
    raw_species TEXT,
    canonical_species TEXT,
    species_mapping_status TEXT NOT NULL DEFAULT 'missing'
        CHECK(species_mapping_status IN ('approved','candidate','deprecated','missing')),
    country TEXT,
    source_workbook TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS trials (
    trial_id INTEGER PRIMARY KEY,
    study_id INTEGER NOT NULL REFERENCES studies(study_id) ON DELETE CASCADE,
    source_trial_no TEXT NOT NULL DEFAULT '',
    treatment TEXT,
    notes TEXT,
    UNIQUE(study_id, source_trial_no)
);

CREATE TABLE IF NOT EXISTS ingredient_samples (
    ingredient_sample_id INTEGER PRIMARY KEY,
    study_id INTEGER NOT NULL REFERENCES studies(study_id) ON DELETE CASCADE,
    source_ingredient_no TEXT NOT NULL,
    raw_name TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    name_mapping_status TEXT NOT NULL DEFAULT 'approved'
        CHECK(name_mapping_status IN ('approved','candidate','deprecated')),
    feed_type TEXT,
    processing_technic TEXT,
    place_of_origin TEXT,
    reported_basis TEXT,
    dry_matter_value REAL,
    source_workbook TEXT NOT NULL,
    source_sheet TEXT NOT NULL,
    first_source_column INTEGER NOT NULL,
    UNIQUE(study_id, source_workbook, source_ingredient_no, raw_name)
);

CREATE TABLE IF NOT EXISTS animal_groups (
    animal_group_id INTEGER PRIMARY KEY,
    trial_id INTEGER NOT NULL REFERENCES trials(trial_id) ON DELETE CASCADE,
    raw_species TEXT,
    canonical_species TEXT,
    species_mapping_status TEXT NOT NULL DEFAULT 'missing'
        CHECK(species_mapping_status IN ('approved','candidate','deprecated','missing')),
    breed TEXT,
    sex TEXT,
    animal_type TEXT,
    period TEXT,
    age_days REAL,
    age_weeks REAL,
    body_weight_kg REAL,
    notes TEXT,
    source_workbook TEXT NOT NULL,
    source_sheet TEXT NOT NULL,
    source_column INTEGER NOT NULL,
    UNIQUE(trial_id, source_workbook, source_sheet, source_column)
);

CREATE TABLE IF NOT EXISTS ingredient_evaluations (
    ingredient_evaluation_id INTEGER PRIMARY KEY,
    ingredient_sample_id INTEGER NOT NULL REFERENCES ingredient_samples(ingredient_sample_id) ON DELETE CASCADE,
    trial_id INTEGER NOT NULL REFERENCES trials(trial_id) ON DELETE CASCADE,
    animal_group_id INTEGER NOT NULL REFERENCES animal_groups(animal_group_id) ON DELETE CASCADE,
    source_workbook TEXT NOT NULL,
    source_sheet TEXT NOT NULL,
    source_column INTEGER NOT NULL,
    UNIQUE(ingredient_sample_id, trial_id, animal_group_id, source_workbook, source_sheet, source_column)
);

CREATE TABLE IF NOT EXISTS component_observations (
    component_observation_id INTEGER PRIMARY KEY,
    ingredient_sample_id INTEGER NOT NULL REFERENCES ingredient_samples(ingredient_sample_id) ON DELETE CASCADE,
    raw_metric TEXT NOT NULL,
    canonical_metric TEXT NOT NULL,
    value_num REAL,
    value_text TEXT,
    raw_unit TEXT,
    canonical_unit TEXT,
    unit_resolution_method TEXT NOT NULL DEFAULT 'unresolved',
    unit_rule_reference TEXT NOT NULL DEFAULT '',
    raw_basis TEXT,
    canonical_basis TEXT,
    metric_mapping_status TEXT NOT NULL DEFAULT 'approved'
        CHECK(metric_mapping_status IN ('approved','candidate','deprecated')),
    unit_mapping_status TEXT NOT NULL DEFAULT 'missing'
        CHECK(unit_mapping_status IN ('approved','candidate','deprecated','missing')),
    basis_mapping_status TEXT NOT NULL DEFAULT 'missing'
        CHECK(basis_mapping_status IN ('approved','candidate','deprecated','missing')),
    qualifier TEXT,
    section TEXT,
    subsection TEXT,
    validation_status TEXT NOT NULL DEFAULT 'accepted'
        CHECK(validation_status IN ('accepted','held','rejected')),
    source_workbook TEXT NOT NULL,
    source_sheet TEXT NOT NULL,
    source_row INTEGER NOT NULL,
    source_column INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS nutrition_observations (
    nutrition_observation_id INTEGER PRIMARY KEY,
    ingredient_evaluation_id INTEGER NOT NULL REFERENCES ingredient_evaluations(ingredient_evaluation_id) ON DELETE CASCADE,
    measurement_context TEXT NOT NULL,
    raw_metric TEXT NOT NULL,
    canonical_metric TEXT NOT NULL,
    value_num REAL,
    value_text TEXT,
    raw_unit TEXT,
    canonical_unit TEXT,
    unit_resolution_method TEXT NOT NULL DEFAULT 'unresolved',
    unit_rule_reference TEXT NOT NULL DEFAULT '',
    raw_basis TEXT,
    canonical_basis TEXT,
    metric_mapping_status TEXT NOT NULL DEFAULT 'approved'
        CHECK(metric_mapping_status IN ('approved','candidate','deprecated')),
    unit_mapping_status TEXT NOT NULL DEFAULT 'missing'
        CHECK(unit_mapping_status IN ('approved','candidate','deprecated','missing')),
    basis_mapping_status TEXT NOT NULL DEFAULT 'missing'
        CHECK(basis_mapping_status IN ('approved','candidate','deprecated','missing')),
    qualifier TEXT,
    section TEXT,
    subsection TEXT,
    validation_status TEXT NOT NULL DEFAULT 'accepted'
        CHECK(validation_status IN ('accepted','held','rejected')),
    source_workbook TEXT NOT NULL,
    source_sheet TEXT NOT NULL,
    source_row INTEGER NOT NULL,
    source_column INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS source_provenance (
    provenance_id INTEGER PRIMARY KEY,
    entity_type TEXT NOT NULL CHECK(entity_type IN ('study','trial','ingredient_sample','animal_group','ingredient_evaluation','component_observation','nutrition_observation')),
    entity_id INTEGER NOT NULL,
    source_workbook TEXT NOT NULL,
    source_sheet TEXT,
    source_row INTEGER,
    source_column INTEGER,
    source_note TEXT,
    UNIQUE(entity_type, entity_id, source_workbook, source_sheet, source_row, source_column)
);

CREATE TABLE IF NOT EXISTS dictionaries (
    dictionary_id INTEGER PRIMARY KEY,
    category TEXT NOT NULL CHECK(category IN ('ingredient','metric','unit','basis','species')),
    context TEXT NOT NULL DEFAULT '',
    raw_term TEXT NOT NULL COLLATE NOCASE,
    canonical_term TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'approved'
        CHECK(status IN ('approved','candidate','deprecated')),
    note TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(category, context, raw_term)
);

CREATE TABLE IF NOT EXISTS metric_catalog (
    metric_catalog_id INTEGER PRIMARY KEY,
    context TEXT NOT NULL,
    group_name TEXT NOT NULL,
    canonical_metric TEXT NOT NULL COLLATE NOCASE,
    default_unit TEXT NOT NULL DEFAULT '%',
    default_basis TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    note TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(context, group_name, canonical_metric)
);

CREATE TABLE IF NOT EXISTS import_runs (
    import_id INTEGER PRIMARY KEY,
    source_path TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    status TEXT NOT NULL,
    studies_count INTEGER NOT NULL DEFAULT 0,
    ingredients_count INTEGER NOT NULL DEFAULT 0,
    component_observations_count INTEGER NOT NULL DEFAULT 0,
    nutrition_observations_count INTEGER NOT NULL DEFAULT 0,
    observations_count INTEGER NOT NULL DEFAULT 0,
    held_count INTEGER NOT NULL DEFAULT 0,
    unmapped_count INTEGER NOT NULL DEFAULT 0,
    message TEXT
);

CREATE TABLE IF NOT EXISTS validation_issues (
    validation_issue_id INTEGER PRIMARY KEY,
    import_id INTEGER REFERENCES import_runs(import_id) ON DELETE CASCADE,
    level TEXT NOT NULL CHECK(level IN ('warning','error')),
    entity_type TEXT,
    entity_id INTEGER,
    source_workbook TEXT,
    source_sheet TEXT,
    source_row INTEGER,
    source_column INTEGER,
    issue_code TEXT NOT NULL,
    message TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0 CHECK(resolved IN (0,1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS operation_log (
    operation_id INTEGER PRIMARY KEY,
    occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    operation_type TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL DEFAULT '',
    counts_json TEXT NOT NULL DEFAULT '{}',
    message TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_component_metric ON component_observations(canonical_metric);
CREATE INDEX IF NOT EXISTS idx_nutrition_metric_context ON nutrition_observations(measurement_context, canonical_metric);
CREATE INDEX IF NOT EXISTS idx_ingredient_sample_name ON ingredient_samples(canonical_name);
CREATE INDEX IF NOT EXISTS idx_dictionary_lookup ON dictionaries(category, context, raw_term, status);
CREATE INDEX IF NOT EXISTS idx_metric_catalog_group ON metric_catalog(context, group_name, active, sort_order);
CREATE INDEX IF NOT EXISTS idx_provenance_entity ON source_provenance(entity_type, entity_id);

CREATE VIEW IF NOT EXISTS ingredients AS
SELECT ingredient_sample_id AS ingredient_id, study_id, source_ingredient_no, raw_name,
       canonical_name, name_mapping_status, feed_type,
       (SELECT t.source_trial_no FROM ingredient_evaluations e
        JOIN trials t ON t.trial_id=e.trial_id
        WHERE e.ingredient_sample_id=ingredient_samples.ingredient_sample_id
        ORDER BY e.ingredient_evaluation_id LIMIT 1) AS trial_no,
       source_workbook, source_sheet, first_source_column AS source_column
FROM ingredient_samples;

CREATE VIEW IF NOT EXISTS observations AS
SELECT component_observation_id * 2 AS observation_id,
       ingredient_sample_id AS ingredient_id,
       raw_metric, canonical_metric, 'composition' AS measurement_context,
       value_num, value_text, raw_unit, canonical_unit, unit_resolution_method, unit_rule_reference,
       canonical_basis AS basis, metric_mapping_status, unit_mapping_status,
       basis_mapping_status, qualifier, source_row, validation_status,
       'component' AS observation_kind
FROM component_observations
UNION ALL
SELECT nutrition_observation_id * 2 + 1 AS observation_id,
       e.ingredient_sample_id AS ingredient_id,
       n.raw_metric, n.canonical_metric, n.measurement_context,
       n.value_num, n.value_text, n.raw_unit, n.canonical_unit, n.unit_resolution_method, n.unit_rule_reference,
       n.canonical_basis AS basis, n.metric_mapping_status, n.unit_mapping_status,
       n.basis_mapping_status, n.qualifier, n.source_row, n.validation_status,
       'nutrition' AS observation_kind
FROM nutrition_observations n
JOIN ingredient_evaluations e USING(ingredient_evaluation_id);
"""
