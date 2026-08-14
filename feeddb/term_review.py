from __future__ import annotations

from dataclasses import dataclass
from difflib import get_close_matches
from pathlib import Path

from .database import (
    apply_dictionary_mapping,
    connect,
    upsert_metric_catalog,
)


@dataclass(frozen=True)
class CandidateTerm:
    dictionary_id: int
    category: str
    context: str
    raw_term: str
    proposed_term: str
    occurrences: int
    source_examples: tuple[str, ...]
    suggestions: tuple[str, ...]


def _candidate_row(conn, dictionary_id: int):
    row = conn.execute(
        """SELECT dictionary_id,category,context,raw_term,canonical_term,status
           FROM dictionaries WHERE dictionary_id=?""",
        (int(dictionary_id),),
    ).fetchone()
    if row is None or row["status"] != "candidate":
        raise ValueError("该候选词不存在，或已经完成核对。")
    return row


def _occurrence_locations(
    conn, category: str, context: str, raw_term: str, proposed_term: str = "",
) -> list[tuple[str, str, object]]:
    parameters = (raw_term,)
    if category == "ingredient":
        sql = """SELECT source_workbook,source_sheet,first_source_column
                 FROM ingredient_samples
                 WHERE name_mapping_status='candidate' AND CASEFOLD(raw_name)=CASEFOLD(?)"""
    elif category == "metric":
        sql = """SELECT source_workbook,source_sheet,source_row
                 FROM component_observations WHERE metric_mapping_status='candidate'
                   AND (CASEFOLD(raw_metric)=CASEFOLD(?) OR CASEFOLD(canonical_metric)=CASEFOLD(?))
                 UNION ALL
                 SELECT source_workbook,source_sheet,source_row
                 FROM nutrition_observations WHERE metric_mapping_status='candidate'
                   AND (CASEFOLD(raw_metric)=CASEFOLD(?) OR CASEFOLD(canonical_metric)=CASEFOLD(?))"""
        proposed = proposed_term or raw_term
        parameters = (raw_term, proposed, raw_term, proposed)
    elif category == "unit":
        sql = """SELECT source_workbook,source_sheet,source_row FROM component_observations
                 WHERE unit_mapping_status='candidate' AND CASEFOLD(raw_unit)=CASEFOLD(?)
                 UNION ALL
                 SELECT source_workbook,source_sheet,source_row FROM nutrition_observations
                 WHERE unit_mapping_status='candidate' AND CASEFOLD(raw_unit)=CASEFOLD(?)"""
        parameters = (raw_term, raw_term)
    elif category == "basis":
        sql = """SELECT source_workbook,source_sheet,source_row FROM component_observations
                 WHERE basis_mapping_status='candidate' AND CASEFOLD(raw_basis)=CASEFOLD(?)
                 UNION ALL
                 SELECT source_workbook,source_sheet,source_row FROM nutrition_observations
                 WHERE basis_mapping_status='candidate' AND CASEFOLD(raw_basis)=CASEFOLD(?)"""
        parameters = (raw_term, raw_term)
    elif category == "species":
        sql = """SELECT source_workbook,'Studies',study_id FROM studies
                 WHERE species_mapping_status='candidate' AND CASEFOLD(raw_species)=CASEFOLD(?)
                 UNION ALL
                 SELECT source_workbook,source_sheet,source_column FROM animal_groups
                 WHERE species_mapping_status='candidate' AND CASEFOLD(raw_species)=CASEFOLD(?)"""
        parameters = (raw_term, raw_term)
    else:
        return []
    return [tuple(row) for row in conn.execute(sql, parameters).fetchall()]


def _candidate_activity_rows(conn, category: str = "", limit: int = 1000) -> list[dict[str, object]]:
    sql = """SELECT dictionary_id,category,context,raw_term,canonical_term
             FROM dictionaries WHERE status='candidate'"""
    parameters: list[object] = []
    if category:
        sql += " AND category=?"
        parameters.append(category)
    sql += " ORDER BY category,context,raw_term"
    result: list[dict[str, object]] = []
    rows = conn.execute(sql, parameters).fetchall()
    metric_contexts: dict[tuple[str, str, str], set[str]] = {}
    for item in rows:
        item_key = (
            str(item["category"]), str(item["raw_term"]).casefold(),
            str(item["canonical_term"]).casefold(),
        )
        if item["category"] == "metric":
            metric_contexts.setdefault(item_key, set()).add(str(item["context"]))
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (
            str(row["category"]), str(row["raw_term"]).casefold(),
            str(row["canonical_term"]).casefold(),
        )
        if row["category"] == "metric" and key in seen:
            continue
        seen.add(key)
        locations = _occurrence_locations(
            conn, row["category"], row["context"], row["raw_term"], row["canonical_term"],
        )
        if not locations:
            continue
        examples: list[str] = []
        for workbook, sheet, position in locations:
            label = f"{workbook} | {sheet or '?'}!{position or '?'}"
            if label not in examples:
                examples.append(label)
            if len(examples) == 3:
                break
        result.append({
            "dictionary_id": int(row["dictionary_id"]),
            "category": str(row["category"]),
            "context": (
                " / ".join(sorted(metric_contexts[key], key=str.casefold))
                if row["category"] == "metric" else str(row["context"])
            ),
            "raw_term": str(row["raw_term"]),
            "proposed_term": str(row["canonical_term"]),
            "occurrences": len(locations),
            "source_examples": tuple(examples),
        })
        if len(result) >= int(limit):
            break
    return result


