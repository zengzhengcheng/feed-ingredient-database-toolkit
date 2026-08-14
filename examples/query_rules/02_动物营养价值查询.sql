-- 例：查询猪的 SID 营养价值。可修改 species 和 measurement_context。
SELECT s.title,
       s.doi,
       i.canonical_name AS ingredient,
       t.source_trial_no AS trial_no,
       a.canonical_species AS animal_species,
       a.breed,
       n.measurement_context,
       n.canonical_metric AS metric,
       n.value_num,
       n.value_text,
       n.canonical_unit AS unit,
       n.canonical_basis AS basis,
       n.validation_status
FROM nutrition_observations n
JOIN ingredient_evaluations e USING (ingredient_evaluation_id)
JOIN ingredient_samples i USING (ingredient_sample_id)
JOIN studies s USING (study_id)
JOIN trials t USING (trial_id)
JOIN animal_groups a USING (animal_group_id)
WHERE a.canonical_species LIKE '%Pig%'
  AND n.measurement_context = 'SID'
ORDER BY s.title, i.canonical_name, t.source_trial_no, n.canonical_metric;
