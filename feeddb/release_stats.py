from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .database import connect
from .excel_io import INGREDIENT_SHEET_NAMES
from .term_review import collect_candidate_term_activity


def collect_release_statistics(database_path: str | Path) -> dict[str, object]:
    allowed_sheets = {name.casefold() for name in INGREDIENT_SHEET_NAMES}
    with connect(database_path) as conn:
        table_counts: dict[str, int] = {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "studies", "trials", "ingredient_samples", "animal_groups", "ingredient_evaluations",
                "component_observations", "nutrition_observations", "source_provenance",
                "dictionaries", "import_runs", "validation_issues",
            )
        }
        table_counts["ingredients"] = table_counts["ingredient_samples"]
        table_counts["observations"] = (
            table_counts["component_observations"] + table_counts["nutrition_observations"]
        )
        observation_status = dict(
            conn.execute("SELECT validation_status, count(*) FROM observations GROUP BY validation_status")
        )
        measurement_contexts = dict(
            conn.execute("SELECT measurement_context, count(*) FROM observations GROUP BY measurement_context ORDER BY measurement_context")
        )
        dictionary_status: dict[str, dict[str, int]] = defaultdict(dict)
        for category, status, count in conn.execute(
            "SELECT category, status, count(*) FROM dictionaries GROUP BY category, status ORDER BY category, status"
        ):
            dictionary_status[category][status] = count
        source_sheets = [row[0] for row in conn.execute("SELECT DISTINCT source_sheet FROM ingredients ORDER BY source_sheet")]
        duplicate_dois = conn.execute(
            """SELECT count(*) FROM (
                   SELECT doi FROM studies WHERE trim(coalesce(doi,''))<>''
                   GROUP BY lower(doi) HAVING count(*) > 1
               )"""
        ).fetchone()[0]
        foreign_key_errors = len(conn.execute("PRAGMA foreign_key_check").fetchall())
        missing_doi = conn.execute("SELECT count(*) FROM studies WHERE trim(coalesce(doi,''))=''").fetchone()[0]
        missing_title = conn.execute("SELECT count(*) FROM studies WHERE trim(coalesce(title,''))=''").fetchone()[0]
        distinct_counts = {
            "source_workbooks": conn.execute("SELECT count(DISTINCT source_workbook) FROM ingredients").fetchone()[0],
            "raw_ingredient_names": conn.execute("SELECT count(DISTINCT raw_name) FROM ingredients").fetchone()[0],
            "canonical_ingredient_names": conn.execute("SELECT count(DISTINCT canonical_name) FROM ingredients").fetchone()[0],
            "raw_metrics": conn.execute("SELECT count(DISTINCT raw_metric) FROM observations").fetchone()[0],
            "canonical_metrics": conn.execute("SELECT count(DISTINCT canonical_metric) FROM observations").fetchone()[0],
            "canonical_metric_context_pairs": conn.execute("SELECT count(*) FROM (SELECT DISTINCT measurement_context,canonical_metric FROM observations)").fetchone()[0],
            "raw_units": conn.execute("SELECT count(DISTINCT raw_unit) FROM observations WHERE trim(coalesce(raw_unit,''))<>''").fetchone()[0],
            "canonical_units": conn.execute("SELECT count(DISTINCT canonical_unit) FROM observations WHERE trim(coalesce(canonical_unit,''))<>''").fetchone()[0],
        }
        observation_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(component_observations)")
        }
        if "unit_resolution_method" in observation_columns:
            unit_methods = dict(conn.execute(
                "SELECT unit_resolution_method,count(*) FROM observations GROUP BY unit_resolution_method ORDER BY count(*) DESC"
            ))
            canonical_unit_counts = dict(conn.execute(
                "SELECT coalesce(canonical_unit,''),count(*) FROM observations GROUP BY canonical_unit ORDER BY count(*) DESC"
            ))
            explicit_unit_observations = conn.execute(
                "SELECT count(*) FROM observations WHERE trim(coalesce(raw_unit,''))<>''"
            ).fetchone()[0]
            unresolved_units = [dict(row) for row in conn.execute(
                """SELECT measurement_context,raw_metric,canonical_metric,count(*) AS observations
                   FROM observations WHERE unit_resolution_method='unresolved'
                   GROUP BY measurement_context,raw_metric,canonical_metric
                   ORDER BY observations DESC,measurement_context,raw_metric"""
            )]
        else:
            unit_methods = {"legacy_schema_unreported": table_counts["observations"]}
            canonical_unit_counts = {}
            explicit_unit_observations = conn.execute(
                "SELECT count(*) FROM observations WHERE trim(coalesce(raw_unit,''))<>''"
            ).fetchone()[0]
            unresolved_units = []
        historical_asparitc = conn.execute(
            "SELECT count(*) FROM observations WHERE canonical_metric='Asparitc'"
        ).fetchone()[0]
        unsafe_asparagine_merge = conn.execute(
            """SELECT count(*) FROM observations AS o
               WHERE lower(o.raw_metric)='asparagine'
                 AND lower(o.canonical_metric)<>'asparagine'
                 AND NOT EXISTS (
                     SELECT 1 FROM dictionaries AS d
                     WHERE d.category='metric'
                       AND lower(d.context)=lower(o.measurement_context)
                       AND lower(d.raw_term)=lower(o.raw_metric)
                       AND lower(d.canonical_term)=lower(o.canonical_metric)
                       AND d.status='approved'
                 )"""
        ).fetchone()[0]

    candidate_terms = collect_candidate_term_activity(database_path)
    candidate_by_category: dict[str, int] = defaultdict(int)
    for term in candidate_terms:
        candidate_by_category[str(term["category"])] += 1
    stored_candidate_rows = sum(statuses.get("candidate", 0) for statuses in dictionary_status.values())
    observations = table_counts["observations"]
    accounted = sum(observation_status.values())
    disallowed_sheets = [sheet for sheet in source_sheets if sheet.casefold() not in allowed_sheets]
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "database_file": Path(database_path).name,
        "release_scope": "Historical feed ingredient composition and nutritional value data selected for this release; diet/formulation sheets and materials outside the selected release scope are excluded.",
        "copyright_boundary": "No article PDF, PDF copy, screenshot, or OCR artifact is included; published articles are referenced by DOI.",
        "table_counts": table_counts,
        "data_model": {
            "component_chain": "Study -> IngredientSample -> ComponentObservation",
            "nutrition_chain": "Study -> Trial -> AnimalGroup -> IngredientEvaluation -> NutritionObservation",
            "logical_observations": table_counts["observations"],
            "source_provenance_rows": table_counts["source_provenance"],
        },
        "observation_status": observation_status,
        "terminology_review": {
            "candidate_terms_requiring_review": len(candidate_terms),
            "candidate_terms_by_category": dict(sorted(candidate_by_category.items())),
            "inactive_candidate_dictionary_rows": stored_candidate_rows - len(candidate_terms),
            "note": "Counts are unique active candidate terms, not data rows, records, or repeated observation occurrences.",
        },
        "measurement_contexts": measurement_contexts,
        "observation_status_accounting": {
            "expected_database_rows": observations,
            "accounted_rows": accounted,
            "complete": accounted == observations,
        },
        "distinct_counts": distinct_counts,
        "dictionary_status": dict(dictionary_status),
        "unit_resolution": {
            "explicit_raw_unit_observations": explicit_unit_observations,
            "methods": unit_methods,
            "canonical_units": canonical_unit_counts,
            "unresolved_observation_count": unit_methods.get("unresolved", 0),
            "unresolved_metric_contexts": unresolved_units,
            "reported_values_changed_by_unit_resolution": 0,
            "note": "Unit resolution normalizes or fills unit labels only; it never rescales reported values.",
        },
        "quality_checks": {
            "foreign_key_errors": foreign_key_errors,
            "duplicate_dois": duplicate_dois,
            "missing_dois_allowed": missing_doi,
            "missing_titles": missing_title,
            "ingredient_source_sheets": source_sheets,
            "disallowed_diet_or_other_source_sheets": disallowed_sheets,
            "diet_sheet_exclusion_passed": not disallowed_sheets,
            "historical_asparitc_canonical_observations": historical_asparitc,
            "unsafe_asparagine_merges": unsafe_asparagine_merge,
            "name_separation_passed": historical_asparitc == 0 and unsafe_asparagine_merge == 0,
        },
        "release_readiness": {
            "structural_checks_passed": (
                foreign_key_errors == 0 and duplicate_dois == 0 and missing_title == 0
                and not disallowed_sheets and historical_asparitc == 0 and unsafe_asparagine_merge == 0
            ),
            "data_validation_complete": observation_status.get("held", 0) == 0 and observation_status.get("rejected", 0) == 0,
            "terminology_curation_complete": len(candidate_terms) == 0,
            "rights_review_complete": False,
            "note": "Data validation and terminology mapping are tracked independently; structural checks can pass while terminology and rights review remain pending.",
        },
    }


def write_release_statistics(database_path: str | Path, output_path: str | Path) -> dict[str, object]:
    statistics = collect_release_statistics(database_path)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(statistics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return statistics
