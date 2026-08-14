WITH feature_rows AS (
    SELECT ingredient_sample_id, canonical_metric, value_num
    FROM component_observations
    WHERE validation_status = 'accepted'
      AND canonical_basis = 'dry matter'
      AND value_num IS NOT NULL
      AND (
          (canonical_metric IN ('CP', 'EE', 'Ash', 'NDF', 'ADF') AND canonical_unit = '%')
          OR (canonical_metric = 'GE' AND canonical_unit = 'MJ/kg')
      )
),
features AS (
    SELECT
        ingredient_sample_id,
        MAX(CASE WHEN canonical_metric = 'CP'  THEN value_num END) AS CP,
        MAX(CASE WHEN canonical_metric = 'EE'  THEN value_num END) AS EE,
        MAX(CASE WHEN canonical_metric = 'Ash' THEN value_num END) AS Ash,
        MAX(CASE WHEN canonical_metric = 'NDF' THEN value_num END) AS NDF,
        MAX(CASE WHEN canonical_metric = 'ADF' THEN value_num END) AS ADF,
        MAX(CASE WHEN canonical_metric = 'GE'  THEN value_num END) AS GE
    FROM feature_rows
    GROUP BY ingredient_sample_id
    HAVING COUNT(*) = 6
       AND COUNT(DISTINCT canonical_metric) = 6
)
SELECT
    s.study_id,
    s.title AS study_title,
    i.ingredient_sample_id,
    i.canonical_name AS ingredient_name,
    n.nutrition_observation_id,
    f.CP, f.EE, f.Ash, f.NDF, f.ADF, f.GE,
    n.value_num AS NE,
    '%' AS composition_unit,
    'MJ/kg' AS GE_unit,
    n.canonical_unit AS target_unit,
    n.canonical_basis AS basis
FROM nutrition_observations AS n
JOIN ingredient_evaluations AS e
  ON e.ingredient_evaluation_id = n.ingredient_evaluation_id
JOIN ingredient_samples AS i
  ON i.ingredient_sample_id = e.ingredient_sample_id
JOIN studies AS s
  ON s.study_id = i.study_id
JOIN features AS f
  ON f.ingredient_sample_id = i.ingredient_sample_id
WHERE s.canonical_species = 'Pig'
  AND n.measurement_context = 'energy'
  AND n.canonical_metric = 'NE'
  AND n.canonical_basis = 'dry matter'
  AND n.canonical_unit = 'MJ/kg'
  AND n.value_num IS NOT NULL
  AND n.validation_status = 'accepted'
ORDER BY s.study_id, i.ingredient_sample_id, n.nutrition_observation_id;
