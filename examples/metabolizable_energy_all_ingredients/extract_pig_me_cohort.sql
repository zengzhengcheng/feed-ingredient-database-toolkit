WITH targets AS (
    SELECT
        n.nutrition_observation_id,
        s.study_id,
        s.title AS study_title,
        i.ingredient_sample_id,
        i.canonical_name AS ingredient_name,
        n.value_num AS ME,
        n.canonical_unit AS target_unit,
        n.canonical_basis AS basis,
        n.source_workbook AS target_source_workbook,
        n.source_sheet AS target_source_sheet,
        n.source_row AS target_source_row,
        n.source_column AS target_source_column
    FROM nutrition_observations AS n
    JOIN ingredient_evaluations AS e
      ON e.ingredient_evaluation_id = n.ingredient_evaluation_id
    JOIN ingredient_samples AS i
      ON i.ingredient_sample_id = e.ingredient_sample_id
    JOIN studies AS s
      ON s.study_id = i.study_id
    WHERE s.canonical_species = 'Pig'
      AND n.measurement_context = 'energy'
      AND n.canonical_metric = 'ME'
      AND n.canonical_basis = 'dry matter'
      AND n.canonical_unit = 'MJ/kg'
      AND n.value_num IS NOT NULL
      AND n.validation_status = 'accepted'
),
feature_candidates AS (
    SELECT
        component_observation_id,
        ingredient_sample_id,
        canonical_metric,
        value_num,
        canonical_unit,
        canonical_basis,
        source_workbook,
        source_sheet,
        source_row,
        source_column
    FROM component_observations
    WHERE canonical_metric IN ('CP', 'EE', 'Ash', 'NDF', 'ADF', 'GE')
      AND canonical_basis = 'dry matter'
      AND value_num IS NOT NULL
      AND validation_status = 'accepted'
      AND (
          (canonical_metric IN ('CP', 'EE', 'Ash', 'NDF', 'ADF') AND canonical_unit = '%')
          OR (canonical_metric = 'GE' AND canonical_unit = 'MJ/kg')
      )
)
SELECT
    t.*,
    f.component_observation_id,
    f.canonical_metric AS feature_name,
    f.value_num AS feature_value,
    f.canonical_unit AS feature_unit,
    f.canonical_basis AS feature_basis,
    f.source_workbook AS feature_source_workbook,
    f.source_sheet AS feature_source_sheet,
    f.source_row AS feature_source_row,
    f.source_column AS feature_source_column
FROM targets AS t
LEFT JOIN feature_candidates AS f
  ON f.ingredient_sample_id = t.ingredient_sample_id
ORDER BY
    t.study_id,
    t.ingredient_sample_id,
    t.nutrition_observation_id,
    f.canonical_metric,
    f.source_row,
    f.source_column;
