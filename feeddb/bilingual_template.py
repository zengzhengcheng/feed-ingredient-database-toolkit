"""Parser for the bilingual two-data-sheet contribution workbook."""

from __future__ import annotations

from collections import Counter, OrderedDict, defaultdict
from pathlib import Path
from typing import Any

from .contribution_batch import (
    AnimalGridRow,
    ContributionBatch,
    IngredientGridRow,
    MetricSpec,
    NutritionGridRow,
)

ARTICLE_METADATA_SHEET = "Article Metadata"
INGREDIENT_DATA_SHEET = "Ingredient Data"
INSTRUCTIONS_SHEET = "Instructions_说明"
DATA_SHEETS = (ARTICLE_METADATA_SHEET, INGREDIENT_DATA_SHEET)
ALLOWED_TEMPLATE_SHEETS = {ARTICLE_METADATA_SHEET, INGREDIENT_DATA_SHEET, INSTRUCTIONS_SHEET}

ARTICLE_METADATA_FIELDS = (
    "study_key", "doi", "title", "journal", "publication_year", "species", "country",
)
INGREDIENT_DATA_FIELDS = (
    "record_type", "study_key", "trial_key", "source_trial_no", "treatment",
    "ingredient_key", "source_ingredient_no", "raw_name", "feed_type",
    "processing_technic", "place_of_origin", "reported_basis", "dry_matter_value",
    "animal_group_key", "species", "breed", "sex", "animal_type", "period",
    "age_days", "age_weeks", "body_weight_kg", "measurement_context",
    "raw_metric", "value", "unit", "basis", "qualifier", "method", "note",
)
RECORD_TYPES = {"trial", "ingredient", "animal_group", "composition", "nutrition"}


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _records(worksheet) -> list[dict[str, Any]]:
    headers = [_clean(cell.value) for cell in worksheet[1]]
    return [
        dict(zip(headers, row))
        for row in worksheet.iter_rows(min_row=2, values_only=True)
        if any(value is not None and _clean(value) for value in row)
    ]


def is_bilingual_template(workbook) -> bool:
    if not set(DATA_SHEETS).issubset(workbook.sheetnames):
        return False
    headers = {_clean(cell.value) for cell in workbook[INGREDIENT_DATA_SHEET][1]}
    return "record_type" in headers


def validate_sheet_contract(workbook) -> None:
    if not is_bilingual_template(workbook):
        raise ValueError(
            "Bilingual contribution workbooks require Article Metadata and Ingredient Data sheets"
        )
    unexpected = [name for name in workbook.sheetnames if name not in ALLOWED_TEMPLATE_SHEETS]
    if unexpected:
        raise ValueError(f"Unexpected sheet(s): {', '.join(unexpected)}")
    metadata_headers = [_clean(cell.value) for cell in workbook[ARTICLE_METADATA_SHEET][1]]
    data_headers = [_clean(cell.value) for cell in workbook[INGREDIENT_DATA_SHEET][1]]
    if metadata_headers != list(ARTICLE_METADATA_FIELDS):
        raise ValueError("Article Metadata field order does not match the published template")
    if data_headers != list(INGREDIENT_DATA_FIELDS):
        raise ValueError("Ingredient Data field order does not match the published template")


def _metric_occurrences(
    rows: list[tuple[str, tuple[str, str, str, str], Any]],
) -> tuple[list[tuple[str, str, str, str, int]], dict[str, dict[tuple[str, str, str, str, int], str]]]:
    order: OrderedDict[tuple[str, str, str, str, int], None] = OrderedDict()
    values: dict[str, dict[tuple[str, str, str, str, int], str]] = defaultdict(dict)
    seen: Counter[tuple[str, tuple[str, str, str, str]]] = Counter()
    for owner, metric_key, raw_value in rows:
        seen[(owner, metric_key)] += 1
        occurrence = (*metric_key, seen[(owner, metric_key)])
        order.setdefault(occurrence, None)
        values[owner][occurrence] = _clean(raw_value)
    return list(order), values


