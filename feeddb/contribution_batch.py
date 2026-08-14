from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .database import (
    canonicalize, connect, initialize, lookup_dictionary, seed_dictionaries,
    suggest_dictionary_terms, upsert_dictionary,
    validation_dictionary_connection,
)
from .excel_io import (
    _add_provenance, _clean, _get_or_create_trial, _numeric,
    _observation_mapping, _upsert_study, _value_parts,
)
from .unit_resolution import DEFAULT_UNIT_RESOLVER


@dataclass(frozen=True)
class MetricSpec:
    raw_metric: str
    context: str = "composition"
    unit: str = ""
    basis: str = ""
    source_unit: str | None = None
    unit_resolution_method: str = ""
    unit_rule_reference: str = ""

    @property
    def label(self) -> str:
        metric_name, _embedded_unit = DEFAULT_UNIT_RESOLVER.split_embedded_unit(self.raw_metric)
        details = " | ".join(value for value in (self.unit, self.basis) if value)
        prefix = "" if self.context == "composition" else f"{self.context} · "
        return f"{prefix}{metric_name}" + (f"\n{details}" if details else "")


@dataclass
class IngredientGridRow:
    ingredient_key: str
    raw_name: str
    source_ingredient_no: str = ""
    feed_type: str = ""
    processing_technic: str = ""
    place_of_origin: str = ""
    reported_basis: str = ""
    dry_matter_value: str = ""
    values: list[str] = field(default_factory=list)


@dataclass
class AnimalGridRow:
    animal_group_key: str
    trial_no: str = ""
    species: str = ""
    breed: str = ""
    sex: str = ""
    animal_type: str = ""
    period: str = ""
    age_days: str = ""
    age_weeks: str = ""
    body_weight_kg: str = ""


@dataclass
class NutritionGridRow:
    ingredient_key: str
    animal_group_key: str
    values: list[str] = field(default_factory=list)


@dataclass
class ContributionBatch:
    title: str
    doi: str = ""
    journal: str = ""
    publication_year: str = ""
    species: str = ""
    country: str = ""
    composition_metrics: list[MetricSpec] = field(default_factory=list)
    ingredients: list[IngredientGridRow] = field(default_factory=list)
    animals: list[AnimalGridRow] = field(default_factory=list)
    nutrition_metrics: list[MetricSpec] = field(default_factory=list)
    nutrition_rows: list[NutritionGridRow] = field(default_factory=list)


@dataclass
class BatchCheck:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    mappings: list[str] = field(default_factory=list)
    new_terms: list[tuple[str, str, str]] = field(default_factory=list)


@dataclass
class BatchSaveResult:
    study_id: int
    ingredient_samples: int = 0
    animal_groups: int = 0
    component_observations: int = 0
    nutrition_observations: int = 0
    deduplicated_observations: int = 0


def _row_has_values(values: list[str]) -> bool:
    return any(_clean(value) for value in values)


def _animal_active(row: AnimalGridRow) -> bool:
    return any(
        _clean(value) for key, value in row.__dict__.items()
        if key != "animal_group_key"
    )


def _numeric_range_warning(location: str, metric: MetricSpec, value: object) -> str:
    number = _numeric(value)
    if number is None:
        return ""
    context = _clean(metric.context).casefold()
    resolved = DEFAULT_UNIT_RESOLVER.resolve(
        metric.raw_metric, metric.context, metric.raw_metric, metric.unit, reported_value=number,
    )
    unit = _clean(resolved.canonical_unit or metric.unit).replace("％", "%").casefold()
    reasons: list[str] = []
    if abs(number) >= 1e12:
        reasons.append("绝对值非常大")
    if unit == "%" and not 0 <= number <= 100:
        reasons.append("百分数超出0–100")
    if unit in {"kj/kg", "mj/kg", "g/kg", "mg/kg", "µg/kg", "μg/kg", "ug/kg"} and number < 0:
        reasons.append("浓度或能量为负数")
    if context == "energy" and unit == "kj/kg" and number > 100_000:
        reasons.append("kJ/kg能量值高于常见范围")
    if context == "energy" and unit == "kj/kg" and 0 < abs(number) < 500:
        reasons.append("kJ/kg能量值低于常见范围，可能实际为MJ/kg")
    if context == "energy" and unit == "mj/kg" and number > 100:
        reasons.append("MJ/kg能量值高于常见范围")
    if context == "energy" and unit == "kcal/kg" and not 500 <= abs(number) < 8_000:
        reasons.append("kcal/kg能量值不在常见量级")
    return f"{location} 数值 {value} 疑似异常（{'；'.join(reasons)}），请核对原文、单位和basis" if reasons else ""


