from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from feeddb.contribution_batch import save_contribution_batch, validate_contribution_batch
from feeddb.contribution_file import load_contribution_file
from feeddb.database import connect
from feeddb.term_review import list_candidate_terms


def _clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def _expected_observations(batch, nutrition: bool) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    if nutrition:
        metrics = batch.nutrition_metrics
        rows = batch.nutrition_rows
    else:
        metrics = batch.composition_metrics
        rows = batch.ingredients
    for row in rows:
        for index, value in enumerate(row.values):
            if index < len(metrics) and _clean(value):
                result.append((_clean(metrics[index].raw_metric), _clean(value)))
    return result


def _stored_observations(conn, table: str, id_column: str) -> list[tuple[str, str]]:
    rows = conn.execute(
        f"SELECT raw_metric,value_num,value_text FROM {table} ORDER BY {id_column}"
    ).fetchall()
    result: list[tuple[str, str]] = []
    for raw_metric, value_num, value_text in rows:
        if _clean(value_text):
            rendered = _clean(value_text)
        elif value_num is not None:
            rendered = format(float(value_num), ".15g")
        else:
            rendered = ""
        result.append((_clean(raw_metric), rendered))
    return result


def _values_equivalent(
    expected: list[tuple[str, str]], stored: list[tuple[str, str]], deduplicated: int = 0
) -> bool:
    if len(expected) != len(stored):
        return (
            len(expected) - len(stored) == deduplicated
            and set(expected) == set(stored)
        )
    for (expected_metric, expected_value), (stored_metric, stored_value) in zip(expected, stored):
        if expected_metric != stored_metric:
            return False
        try:
            if float(expected_value) != float(stored_value):
                return False
        except ValueError:
            if expected_value != stored_value:
                return False
    return True


def _run_case(source: Path, output_root: Path, index: int) -> dict[str, object]:
    case_id = f"case_{index:03d}"
    case_root = output_root / "case_databases" / case_id
    case_root.mkdir(parents=True, exist_ok=True)
    preflight_db = case_root / "preflight-must-not-exist.sqlite"
    import_db = case_root / "import.sqlite"
    report: dict[str, object] = {
        "case_id": case_id,
        "source": str(source),
        "suffix": source.suffix.casefold(),
        "load_status": "not_run",
        "validation_status": "not_run",
        "import_status": "not_run",
        "errors": [],
        "warnings": [],
        "new_terms": [],
        "invariant_failures": [],
    }
    failures: list[str] = report["invariant_failures"]  # type: ignore[assignment]
    try:
        loaded = load_contribution_file(source)
        batch = loaded.batch
        report["load_status"] = "loaded"
        report["source_kind"] = loaded.source_kind
        report["loader_warnings"] = loaded.warnings
    except Exception as exc:
        report["load_status"] = "rejected"
        report["errors"] = [f"load: {type(exc).__name__}: {exc}"]
        report["validation_status"] = "not_available"
        report["import_status"] = "not_attempted"
        return report

    check = validate_contribution_batch(preflight_db, batch)
    report["errors"] = list(check.errors)
    report["warnings"] = [*loaded.warnings, *check.warnings]
    report["mappings"] = list(check.mappings)
    report["new_terms"] = [list(item) for item in check.new_terms]
    report["validation_status"] = "blocked" if check.errors else "passed"
    if preflight_db.exists():
        failures.append("preflight_created_database_file")

    if check.errors:
        try:
            save_contribution_batch(import_db, batch, source_prefix=f"adversarial/{case_id}")
            report["import_status"] = "unexpectedly_saved"
            failures.append("validation_errors_did_not_block_import")
        except Exception:
            report["import_status"] = "blocked_as_expected"
        if import_db.exists():
            failures.append("blocked_import_created_database_file")
        return report

    try:
        saved = save_contribution_batch(import_db, batch, source_prefix=f"adversarial/{case_id}")
        report["import_status"] = "saved"
        report["save_result"] = asdict(saved)
    except Exception as exc:
        report["import_status"] = "failed_after_passing_validation"
        report["errors"] = [*check.errors, f"save: {type(exc).__name__}: {exc}"]
        failures.append("validated_batch_failed_to_save")
        return report

    expected_ingredients = Counter(
        _clean(row.raw_name) for row in batch.ingredients
        if _clean(row.raw_name) or any(_clean(value) for value in row.values)
    )
    expected_component = _expected_observations(batch, False)
    expected_nutrition = _expected_observations(batch, True)
    with connect(import_db) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
        table_counts = {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "studies", "trials", "ingredient_samples", "animal_groups",
                "component_observations", "nutrition_observations",
            )
        }
        report["table_counts"] = table_counts
        report["integrity_check"] = integrity
        report["foreign_key_violations"] = len(foreign_keys)
        if integrity != "ok":
            failures.append("sqlite_integrity_failed")
        if foreign_keys:
            failures.append("foreign_key_violation")
        if table_counts["studies"] != 1:
            failures.append("one_file_did_not_create_exactly_one_study")
        stored_ingredients = Counter(
            row[0] for row in conn.execute("SELECT raw_name FROM ingredient_samples").fetchall()
        )
        if stored_ingredients != expected_ingredients:
            failures.append("raw_ingredient_names_changed_or_were_lost")
        stored_component = _stored_observations(conn, "component_observations", "component_observation_id")
        stored_nutrition = _stored_observations(conn, "nutrition_observations", "nutrition_observation_id")
        deduplicated = int(report["save_result"]["deduplicated_observations"])
        component_gap = len(expected_component) - len(stored_component)
        component_deduplicated = max(0, min(deduplicated, component_gap))
        nutrition_deduplicated = max(0, deduplicated - component_deduplicated)
        if not _values_equivalent(expected_component, stored_component, component_deduplicated):
            failures.append("component_raw_metrics_or_values_changed")
        if not _values_equivalent(expected_nutrition, stored_nutrition, nutrition_deduplicated):
            failures.append("nutrition_raw_metrics_or_values_changed")
        provenance_counts = {
            entity: conn.execute(
                "SELECT count(*) FROM source_provenance WHERE entity_type=?", (entity,)
            ).fetchone()[0]
            for entity in ("component_observation", "nutrition_observation")
        }
        report["observation_provenance_counts"] = provenance_counts
        if provenance_counts["component_observation"] != len(expected_component):
            failures.append("component_source_locations_were_lost")
        if provenance_counts["nutrition_observation"] != len(expected_nutrition):
            failures.append("nutrition_source_locations_were_lost")
        nonaccepted = sum(
            conn.execute(
                f"SELECT count(*) FROM {table} WHERE validation_status!='accepted'"
            ).fetchone()[0]
            for table in ("component_observations", "nutrition_observations")
        )
        report["nonaccepted_observations"] = nonaccepted
        if nonaccepted:
            failures.append("valid_observations_were_held_or_rejected")

        candidate_observations = conn.execute(
            """SELECT count(*) FROM observations
               WHERE metric_mapping_status='candidate' OR unit_mapping_status='candidate'"""
        ).fetchone()[0]
        candidate_ingredients = conn.execute(
            "SELECT count(*) FROM ingredient_samples WHERE name_mapping_status='candidate'"
        ).fetchone()[0]
        report["candidate_observations"] = candidate_observations
        report["candidate_ingredients"] = candidate_ingredients

    candidates = [item for item in list_candidate_terms(import_db) if item.occurrences > 0]
    report["review_queue_terms_with_occurrences"] = len(candidates)
    report["review_queue_preview"] = [
        {
            "category": item.category,
            "context": item.context,
            "raw_term": item.raw_term,
            "occurrences": item.occurrences,
            "suggestions": list(item.suggestions),
        }
        for item in candidates[:12]
    ]
    if (report["candidate_observations"] or report["candidate_ingredients"]) and not candidates:
        failures.append("candidate_data_missing_from_review_queue")

    try:
        save_contribution_batch(import_db, batch, source_prefix=f"adversarial/{case_id}")
        report["duplicate_reimport"] = "unexpectedly_saved"
        failures.append("exact_duplicate_reimport_not_blocked")
    except ValueError as exc:
        report["duplicate_reimport"] = f"blocked: {exc}"

    return report


