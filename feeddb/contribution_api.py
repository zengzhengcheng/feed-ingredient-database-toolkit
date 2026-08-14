from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from .contribution_batch import (
    AnimalGridRow, BatchCheck, ContributionBatch, IngredientGridRow, MetricSpec,
    NutritionGridRow, save_contribution_batch, validate_contribution_batch,
)


FORMAT_VERSION = "1.0"

TOP_LEVEL_FIELDS = {
    "format_version", "metadata", "composition_metrics", "ingredients",
    "animals", "nutrition_metrics", "nutrition_rows",
}


def _check_object_fields(
    value: Mapping[str, Any], label: str, required: set[str], allowed: set[str]
) -> None:
    missing = sorted(required - set(value))
    extra = sorted(set(value) - allowed)
    if missing:
        raise ValueError(f"{label} 缺少必填字段：{', '.join(missing)}")
    if extra:
        raise ValueError(f"{label} 包含未定义字段：{', '.join(extra)}")


def _require_nonempty_string(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} 必须是非空字符串")


def _optional_string(value: Any, label: str, allow_number: bool = False) -> None:
    if value is None or isinstance(value, str):
        return
    if allow_number and isinstance(value, (int, float)) and not isinstance(value, bool):
        return
    expected = "字符串、数字或 null" if allow_number else "字符串或 null"
    raise ValueError(f"{label} 必须是{expected}")


def _validate_value_array(value: Any, label: str) -> None:
    if not isinstance(value, list):
        raise ValueError(f"{label} 必须是 JSON array")
    for index, item in enumerate(value, start=1):
        if item is not None and not (
            isinstance(item, str) or isinstance(item, (int, float)) and not isinstance(item, bool)
        ):
            raise ValueError(f"{label} 第 {index} 项必须是字符串、数字或 null")


def validate_payload_contract(payload: Mapping[str, Any]) -> None:
    """Enforce the public JSON schema before any coercion into GUI text fields."""
    _check_object_fields(payload, "JSON root", TOP_LEVEL_FIELDS, TOP_LEVEL_FIELDS)
    if payload.get("format_version") != FORMAT_VERSION:
        raise ValueError(f"仅支持 format_version {FORMAT_VERSION}")

    metadata = _mapping(payload.get("metadata"), "metadata")
    metadata_fields = {"title", "doi", "journal", "publication_year", "species", "country"}
    _check_object_fields(metadata, "metadata", {"title"}, metadata_fields)
    _require_nonempty_string(metadata.get("title"), "metadata.title")
    for name in ("doi", "journal", "species", "country"):
        if name in metadata:
            _optional_string(metadata[name], f"metadata.{name}")
    if "publication_year" in metadata:
        _optional_string(metadata["publication_year"], "metadata.publication_year", allow_number=True)

    specifications = (
        ("composition_metrics", {"metric"}, {"metric", "unit", "basis"}),
        ("ingredients", {"ingredient_key", "raw_name", "values"}, {
            "ingredient_key", "raw_name", "source_ingredient_no", "feed_type", "processing_technic",
            "place_of_origin", "reported_basis", "dry_matter_value", "values",
        }),
        ("animals", {"animal_group_key"}, {
            "animal_group_key", "trial_no", "species", "breed", "sex", "animal_type", "period",
            "age_days", "age_weeks", "body_weight_kg",
        }),
        ("nutrition_metrics", {"metric", "context"}, {"metric", "context", "unit", "basis"}),
        ("nutrition_rows", {"ingredient_key", "animal_group_key", "values"}, {
            "ingredient_key", "animal_group_key", "values",
        }),
    )
    for array_name, required, allowed in specifications:
        array = payload.get(array_name)
        if not isinstance(array, list):
            raise ValueError(f"{array_name} 必须是 JSON array")
        for index, raw_item in enumerate(array, start=1):
            item = _mapping(raw_item, f"{array_name} 第 {index} 项")
            label = f"{array_name} 第 {index} 项"
            _check_object_fields(item, label, required, allowed)
            for key in required - {"values"}:
                _require_nonempty_string(item.get(key), f"{label}.{key}")
            if "values" in required:
                _validate_value_array(item.get("values"), f"{label}.values")

    allowed_contexts = {"energy", "AID", "SID", "ATTD", "STTD", "CATTD"}
    for index, item in enumerate(payload["nutrition_metrics"], start=1):
        if item["context"] not in allowed_contexts:
            raise ValueError(
                f"nutrition_metrics 第 {index} 项.context 必须是：{', '.join(sorted(allowed_contexts))}"
            )

    for array_name in ("composition_metrics", "nutrition_metrics"):
        for index, item in enumerate(payload[array_name], start=1):
            for field in ("unit", "basis"):
                if field in item:
                    _optional_string(item[field], f"{array_name} 第 {index} 项.{field}")
    for index, item in enumerate(payload["ingredients"], start=1):
        for field in ("source_ingredient_no", "dry_matter_value"):
            if field in item:
                _optional_string(item[field], f"ingredients 第 {index} 项.{field}", allow_number=True)
        for field in ("feed_type", "processing_technic", "place_of_origin", "reported_basis"):
            if field in item:
                _optional_string(item[field], f"ingredients 第 {index} 项.{field}")
    for index, item in enumerate(payload["animals"], start=1):
        for field in ("trial_no", "age_days", "age_weeks", "body_weight_kg"):
            if field in item:
                _optional_string(item[field], f"animals 第 {index} 项.{field}", allow_number=True)
        for field in ("species", "breed", "sex", "animal_type", "period"):
            if field in item:
                _optional_string(item[field], f"animals 第 {index} 项.{field}")


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} 必须是 JSON object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{label} 必须是 JSON array")
    return value