def _term_specs(batch: ContributionBatch) -> list[tuple[str, str, str]]:
    terms: list[tuple[str, str, str]] = []
    terms.extend(("ingredient", "", _clean(row.raw_name)) for row in batch.ingredients if _clean(row.raw_name))
    for metric in [*batch.composition_metrics, *batch.nutrition_metrics]:
        metric_name, embedded_unit = DEFAULT_UNIT_RESOLVER.split_embedded_unit(metric.raw_metric)
        terms.append(("metric", metric.context, _clean(metric_name)))
        source_unit = metric.source_unit if metric.source_unit is not None else (embedded_unit or metric.unit)
        if _clean(source_unit):
            terms.append(("unit", "", _clean(source_unit)))
    unique: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for category, context, raw in terms:
        key = (category, context, raw.casefold())
        if raw and key not in seen:
            seen.add(key); unique.append((category, context, raw))
    return unique


def validate_contribution_batch(database_path: str | Path, batch: ContributionBatch) -> BatchCheck:
    check = BatchCheck()
    if not _clean(batch.title):
        check.errors.append("论文标题不能为空；DOI 可以留空")
    if _clean(batch.doi) and not _clean(batch.doi).casefold().startswith("10."):
        check.warnings.append("DOI 通常以 10. 开头，请确认录入是否正确")
    if _clean(batch.publication_year):
        try:
            year = int(float(batch.publication_year))
            if not 1800 <= year <= 2100:
                check.warnings.append("发表年份不在常见范围 1800–2100")
        except ValueError:
            check.errors.append("发表年份必须是整数")

    ingredient_keys: set[str] = set()
    active_ingredient_count = 0
    entered_observation_count = 0
    for row_no, row in enumerate(batch.ingredients, start=1):
        active = bool(_clean(row.raw_name) or _row_has_values(row.values))
        if not active:
            continue
        active_ingredient_count += 1
        key = _clean(row.ingredient_key)
        if not key:
            check.errors.append(f"原料表第 {row_no} 行缺少原料编号")
        elif key.casefold() in ingredient_keys:
            check.errors.append(f"原料编号重复：{key}")
        else:
            ingredient_keys.add(key.casefold())
        if not _clean(row.raw_name):
            check.errors.append(f"原料表第 {row_no} 行缺少原料名称")
        if _clean(row.dry_matter_value):
            value = _numeric(row.dry_matter_value)
            if value is None:
                check.errors.append(f"原料表第 {row_no} 行干物质值不是数字")
            elif not 0 <= value <= 100:
                check.warnings.append(f"原料表第 {row_no} 行干物质值超出 0–100，请确认单位")
        seen_component_values: set[tuple[str, str, str, str]] = set()
        for metric_no, value in enumerate(row.values):
            if _clean(value):
                entered_observation_count += 1
                number, raw_text = _value_parts(value)
                metric_spec = batch.composition_metrics[metric_no]
                metric_name = metric_spec.raw_metric
                signature = (
                    _clean(metric_name).casefold(), _clean(metric_spec.unit).casefold(),
                    _clean(metric_spec.basis or row.reported_basis).casefold(), _clean(value),
                )
                if signature in seen_component_values:
                    check.warnings.append(
                        f"原料表第 {row_no} 行指标 {metric_name} 存在完全重复值；"
                        "入库时合并为一个逻辑观察，但保留每个来源位置"
                    )
                seen_component_values.add(signature)
                if number is None:
                    check.warnings.append(f"原料表第 {row_no} 行指标 {metric_name} 不是纯数字，将保留为原始文本值")
                elif raw_text is not None:
                    check.warnings.append(f"原料表第 {row_no} 行指标 {metric_name} 含数字格式或不确定度；将同时保存数值和原始文本")
                range_warning = _numeric_range_warning(
                    f"原料表第 {row_no} 行指标 {metric_name}", batch.composition_metrics[metric_no], value
                )
                if range_warning:
                    check.warnings.append(range_warning)

    if active_ingredient_count == 0:
        check.errors.append("贡献文件至少需要一个有名称的原料")

    animal_keys: set[str] = set()
    for row_no, row in enumerate(batch.animals, start=1):
        active = _animal_active(row)
        if not active:
            continue
        key = _clean(row.animal_group_key)
        if not key:
            check.errors.append(f"动物表第 {row_no} 行缺少动物组编号")
        elif key.casefold() in animal_keys:
            check.errors.append(f"动物组编号重复：{key}")
        else:
            animal_keys.add(key.casefold())
        if not _clean(row.species) and not _clean(batch.species):
            check.warnings.append(f"动物表第 {row_no} 行没有动物种类")
        for label, value in (("日龄", row.age_days), ("周龄", row.age_weeks), ("体重", row.body_weight_kg)):
            if _clean(value) and _numeric(value) is None:
                check.warnings.append(f"动物表第 {row_no} 行{label}是范围或文本，将保留在动物备注中")
            elif _numeric(value) is not None and _numeric(value) < 0:
                check.warnings.append(f"动物表第 {row_no} 行{label}为负数，请核对原文和单位")

    for row_no, row in enumerate(batch.nutrition_rows, start=1):
        if not _row_has_values(row.values):
            continue
        if _clean(row.ingredient_key).casefold() not in ingredient_keys:
            check.errors.append(f"营养价值表第 {row_no} 行引用了不存在的原料：{row.ingredient_key}")
        if _clean(row.animal_group_key).casefold() not in animal_keys:
            check.errors.append(f"营养价值表第 {row_no} 行引用了不存在的动物组：{row.animal_group_key}")
        seen_nutrition_values: set[tuple[str, str, str, str, str]] = set()
        for metric_no, value in enumerate(row.values):
            if _clean(value):
                entered_observation_count += 1
                number, raw_text = _value_parts(value)
                metric_spec = batch.nutrition_metrics[metric_no]
                metric_name = metric_spec.raw_metric
                signature = (
                    _clean(metric_spec.context).casefold(), _clean(metric_name).casefold(),
                    _clean(metric_spec.unit).casefold(), _clean(metric_spec.basis).casefold(), _clean(value),
                )
                if signature in seen_nutrition_values:
                    check.warnings.append(
                        f"营养价值表第 {row_no} 行指标 {metric_name} 存在完全重复值；"
                        "入库时合并为一个逻辑观察，但保留每个来源位置"
                    )
                seen_nutrition_values.add(signature)
                if number is None:
                    check.warnings.append(f"营养价值表第 {row_no} 行指标 {metric_name} 不是纯数字，将保留为原始文本值")
                elif raw_text is not None:
                    check.warnings.append(f"营养价值表第 {row_no} 行指标 {metric_name} 含数字格式或不确定度；将同时保存数值和原始文本")
                range_warning = _numeric_range_warning(
                    f"营养价值表第 {row_no} 行指标 {metric_name}", batch.nutrition_metrics[metric_no], value
                )
                if range_warning:
                    check.warnings.append(range_warning)

    if entered_observation_count == 0:
        check.errors.append("贡献文件至少需要一个原料成分值或动物相关营养价值")

    with validation_dictionary_connection(database_path) as conn:
        for category, context, raw in _term_specs(batch):
            found = lookup_dictionary(conn, category, raw, context)
            if found:
                canonical, status = found
                if canonical.casefold() != raw.casefold() or status != "approved":
                    check.mappings.append(f"{category}{f'/{context}' if context else ''}: {raw} → {canonical}（{status}）")
                continue
            term = (category, context, raw)
            check.new_terms.append(term)
            suggestions = suggest_dictionary_terms(conn, category, raw, context)
            if suggestions:
                similar = "；".join(f"{alias} → {canonical}" for alias, canonical in suggestions)
                check.warnings.append(f"{raw} 未收录，可能接近：{similar}")
            else:
                check.warnings.append(f"{raw} 未收录；确认无误后可作为新规范名称加入词典")
        energy_decisions: dict[tuple[str, str, str], int] = {}
        for metric_no, metric in enumerate(batch.nutrition_metrics):
            if _clean(metric.context).casefold() != "energy" or _clean(metric.unit):
                continue
            found = lookup_dictionary(conn, "metric", metric.raw_metric, "energy")
            canonical_metric = found[0] if found else _clean(metric.raw_metric)
            for row in batch.nutrition_rows:
                if metric_no >= len(row.values) or not _clean(row.values[metric_no]):
                    continue
                number = _numeric(row.values[metric_no])
                resolution = DEFAULT_UNIT_RESOLVER.resolve(
                    metric.raw_metric, "energy", canonical_metric, "", "", number
                )
                key = (metric.raw_metric, resolution.canonical_unit, resolution.resolution_method)
                energy_decisions[key] = energy_decisions.get(key, 0) + 1
        for (metric_name, unit, method), count in energy_decisions.items():
            if unit:
                check.mappings.append(
                    f"能量单位判定: {metric_name} 的 {count} 个值 → {unit}（{method}；原始数值不变）"
                )
            else:
                check.warnings.append(
                    f"能量指标 {metric_name} 的 {count} 个值无法仅按数值量级确定单位，请在导入前选择单位"
                )
    return check