def _write_markdown(report: dict[str, object], path: Path) -> None:
    cases: list[dict[str, object]] = report["cases"]  # type: ignore[assignment]
    counts = report["summary"]
    lines = [
        "# 对抗性新数据导入测试报告",
        "",
        "## 汇总",
        "",
        f"- 测试文件：{counts['total_cases']}",
        f"- 成功载入：{counts['loaded']}",
        f"- 载入阶段拒绝：{counts['load_rejected']}",
        f"- 预检通过：{counts['validation_passed']}",
        f"- 预检阻断：{counts['validation_blocked']}",
        f"- 成功入库：{counts['saved']}",
        f"- 系统不变量失败案例：{counts['cases_with_invariant_failures']}",
        "",
        "## 逐案结果",
        "",
        "| 案例 | 文件 | 载入 | 预检 | 入库 | 警告 | 新词 | 不变量失败 |",
        "|---|---|---|---|---|---:|---:|---|",
    ]
    for item in cases:
        lines.append(
            "| {case_id} | {name} | {load} | {validation} | {import_status} | {warnings} | {new_terms} | {failures} |".format(
                case_id=item["case_id"], name=Path(str(item["source"])).name.replace("|", "\\|"),
                load=item["load_status"], validation=item["validation_status"],
                import_status=item["import_status"], warnings=len(item.get("warnings", [])),
                new_terms=len(item.get("new_terms", [])),
                failures="；".join(item.get("invariant_failures", [])) or "—",
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run isolated adversarial intake tests")
    parser.add_argument("input_root")
    parser.add_argument("output_root")
    args = parser.parse_args()
    input_root = Path(args.input_root).resolve()
    output_root = Path(args.output_root).resolve()
    if output_root.exists():
        raise FileExistsError(f"为避免覆盖既有测试结果，输出目录必须不存在：{output_root}")
    output_root.mkdir(parents=True)
    sources = sorted(
        path for path in input_root.rglob("*")
        if path.is_file() and path.suffix.casefold() in {".xlsx", ".xlsm", ".json"}
    )
    cases = [_run_case(source, output_root, index) for index, source in enumerate(sources, start=1)]
    summary = {
        "total_cases": len(cases),
        "loaded": sum(item["load_status"] == "loaded" for item in cases),
        "load_rejected": sum(item["load_status"] == "rejected" for item in cases),
        "validation_passed": sum(item["validation_status"] == "passed" for item in cases),
        "validation_blocked": sum(item["validation_status"] == "blocked" for item in cases),
        "saved": sum(item["import_status"] == "saved" for item in cases),
        "cases_with_invariant_failures": sum(bool(item["invariant_failures"]) for item in cases),
    }
    report = {"input_root": str(input_root), "summary": summary, "cases": cases}
    (output_root / "adversarial_results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_markdown(report, output_root / "ADVERSARIAL_TEST_REPORT.md")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return int(summary["cases_with_invariant_failures"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
