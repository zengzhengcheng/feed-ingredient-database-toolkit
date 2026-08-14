from __future__ import annotations

from pathlib import Path

import time

from .user_query import readonly_connection


def search_database(
    database_path: str | Path,
    query: str = "",
    limit: int = 1000,
    timeout_seconds: float = 15.0,
):
    """Multi-keyword substring search across citations, ingredients, metrics, contexts, and animals."""
    terms = [term.strip() for term in query.split() if term.strip()]
    if limit < 1 or limit > 10_000:
        raise ValueError("关键词查询显示上限必须在 1 到 10,000 之间。")
    deadline = time.monotonic() + max(float(timeout_seconds), 0.1)
    with readonly_connection(database_path) as conn:
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        where_groups: list[str] = []
        parameters: list[object] = []
        for term in terms:
            variants = {term}
            aliases = conn.execute(
                """SELECT raw_term,canonical_term FROM dictionaries
                   WHERE raw_term LIKE ? OR canonical_term LIKE ?
                   ORDER BY status='approved' DESC,raw_term LIMIT 50""",
                (f"%{term}%", f"%{term}%"),
            ).fetchall()
            for row in aliases:
                variants.update(value for value in (row[0], row[1]) if value)
            ordered = sorted(variants, key=lambda value: (value.casefold() != term.casefold(), value.casefold()))
            where_groups.append("(" + " OR ".join("search_blob LIKE ?" for _ in ordered) + ")")
            parameters.extend(f"%{value}%" for value in ordered)
        where = " AND ".join(where_groups) or "1=1"
        parameters.append(int(limit))
        sql = f"""
        WITH searchable AS (
            SELECT s.doi,s.title,i.raw_name AS raw_ingredient,i.canonical_name AS ingredient,
                   'component' AS data_type,'composition' AS context,
                   o.raw_metric,o.canonical_metric AS metric,o.value_num,o.value_text,
                   o.canonical_unit AS unit,o.canonical_basis AS basis,o.validation_status AS data_status,
                   '' AS trial_no,'' AS animal_species,'' AS breed,
                   coalesce(s.doi,'') || ' ' || s.title || ' ' || i.raw_name || ' ' || i.canonical_name || ' ' ||
                   o.raw_metric || ' ' || o.canonical_metric || ' composition' AS search_blob,
                   o.component_observation_id AS sort_id
            FROM component_observations o
            JOIN ingredient_samples i USING(ingredient_sample_id)
            JOIN studies s USING(study_id)
            UNION ALL
            SELECT s.doi,s.title,i.raw_name,i.canonical_name,
                   'nutrition',o.measurement_context,o.raw_metric,o.canonical_metric,o.value_num,o.value_text,
                   o.canonical_unit,o.canonical_basis,o.validation_status,
                   t.source_trial_no,a.canonical_species,a.breed,
                   coalesce(s.doi,'') || ' ' || s.title || ' ' || i.raw_name || ' ' || i.canonical_name || ' ' ||
                   o.raw_metric || ' ' || o.canonical_metric || ' ' || o.measurement_context || ' ' ||
                   coalesce(a.raw_species,'') || ' ' || coalesce(a.canonical_species,'') || ' ' || coalesce(a.breed,'') || ' ' ||
                   coalesce(t.source_trial_no,'') AS search_blob,
                   o.nutrition_observation_id
            FROM nutrition_observations o
            JOIN ingredient_evaluations e USING(ingredient_evaluation_id)
            JOIN ingredient_samples i USING(ingredient_sample_id)
            JOIN studies s USING(study_id)
            JOIN trials t USING(trial_id)
            JOIN animal_groups a USING(animal_group_id)
        )
        SELECT doi,title,raw_ingredient,ingredient,data_type,context,raw_metric,metric,
               value_num,value_text,unit,basis,trial_no,animal_species,breed,data_status
        FROM searchable WHERE {where}
        ORDER BY title,ingredient,data_type,context,metric,sort_id LIMIT ?
        """
        cursor = conn.execute(sql, parameters)
        return [description[0] for description in cursor.description], cursor.fetchall()