def _get_or_create_sample(conn, study_id: int, row: IngredientGridRow, source: str, source_row: int) -> tuple[int, bool]:
    raw_name = _clean(row.raw_name)
    canonical, status = canonicalize(conn, "ingredient", raw_name)
    source_no = _clean(row.source_ingredient_no) or _clean(row.ingredient_key)
    cursor = conn.execute(
        """INSERT OR IGNORE INTO ingredient_samples(
               study_id,source_ingredient_no,raw_name,canonical_name,name_mapping_status,feed_type,
               processing_technic,place_of_origin,reported_basis,dry_matter_value,
               source_workbook,source_sheet,first_source_column
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING ingredient_sample_id""",
        (
            study_id, source_no, raw_name, canonical, status, _clean(row.feed_type),
            _clean(row.processing_technic), _clean(row.place_of_origin), _clean(row.reported_basis),
            _numeric(row.dry_matter_value), source, "IngredientsComposition", source_row,
        ),
    )
    inserted = cursor.fetchone()
    if inserted:
        return int(inserted[0]), True
    sample_id = conn.execute(
        """SELECT ingredient_sample_id FROM ingredient_samples
           WHERE study_id=? AND source_workbook=? AND source_ingredient_no=? AND raw_name=?""",
        (study_id, source, source_no, raw_name),
    ).fetchone()[0]
    return int(sample_id), False