def batch_from_bilingual_workbook(workbook, source: Path) -> ContributionBatch:
    """Convert the two data sheets to the same ContributionBatch used by JSON/GUI."""
    validate_sheet_contract(workbook)
    metadata_rows = _records(workbook[ARTICLE_METADATA_SHEET])
    if len(metadata_rows) != 1:
        raise ValueError(
            f"Article Metadata must contain exactly one populated record; found {len(metadata_rows)}"
        )
    metadata = metadata_rows[0]
    title = _clean(metadata.get("title"))
    if not title:
        raise ValueError("Article Metadata.title is required; DOI may be blank")
    study_key = _clean(metadata.get("study_key"))
    if not study_key:
        raise ValueError("Article Metadata.study_key is required")

    rows = _records(workbook[INGREDIENT_DATA_SHEET])
    typed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, row in enumerate(rows, start=2):
        record_type = _clean(row.get("record_type")).casefold()
        if record_type not in RECORD_TYPES:
            raise ValueError(f"Ingredient Data!A{index}: unsupported record_type {record_type!r}")
        row_study = _clean(row.get("study_key")) or study_key
        if row_study.casefold() != study_key.casefold():
            raise ValueError(f"Ingredient Data!B{index}: study_key does not match Article Metadata")
        typed[record_type].append(row)

    trials: dict[str, dict[str, Any]] = {}
    for row in typed["trial"]:
        key = _clean(row.get("trial_key"))
        if not key or key.casefold() in trials:
            raise ValueError("Each trial row requires a unique trial_key")
        trials[key.casefold()] = row

    ingredient_records: dict[str, dict[str, Any]] = {}
    for row in typed["ingredient"]:
        key = _clean(row.get("ingredient_key"))
        if not key or key.casefold() in ingredient_records:
            raise ValueError("Each ingredient row requires a unique ingredient_key")
        if not _clean(row.get("raw_name")):
            raise ValueError(f"Ingredient {key} requires raw_name")
        ingredient_records[key.casefold()] = row

    animal_records: dict[str, dict[str, Any]] = {}
    for row in typed["animal_group"]:
        key = _clean(row.get("animal_group_key"))
        trial_key = _clean(row.get("trial_key"))
        if not key or key.casefold() in animal_records:
            raise ValueError("Each animal_group row requires a unique animal_group_key")
        if trial_key.casefold() not in trials:
            raise ValueError(f"Animal group {key} references unknown trial_key {trial_key}")
        animal_records[key.casefold()] = row

    composition_long: list[tuple[str, tuple[str, str, str, str], Any]] = []
    for row in typed["composition"]:
        owner = _clean(row.get("ingredient_key"))
        metric = _clean(row.get("raw_metric"))
        if owner.casefold() not in ingredient_records or not metric:
            raise ValueError("Composition rows require a known ingredient_key and raw_metric")
        composition_long.append(
            (owner, (metric, "composition", _clean(row.get("unit")), _clean(row.get("basis"))), row.get("value"))
        )
    composition_order, composition_values = _metric_occurrences(composition_long)

    nutrition_long: list[tuple[str, tuple[str, str, str, str], Any]] = []
    for row in typed["nutrition"]:
        ingredient_key = _clean(row.get("ingredient_key"))
        animal_key = _clean(row.get("animal_group_key"))
        context = _clean(row.get("measurement_context"))
        metric = _clean(row.get("raw_metric"))
        if ingredient_key.casefold() not in ingredient_records:
            raise ValueError(f"Nutrition row references unknown ingredient_key {ingredient_key}")
        if animal_key.casefold() not in animal_records:
            raise ValueError(f"Nutrition row references unknown animal_group_key {animal_key}")
        if not context or not metric:
            raise ValueError("Nutrition rows require measurement_context and raw_metric")
        owner = f"{ingredient_key}\u241f{animal_key}"
        nutrition_long.append(
            (owner, (metric, context, _clean(row.get("unit")), _clean(row.get("basis"))), row.get("value"))
        )
    nutrition_order, nutrition_values = _metric_occurrences(nutrition_long)

    return ContributionBatch(
        title=title,
        doi=_clean(metadata.get("doi")),
        journal=_clean(metadata.get("journal")),
        publication_year=_clean(metadata.get("publication_year")),
        species=_clean(metadata.get("species")),
        country=_clean(metadata.get("country")),
        composition_metrics=[
            MetricSpec(metric, context, unit, basis)
            for metric, context, unit, basis, _ in composition_order
        ],
        ingredients=[
            IngredientGridRow(
                ingredient_key=_clean(row.get("ingredient_key")),
                raw_name=_clean(row.get("raw_name")),
                source_ingredient_no=_clean(row.get("source_ingredient_no")),
                feed_type=_clean(row.get("feed_type")),
                processing_technic=_clean(row.get("processing_technic")),
                place_of_origin=_clean(row.get("place_of_origin")),
                reported_basis=_clean(row.get("reported_basis")),
                dry_matter_value=_clean(row.get("dry_matter_value")),
                values=[composition_values[_clean(row.get("ingredient_key"))].get(key, "") for key in composition_order],
            )
            for row in ingredient_records.values()
        ],
        animals=[
            AnimalGridRow(
                animal_group_key=_clean(row.get("animal_group_key")),
                trial_no=_clean(trials[_clean(row.get("trial_key")).casefold()].get("source_trial_no"))
                or _clean(row.get("trial_key")),
                species=_clean(row.get("species")) or _clean(metadata.get("species")),
                breed=_clean(row.get("breed")),
                sex=_clean(row.get("sex")),
                animal_type=_clean(row.get("animal_type")),
                period=_clean(row.get("period")),
                age_days=_clean(row.get("age_days")),
                age_weeks=_clean(row.get("age_weeks")),
                body_weight_kg=_clean(row.get("body_weight_kg")),
            )
            for row in animal_records.values()
        ],
        nutrition_metrics=[
            MetricSpec(metric, context, unit, basis)
            for metric, context, unit, basis, _ in nutrition_order
        ],
        nutrition_rows=[
            NutritionGridRow(owner.split("\u241f", 1)[0], owner.split("\u241f", 1)[1], [values.get(key, "") for key in nutrition_order])
            for owner, values in nutrition_values.items()
        ],
    )
