from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


TOP_LEVEL_SECTIONS = {
    "基础数据": "基础数据",
    "饲料组成": "饲料组成",
    "特殊成分": "特殊成分",
    "营养价值": "营养价值",
    "能量": "能量",
    "营养物质消化率": "营养物质消化率",
    "非表观营养价值": "非表观营养价值",
    # One historical workbook uses this label for the same AID/SID/CATTD
    # hierarchy. Preserve the raw label but give it the same structural role.
    "非常规营养价值": "非表观营养价值",
}

DIGESTIBILITY_CONTEXTS = {
    "aid": "AID",
    "sid": "SID",
    "attd": "ATTD",
    "sttd": "STTD",
}

ANIMAL_BASE_FIELDS = {
    "animal",
    "animalname",
    "animaltype",
    "breed",
    "cunyi",
    "days",
    "gender",
    "period",
    "trial no",
    "trial number",
    "type",
    "weeks",
    "weight",
    "试验编号",
}

INGREDIENT_BASE_FIELDS = {
    "dry base",
    "dry matter",
    "feed type",
    "feedtype",
    "ingredient no",
    "ingredient number",
    "place of origin",
    "processing technic",
    "source",
    "日粮处理",
    "原料编号",
}


def clean_text(value: object) -> str:
    return "" if value is None else str(value).strip()


def normalize_label(value: object) -> str:
    """Normalize labels as the historical ArticleClass did, without losing raw text."""
    text = clean_text(value).casefold()
    for character in ("-", "–", "_", "‘", "’"):
        text = text.replace(character, " ")
    text = text.replace("..", ".")
    return " ".join(text.split())


@dataclass(frozen=True)
class StructuralRow:
    source_row: int
    raw_label: str
    normalized_label: str
    role: str
    section: str
    subsection: str | None = None


@dataclass(frozen=True)
class LegacyObservation:
    source_row: int
    source_column: int
    raw_metric: str
    normalized_metric: str
    value: Any
    section: str
    subsection: str | None
    measurement_context: str | None


@dataclass
class LegacyIngredientColumn:
    source_column: int
    raw_name: str
    source_ingredient_no: str
    trial_no: str
    ingredient_fields: dict[str, Any] = field(default_factory=dict)
    animal_fields: dict[str, Any] = field(default_factory=dict)
    other_base_fields: dict[str, Any] = field(default_factory=dict)
    observations: list[LegacyObservation] = field(default_factory=list)

    @property
    def reported_basis(self) -> str:
        value = self.ingredient_fields.get("dry base")
        if value is True or normalize_label(value) in {"true", "dry", "dry matter", "dm"}:
            return "dry matter"
        if value is False or normalize_label(value) in {"false", "as fed", "feed", "fresh"}:
            return "as-fed"
        return ""


@dataclass
class LegacySheetData:
    ingredients: list[LegacyIngredientColumn]
    structural_rows: list[StructuralRow]


def _used_width(worksheet) -> int:
    """Return the historical contiguous header width, with a safe worksheet fallback."""
    width = 0
    for column in range(1, worksheet.max_column + 1):
        if worksheet.cell(1, column).value is None:
            break
        width = column
    return max(width, 1)


def _row_is_subsection(worksheet, row: int, width: int) -> bool:
    second_value = worksheet.cell(row, 2).value if width >= 2 else None
    if normalize_label(second_value) == "data":
        return True
    values = [worksheet.cell(row, column).value for column in range(2, width + 1)]
    return bool(values) and all(value is None for value in values)


def _measurement_context(section: str, subsection: str | None) -> str | None:
    normalized_subsection = normalize_label(subsection)
    if section == "能量":
        return "energy"
    if section == "营养物质消化率":
        if normalized_subsection:
            return DIGESTIBILITY_CONTEXTS.get(normalized_subsection, normalized_subsection.upper())
        return "ATTD"
    if section == "非表观营养价值":
        if not normalized_subsection:
            return None
        return DIGESTIBILITY_CONTEXTS.get(normalized_subsection, normalized_subsection.upper())
    return "composition"


def _store_base_value(ingredient: LegacyIngredientColumn, label: str, value: Any) -> None:
    normalized = normalize_label(label)
    if normalized == "feed type":
        normalized = "feedtype"
    if normalized in ANIMAL_BASE_FIELDS:
        ingredient.animal_fields[normalized] = value
    elif normalized in INGREDIENT_BASE_FIELDS or normalized == "feedtype":
        ingredient.ingredient_fields[normalized] = value
    else:
        ingredient.other_base_fields[normalized] = value


def parse_legacy_ingredient_sheet(worksheet) -> LegacySheetData:
    """Parse one historical 成分及营养价值数据 sheet into explicit structure.

    The function deliberately has no database or dictionary dependency. It restores
    the historical section/subsection state machine first, then emits observations.
    """
    width = _used_width(worksheet)
    if width < 2:
        return LegacySheetData([], [])

    ingredients = [
        LegacyIngredientColumn(
            source_column=column,
            raw_name=clean_text(worksheet.cell(1, column).value),
            source_ingredient_no=str(column - 1),
            trial_no="",
        )
        for column in range(2, width + 1)
        if clean_text(worksheet.cell(1, column).value)
    ]
    by_column = {ingredient.source_column: ingredient for ingredient in ingredients}

    section = "基础数据"
    subsection: str | None = None
    structural_rows: list[StructuralRow] = []

    for row in range(2, worksheet.max_row + 1):
        raw_label = clean_text(worksheet.cell(row, 1).value)
        if not raw_label:
            continue
        normalized = normalize_label(raw_label)

        if normalized in TOP_LEVEL_SECTIONS:
            section = TOP_LEVEL_SECTIONS[normalized]
            subsection = None
            structural_rows.append(
                StructuralRow(row, raw_label, normalized, "section", section)
            )
            continue

        if _row_is_subsection(worksheet, row, width):
            subsection = raw_label
            structural_rows.append(
                StructuralRow(row, raw_label, normalized, "subsection", section, subsection)
            )
            continue

        for column, ingredient in by_column.items():
            value = worksheet.cell(row, column).value
            if value is None or clean_text(value) == "":
                continue
            if normalize_label(value) == "data":
                continue

            if section == "基础数据":
                _store_base_value(ingredient, raw_label, value)
                if normalized in {"原料编号", "ingredient no", "ingredient number"}:
                    ingredient.source_ingredient_no = clean_text(value)
                elif normalized in {"试验编号", "trial no", "trial number"}:
                    ingredient.trial_no = clean_text(value)
                continue

            ingredient.observations.append(
                LegacyObservation(
                    source_row=row,
                    source_column=column,
                    raw_metric=raw_label,
                    normalized_metric=normalized,
                    value=value,
                    section=section,
                    subsection=subsection,
                    measurement_context=_measurement_context(section, subsection),
                )
            )

    return LegacySheetData(ingredients, structural_rows)