def _values(value: Any, label: str) -> list[str]:
    return [_text(item) for item in _list(value, label)]


def batch_from_payload(payload: Mapping[str, Any]) -> ContributionBatch:
    """Convert the stable JSON exchange contract into the same batch used by the GUI."""
    validate_payload_contract(payload)
    metadata = _mapping(payload.get("metadata", {}), "metadata")
    composition_metrics = [
        MetricSpec(
            _text(_mapping(item, "composition_metrics item").get("metric")),
            "composition",
            _text(item.get("unit")),
            _text(item.get("basis")),
        )
        for item in _list(payload.get("composition_metrics"), "composition_metrics")
    ]
    ingredients = []
    for item in _list(payload.get("ingredients"), "ingredients"):
        row = _mapping(item, "ingredients item")
        ingredients.append(IngredientGridRow(
            ingredient_key=_text(row.get("ingredient_key")), raw_name=_text(row.get("raw_name")),
            source_ingredient_no=_text(row.get("source_ingredient_no")), feed_type=_text(row.get("feed_type")),
            processing_technic=_text(row.get("processing_technic")), place_of_origin=_text(row.get("place_of_origin")),
            reported_basis=_text(row.get("reported_basis")), dry_matter_value=_text(row.get("dry_matter_value")),
            values=_values(row.get("values"), "ingredients.values"),
        ))
    animals = []
    for item in _list(payload.get("animals"), "animals"):
        row = _mapping(item, "animals item")
        animals.append(AnimalGridRow(
            animal_group_key=_text(row.get("animal_group_key")), trial_no=_text(row.get("trial_no")),
            species=_text(row.get("species")), breed=_text(row.get("breed")), sex=_text(row.get("sex")),
            animal_type=_text(row.get("animal_type")), period=_text(row.get("period")),
            age_days=_text(row.get("age_days")), age_weeks=_text(row.get("age_weeks")),
            body_weight_kg=_text(row.get("body_weight_kg")),
        ))
    nutrition_metrics = []
    for item in _list(payload.get("nutrition_metrics"), "nutrition_metrics"):
        row = _mapping(item, "nutrition_metrics item")
        nutrition_metrics.append(MetricSpec(
            _text(row.get("metric")), _text(row.get("context")),
            _text(row.get("unit")), _text(row.get("basis")),
        ))
    nutrition_rows = []
    for item in _list(payload.get("nutrition_rows"), "nutrition_rows"):
        row = _mapping(item, "nutrition_rows item")
        nutrition_rows.append(NutritionGridRow(
            ingredient_key=_text(row.get("ingredient_key")),
            animal_group_key=_text(row.get("animal_group_key")),
            values=_values(row.get("values"), "nutrition_rows.values"),
        ))
    batch = ContributionBatch(
        title=_text(metadata.get("title")), doi=_text(metadata.get("doi")),
        journal=_text(metadata.get("journal")), publication_year=_text(metadata.get("publication_year")),
        species=_text(metadata.get("species")), country=_text(metadata.get("country")),
        composition_metrics=composition_metrics, ingredients=ingredients, animals=animals,
        nutrition_metrics=nutrition_metrics, nutrition_rows=nutrition_rows,
    )
    _validate_matrix_widths(batch)
    return batch


def _validate_matrix_widths(batch: ContributionBatch) -> None:
    for row_no, row in enumerate(batch.ingredients, start=1):
        if len(row.values) != len(batch.composition_metrics):
            raise ValueError(
                f"ingredients 第 {row_no} 行 values 有 {len(row.values)} 项，"
                f"但 composition_metrics 有 {len(batch.composition_metrics)} 项"
            )
    for row_no, row in enumerate(batch.nutrition_rows, start=1):
        if len(row.values) != len(batch.nutrition_metrics):
            raise ValueError(
                f"nutrition_rows 第 {row_no} 行 values 有 {len(row.values)} 项，"
                f"但 nutrition_metrics 有 {len(batch.nutrition_metrics)} 项"
            )


def validate_payload(database_path: str | Path, payload: Mapping[str, Any]) -> tuple[ContributionBatch, BatchCheck]:
    batch = batch_from_payload(payload)
    return batch, validate_contribution_batch(database_path, batch)


def import_payload(
    database_path: str | Path,
    payload: Mapping[str, Any],
    approve_new_terms: bool = False,
) -> dict[str, Any]:
    """Validated local interface for AI agents, scripts, and future UIs."""
    batch, check = validate_payload(database_path, payload)
    if check.errors:
        return {"saved": False, "validation": asdict(check)}
    result = save_contribution_batch(
        database_path, batch, approve_new_terms=approve_new_terms, source_prefix="ai-json",
    )
    return {"saved": True, "validation": asdict(check), "result": asdict(result)}


def read_payload(path: str | Path) -> Mapping[str, Any]:
    def no_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"JSON object 中字段重复：{key}")
            result[key] = value
        return result

    if str(path) == "-":
        import sys
        value = json.load(sys.stdin, object_pairs_hook=no_duplicate_keys)
    else:
        value = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=no_duplicate_keys)
    return _mapping(value, "JSON root")
