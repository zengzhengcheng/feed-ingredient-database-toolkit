-- 把 Corn 改成需要查询的统一原料名；百分号表示包含匹配。
SELECT s.title,
       s.doi,
       i.raw_name AS raw_ingredient,
       i.canonical_name AS ingredient,
       o.raw_metric,
       o.canonical_metric AS metric,
       o.value_num,
       o.value_text,
       o.canonical_unit AS unit,
       o.canonical_basis AS basis,
       o.validation_status
FROM component_observations o
JOIN ingredient_samples i USING (ingredient_sample_id)
JOIN studies s USING (study_id)
WHERE i.canonical_name LIKE '%Corn%'
ORDER BY s.title, i.canonical_name, o.canonical_metric;