def _approve_new_terms(conn, batch: ContributionBatch) -> None:
    for category, context, raw in _term_specs(batch):
        if lookup_dictionary(conn, category, raw, context) is None:
            upsert_dictionary(
                conn, category, raw, raw, "approved",
                "Confirmed as a new canonical term during batch GUI entry", context,
            )


def save_contribution_batch(
    database_path: str | Path,
    batch: ContributionBatch,
    approve_new_terms: bool = False,
    source_prefix: str = "gui-contribution",
) -> BatchSaveResult:
    check = validate_contribution_batch(database_path, batch)
    if check.errors:
        raise ValueError("；".join(check.errors))
    initialize(database_path)
    source = f"{_clean(source_prefix) or 'contribution'}/{_clean(batch.title)}"
    publication_year = int(float(batch.publication_year)) if _clean(batch.publication_year) else None
    with connect(database_path) as conn:
        seed_dictionaries(conn)
        already_imported = conn.execute(
            "SELECT 1 FROM source_provenance WHERE source_workbook=? LIMIT 1",
            (source,),
        ).fetchone()
        if already_imported:
            raise ValueError(
                "同一来源和论文标题已经写入工作数据库。为避免重复，本次保存已停止；"
                "如内容已经修订，请使用新的可读版本标识后重新导入。"
            )
        if approve_new_terms:
            _approve_new_terms(conn, batch)
        study_id = _upsert_study(
            conn,
            {
                "title": batch.title, "doi": batch.doi, "journal": batch.journal,
                "publication_year": publication_year, "species": batch.species, "country": batch.country,
            },
            source,
        )
        result = BatchSaveResult(study_id=study_id)
        _add_provenance(conn, "study", study_id, source, "Metadata", 2, 1)
        sample_ids: dict[str, int] = {}

        for row_no, row in enumerate(batch.ingredients, start=2):
            if not (_clean(row.raw_name) or _row_has_values(row.values)):
                continue
            sample_id, inserted = _get_or_create_sample(conn, study_id, row, source, row_no)
            sample_ids[_clean(row.ingredient_key).casefold()] = sample_id
            result.ingredient_samples += int(inserted)
            _add_provenance(conn, "ingredient_sample", sample_id, source, "IngredientsComposition", row_no, 1)
            for metric_no, (metric, value) in enumerate(zip(batch.composition_metrics, row.values), start=9):
                if not _clean(value):
                    continue
                reviewed = metric.source_unit is not None
                mapped = _observation_mapping(
                    conn, metric.raw_metric, "composition",
                    metric.source_unit if reviewed else metric.unit,
                    metric.basis or row.reported_basis, value,
                    selected_unit=metric.unit if reviewed else None,
                    selected_method=metric.unit_resolution_method,
                    selected_rule=metric.unit_rule_reference,
                )
                parsed_metric, canonical_metric, metric_status, raw_unit, canonical_unit, unit_status, unit_method, unit_rule, canonical_basis, basis_status = mapped
                value_num, value_text = _value_parts(value)
                existing = conn.execute(
                    """SELECT component_observation_id FROM component_observations
                       WHERE ingredient_sample_id=? AND CASEFOLD(raw_metric)=CASEFOLD(?) AND value_num IS ? AND value_text IS ?
                         AND CASEFOLD(coalesce(raw_unit,''))=CASEFOLD(?) AND CASEFOLD(coalesce(raw_basis,''))=CASEFOLD(?)
                       ORDER BY component_observation_id LIMIT 1""",
                    (sample_id, metric.raw_metric, value_num, value_text, raw_unit, metric.basis or row.reported_basis),
                ).fetchone()
                if existing:
                    observation_id = int(existing[0]); result.deduplicated_observations += 1
                else:
                    observation_id = int(conn.execute(
                        """INSERT INTO component_observations(
                               ingredient_sample_id,raw_metric,canonical_metric,value_num,value_text,raw_unit,canonical_unit,
                               unit_resolution_method,unit_rule_reference,
                               raw_basis,canonical_basis,metric_mapping_status,unit_mapping_status,basis_mapping_status,
                               qualifier,section,subsection,validation_status,source_workbook,source_sheet,source_row,source_column
                           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING component_observation_id""",
                        (
                            sample_id, metric.raw_metric, canonical_metric, value_num, value_text, raw_unit, canonical_unit,
                            unit_method, unit_rule, metric.basis or row.reported_basis, canonical_basis,
                            metric_status, unit_status, basis_status,
                            "", "composition", "GUI batch", "accepted", source, "IngredientsComposition", row_no, metric_no,
                        ),
                    ).fetchone()[0])
                    result.component_observations += 1
                _add_provenance(conn, "component_observation", observation_id, source, "IngredientsComposition", row_no, metric_no)

        animal_ids: dict[str, int] = {}
        animal_trial_ids: dict[str, int] = {}
        for row_no, row in enumerate(batch.animals, start=2):
            if not _animal_active(row):
                continue
            trial_id = _get_or_create_trial(conn, study_id, row.trial_no)
            raw_species = _clean(row.species) or _clean(batch.species)
            canonical_species, species_status = canonicalize(conn, "species", raw_species)
            raw_numeric_notes = []
            for label, raw_value in (("age_days", row.age_days), ("age_weeks", row.age_weeks), ("body_weight_kg", row.body_weight_kg)):
                if _clean(raw_value) and _numeric(raw_value) is None:
                    raw_numeric_notes.append(f"{label}_raw={_clean(raw_value)}")
            cursor = conn.execute(
                """INSERT OR IGNORE INTO animal_groups(
                       trial_id,raw_species,canonical_species,species_mapping_status,breed,sex,animal_type,period,
                       age_days,age_weeks,body_weight_kg,notes,source_workbook,source_sheet,source_column
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING animal_group_id""",
                (
                    trial_id, raw_species, canonical_species, species_status, _clean(row.breed), _clean(row.sex),
                    _clean(row.animal_type), _clean(row.period), _numeric(row.age_days), _numeric(row.age_weeks),
                    _numeric(row.body_weight_kg), "; ".join(raw_numeric_notes), source, "AnimalGroups", row_no,
                ),
            )
            inserted = cursor.fetchone()
            if inserted:
                animal_id = int(inserted[0]); result.animal_groups += 1
            else:
                animal_id = int(conn.execute(
                    """SELECT animal_group_id FROM animal_groups
                       WHERE trial_id=? AND source_workbook=? AND source_sheet='AnimalGroups' AND source_column=?""",
                    (trial_id, source, row_no),
                ).fetchone()[0])
            key = _clean(row.animal_group_key).casefold()
            animal_ids[key] = animal_id; animal_trial_ids[key] = trial_id
            _add_provenance(conn, "animal_group", animal_id, source, "AnimalGroups", row_no, 1)

        evaluations: dict[tuple[int, int], int] = {}
        for row_no, row in enumerate(batch.nutrition_rows, start=2):
            if not _row_has_values(row.values):
                continue
            ingredient_id = sample_ids[_clean(row.ingredient_key).casefold()]
            animal_id = animal_ids[_clean(row.animal_group_key).casefold()]
            pair = (ingredient_id, animal_id)
            evaluation_id = evaluations.get(pair)
            if evaluation_id is None:
                trial_id = animal_trial_ids[_clean(row.animal_group_key).casefold()]
                cursor = conn.execute(
                    """INSERT OR IGNORE INTO ingredient_evaluations(
                           ingredient_sample_id,trial_id,animal_group_id,source_workbook,source_sheet,source_column
                       ) VALUES(?,?,?,?,?,?) RETURNING ingredient_evaluation_id""",
                    (ingredient_id, trial_id, animal_id, source, "NutritionValues", row_no),
                )
                inserted = cursor.fetchone()
                evaluation_id = int(inserted[0]) if inserted else int(conn.execute(
                    """SELECT ingredient_evaluation_id FROM ingredient_evaluations
                       WHERE ingredient_sample_id=? AND trial_id=? AND animal_group_id=?
                         AND source_workbook=? AND source_sheet='NutritionValues' AND source_column=?""",
                    (ingredient_id, trial_id, animal_id, source, row_no),
                ).fetchone()[0])
                evaluations[pair] = evaluation_id
            for metric_no, (metric, value) in enumerate(zip(batch.nutrition_metrics, row.values), start=3):
                if not _clean(value):
                    continue
                context = metric.context if metric.context.casefold() == "energy" else metric.context.upper()
                reviewed = metric.source_unit is not None
                mapped = _observation_mapping(
                    conn, metric.raw_metric, context,
                    metric.source_unit if reviewed else metric.unit,
                    metric.basis, value,
                    selected_unit=metric.unit if reviewed else None,
                    selected_method=metric.unit_resolution_method,
                    selected_rule=metric.unit_rule_reference,
                )
                parsed_metric, canonical_metric, metric_status, raw_unit, canonical_unit, unit_status, unit_method, unit_rule, canonical_basis, basis_status = mapped
                value_num, value_text = _value_parts(value)
                existing = conn.execute(
                    """SELECT nutrition_observation_id FROM nutrition_observations
                       WHERE ingredient_evaluation_id=? AND measurement_context=? AND CASEFOLD(raw_metric)=CASEFOLD(?)
                         AND value_num IS ? AND value_text IS ? AND CASEFOLD(coalesce(raw_unit,''))=CASEFOLD(?)
                         AND CASEFOLD(coalesce(raw_basis,''))=CASEFOLD(?)
                       ORDER BY nutrition_observation_id LIMIT 1""",
                    (evaluation_id, context, metric.raw_metric, value_num, value_text, raw_unit, metric.basis),
                ).fetchone()
                if existing:
                    observation_id = int(existing[0]); result.deduplicated_observations += 1
                else:
                    observation_id = int(conn.execute(
                        """INSERT INTO nutrition_observations(
                               ingredient_evaluation_id,measurement_context,raw_metric,canonical_metric,value_num,value_text,
                               raw_unit,canonical_unit,unit_resolution_method,unit_rule_reference,
                               raw_basis,canonical_basis,metric_mapping_status,unit_mapping_status,
                               basis_mapping_status,qualifier,section,subsection,validation_status,
                               source_workbook,source_sheet,source_row,source_column
                           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING nutrition_observation_id""",
                        (
                            evaluation_id, context, metric.raw_metric, canonical_metric, value_num, value_text,
                            raw_unit, canonical_unit, unit_method, unit_rule, metric.basis, canonical_basis,
                            metric_status, unit_status, basis_status,
                            "", "nutrition", "GUI batch", "accepted", source, "NutritionValues", row_no, metric_no,
                        ),
                    ).fetchone()[0])
                    result.nutrition_observations += 1
                _add_provenance(conn, "nutrition_observation", observation_id, source, "NutritionValues", row_no, metric_no)
        return result