def collect_candidate_term_activity(
    database_path: str | Path, category: str = "", limit: int = 1000,
) -> list[dict[str, object]]:
    """Return active unique candidate terms; measurement contexts are metadata, never terms."""
    with connect(database_path) as conn:
        return _candidate_activity_rows(conn, category, limit)


def _standard_terms(conn, category: str, context: str) -> list[str]:
    parameters: tuple[object, ...]
    if category == "metric":
        rows = conn.execute(
            """SELECT canonical_metric AS term FROM metric_catalog WHERE active=1
               UNION
               SELECT canonical_term AS term FROM dictionaries
               WHERE category='metric' AND status='approved'
               ORDER BY term""",
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT DISTINCT canonical_term AS term FROM dictionaries
               WHERE category=? AND status='approved' ORDER BY term""",
            (category,),
        ).fetchall()
    return [str(row[0]) for row in rows if str(row[0]).strip()]


def _suggestions(conn, category: str, context: str, raw_term: str) -> tuple[str, ...]:
    standards = _standard_terms(conn, category, context)
    by_fold = {term.casefold(): term for term in standards}
    if category == "metric":
        aliases = conn.execute(
            """SELECT raw_term,canonical_term FROM dictionaries
               WHERE category='metric' AND status='approved'""",
        ).fetchall()
    else:
        aliases = conn.execute(
            """SELECT raw_term,canonical_term FROM dictionaries
               WHERE category=? AND status='approved'""",
            (category,),
        ).fetchall()
    for alias, canonical in aliases:
        by_fold[str(alias).casefold()] = str(canonical)
    matches = get_close_matches(raw_term.casefold(), list(by_fold), n=12, cutoff=0.55)
    result: list[str] = []
    for match in matches:
        canonical = by_fold[match]
        if canonical not in result:
            result.append(canonical)
        if len(result) == 6:
            break
    return tuple(result)


def list_candidate_terms(
    database_path: str | Path,
    category: str = "",
    limit: int = 1000,
) -> list[CandidateTerm]:
    with connect(database_path) as conn:
        candidates = _candidate_activity_rows(conn, category, limit)
        result: list[CandidateTerm] = []
        for row in candidates:
            result.append(CandidateTerm(
                int(row["dictionary_id"]), row["category"], row["context"], row["raw_term"],
                row["proposed_term"], int(row["occurrences"]), tuple(row["source_examples"]),
                _suggestions(conn, row["category"], row["context"], row["raw_term"]),
            ))
        return result


def _approve_metric_variants(conn, row, canonical: str) -> None:
    if row["category"] != "metric":
        return
    parameters = (canonical, row["raw_term"], row["canonical_term"])
    for table in ("component_observations", "nutrition_observations"):
        conn.execute(
            f"""UPDATE {table} SET canonical_metric=?,metric_mapping_status='approved'
                WHERE metric_mapping_status='candidate'
                  AND (CASEFOLD(raw_metric)=CASEFOLD(?) OR CASEFOLD(canonical_metric)=CASEFOLD(?))""",
            parameters,
        )
    conn.execute(
        """UPDATE dictionaries SET canonical_term=?,status='approved',updated_at=CURRENT_TIMESTAMP
           WHERE category='metric' AND status='candidate'
             AND (CASEFOLD(raw_term)=CASEFOLD(?) OR CASEFOLD(canonical_term)=CASEFOLD(?))""",
        parameters,
    )


def resolve_candidate_as_alias(
    database_path: str | Path,
    dictionary_id: int,
    existing_canonical_term: str,
    note: str = "",
) -> None:
    canonical = existing_canonical_term.strip()
    if not canonical:
        raise ValueError("请选择一个已有标准项。")
    with connect(database_path) as conn:
        row = _candidate_row(conn, dictionary_id)
        standards = _standard_terms(conn, row["category"], row["context"])
        matched = next((term for term in standards if term.casefold() == canonical.casefold()), None)
        if matched is None:
            raise ValueError("该名称还不是已有标准项；如果它是新概念，请使用“建立新标准项”。")
        apply_dictionary_mapping(
            conn, row["category"], row["raw_term"], matched, "approved",
            note.strip() or "Candidate reviewed and mapped to an existing canonical term",
            row["context"],
        )
        _approve_metric_variants(conn, row, matched)


def create_standard_from_candidate(
    database_path: str | Path,
    dictionary_id: int,
    canonical_term: str,
    group_name: str = "",
    default_unit: str = "",
    default_basis: str = "",
    note: str = "",
) -> None:
    canonical = canonical_term.strip()
    if not canonical:
        raise ValueError("新标准名称不能为空。")
    with connect(database_path) as conn:
        row = _candidate_row(conn, dictionary_id)
        if row["category"] == "metric":
            group = group_name.strip()
            if not row["context"] or not group:
                raise ValueError("建立新组分/指标时必须指定语境和分组。")
            upsert_metric_catalog(
                conn, row["context"], group, canonical, default_unit, default_basis, True,
                note.strip() or "Created while reviewing an unmatched metric",
            )
        apply_dictionary_mapping(
            conn, row["category"], row["raw_term"], canonical, "approved",
            note.strip() or "Candidate confirmed as a new canonical term",
            row["context"],
        )
        _approve_metric_variants(conn, row, canonical)
