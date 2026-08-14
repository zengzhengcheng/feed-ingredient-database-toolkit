from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path

from .database import connect, initialize
from .contribution_api import import_payload, read_payload, validate_payload
from .dictionary_report import write_unmatched_terms
from .excel_io import Issue, import_directory, import_workbook, validate_standard_workbook
from .release_audit import write_source_audit
from .release_builder import build_release_package
from .release_check import check_release_package
from .release_stats import write_release_statistics
from .user_query import execute_readonly_query, export_readonly_query
from .database_admin import clear_database, delete_database_file, delete_study, rebuild_database
from .contribution_file import load_contribution_file


def _read_query_sql(sql: str | None, sql_file: str | None) -> str:
    if sql is not None:
        return sql
    if sql_file is not None:
        return Path(sql_file).read_text(encoding="utf-8-sig")
    raise ValueError("必须提供 --sql 或 --sql-file。")


def _read_query_parameters(value: str) -> list[object]:
    parameters = json.loads(value)
    if not isinstance(parameters, list):
        raise ValueError("--parameters 必须是 JSON 数组，例如 [\"Corn\", 10]。")
    return parameters


def _configure_utf8_stdio() -> None:
    """Keep Chinese and scientific unit symbols printable in Windows terminals."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")


def main(argv: list[str] | None = None) -> int:
    _configure_utf8_stdio()
    parser = argparse.ArgumentParser(prog="feeddb")
    sub = parser.add_subparsers(dest="command", required=True)
    p_init = sub.add_parser("init"); p_init.add_argument("database")
    p_validate = sub.add_parser("validate"); p_validate.add_argument("workbook")
    p_import = sub.add_parser("import"); p_import.add_argument("workbook"); p_import.add_argument("database"); p_import.add_argument("--mode", choices=["auto","standard","legacy"], default="auto")
    p_import_dir = sub.add_parser("import-dir"); p_import_dir.add_argument("directory"); p_import_dir.add_argument("database"); p_import_dir.add_argument("--duplicate-resolution")
    p_delete_study = sub.add_parser("delete-study", help="preview or delete one study from a working database")
    p_delete_study.add_argument("database"); p_delete_study.add_argument("--study-id", type=int, required=True)
    p_delete_study.add_argument("--confirm", action="store_true")
    p_clear = sub.add_parser("clear-database", help="preview or clear business data from a working database")
    p_clear.add_argument("database"); p_clear.add_argument("--keep-dictionaries", action="store_true")
    p_clear.add_argument("--confirm", action="store_true")
    p_delete_database = sub.add_parser("delete-database", help="delete one explicitly confirmed working database file")
    p_delete_database.add_argument("database"); p_delete_database.add_argument("--confirm-filename", default="")
    p_delete_database.add_argument("--no-backup", action="store_true")
    p_rebuild = sub.add_parser("rebuild-database", help="build a new working database from the Excel source directory")
    p_rebuild.add_argument("excel_directory"); p_rebuild.add_argument("output_database")
    p_rebuild.add_argument("--duplicate-resolution")
    p_stats = sub.add_parser("stats"); p_stats.add_argument("database")
    p_audit = sub.add_parser("audit-sources"); p_audit.add_argument("directory"); p_audit.add_argument("output_directory")
    p_release_stats = sub.add_parser("release-stats"); p_release_stats.add_argument("database"); p_release_stats.add_argument("output")
    p_dictionary_report = sub.add_parser("dictionary-report"); p_dictionary_report.add_argument("database"); p_dictionary_report.add_argument("output")
    p_validate_json = sub.add_parser("validate-json", help="validate AI/agent JSON without saving")
    p_validate_json.add_argument("input", help="UTF-8 JSON file, or - for stdin"); p_validate_json.add_argument("database")
    p_import_json = sub.add_parser("import-json", help="validate and save AI/agent JSON")
    p_import_json.add_argument("input", help="UTF-8 JSON file, or - for stdin"); p_import_json.add_argument("database")
    p_import_json.add_argument("--approve-new-terms", action="store_true", help="curator-confirmed new terms become approved")
    p_build_release = sub.add_parser("build-release", help="rebuild a sanitized public-release candidate")
    p_build_release.add_argument("historical_directory")
    p_build_release.add_argument("working_database")
    p_build_release.add_argument("output_directory")
    p_build_release.add_argument("--duplicate-resolution")
    p_build_release.add_argument("--rights-review")
    p_build_release.add_argument("--release-version", default="v1.0.0")
    p_build_release.add_argument("--data-license", default="PENDING")
    p_check_release = sub.add_parser("check-release", help="validate a generated release package")
    p_check_release.add_argument("release_directory")
    p_query_sql = sub.add_parser("query-sql", help="run one bounded read-only SELECT/WITH rule")
    p_query_sql.add_argument("database")
    query_source = p_query_sql.add_mutually_exclusive_group(required=True)
    query_source.add_argument("--sql")
    query_source.add_argument("--sql-file")
    p_query_sql.add_argument("--parameters", default="[]", help="JSON array bound to ? placeholders")
    p_query_sql.add_argument("--limit", type=int, default=1000)
    p_export_query = sub.add_parser("export-query", help="export a read-only rule as CSV, JSON, or XLSX")
    p_export_query.add_argument("database")
    p_export_query.add_argument("output")
    export_source = p_export_query.add_mutually_exclusive_group(required=True)
    export_source.add_argument("--sql")
    export_source.add_argument("--sql-file")
    p_export_query.add_argument("--parameters", default="[]", help="JSON array bound to ? placeholders")
    p_export_query.add_argument("--max-rows", type=int, default=1000000)
    args = parser.parse_args(argv)
    if args.command == "init": initialize(args.database); return 0
    if args.command == "validate":
        try:
            loaded = load_contribution_file(args.workbook)
            issues = [Issue("warning", Path(args.workbook).name, message) for message in loaded.warnings]
        except Exception:
            issues = validate_standard_workbook(args.workbook)
        print(json.dumps([i.__dict__ for i in issues], ensure_ascii=False, indent=2)); return int(any(i.level == "error" for i in issues))
    if args.command == "import":
        result = import_workbook(args.workbook, args.database, args.mode)
        print(json.dumps({**result.__dict__, "issues": [i.__dict__ for i in result.issues]}, ensure_ascii=False, indent=2)); return int(any(i.level == "error" for i in result.issues))
    if args.command == "import-dir":
        result = import_directory(args.directory, args.database, args.duplicate_resolution)
        print(json.dumps({**result.__dict__, "issues": [i.__dict__ for i in result.issues]}, ensure_ascii=False, indent=2)); return int(any(i.level == "error" for i in result.issues))
    if args.command == "delete-study":
        try:
            preview = delete_study(args.database, args.study_id, confirm=args.confirm)
            print(json.dumps({**asdict(preview), "executed": args.confirm}, ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError, KeyError, PermissionError, sqlite3.Error) as exc:
            print(json.dumps({"executed": False, "error": str(exc)}, ensure_ascii=False, indent=2)); return 2
    if args.command == "clear-database":
        try:
            preview = clear_database(
                args.database, keep_dictionaries=args.keep_dictionaries, confirm=args.confirm
            )
            print(json.dumps({**asdict(preview), "executed": args.confirm}, ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError, PermissionError, sqlite3.Error) as exc:
            print(json.dumps({"executed": False, "error": str(exc)}, ensure_ascii=False, indent=2)); return 2
    if args.command == "delete-database":
        try:
            report = delete_database_file(
                args.database, confirm_filename=args.confirm_filename, backup=not args.no_backup
            )
            print(json.dumps(report, ensure_ascii=False, indent=2)); return int(not report["deleted"])
        except (OSError, ValueError, PermissionError) as exc:
            print(json.dumps({"deleted": False, "error": str(exc)}, ensure_ascii=False, indent=2)); return 2
    if args.command == "rebuild-database":
        try:
            report = rebuild_database(
                args.excel_directory, args.output_database,
                duplicate_resolution_file=args.duplicate_resolution,
            )
            print(json.dumps(report, ensure_ascii=False, indent=2)); return 0
        except (OSError, ValueError, RuntimeError, PermissionError, sqlite3.Error) as exc:
            print(json.dumps({"built": False, "error": str(exc)}, ensure_ascii=False, indent=2)); return 2
    if args.command == "audit-sources":
        print(json.dumps(write_source_audit(args.directory, args.output_directory), ensure_ascii=False, indent=2)); return 0
    if args.command == "release-stats":
        statistics = write_release_statistics(args.database, args.output)
        print(json.dumps({
            "output": str(Path(args.output)),
            "studies": statistics["table_counts"]["studies"],
            "ingredients": statistics["table_counts"]["ingredients"],
            "observations": statistics["table_counts"]["observations"],
            "structural_checks_passed": statistics["release_readiness"]["structural_checks_passed"],
        }, ensure_ascii=True, indent=2)); return 0
    if args.command == "dictionary-report":
        rows = write_unmatched_terms(args.database, args.output)
        print(json.dumps({"output": str(Path(args.output)), "candidate_terms": len(rows)}, ensure_ascii=False, indent=2)); return 0
    if args.command == "validate-json":
        try:
            _, check = validate_payload(args.database, read_payload(args.input))
            print(json.dumps(asdict(check), ensure_ascii=False, indent=2))
            return int(bool(check.errors))
        except (ValueError, json.JSONDecodeError, OSError) as exc:
            print(json.dumps({"errors": [str(exc)]}, ensure_ascii=False, indent=2)); return 2
    if args.command == "import-json":
        try:
            result = import_payload(args.database, read_payload(args.input), args.approve_new_terms)
            print(json.dumps(result, ensure_ascii=False, indent=2)); return int(not result["saved"])
        except (ValueError, json.JSONDecodeError, OSError) as exc:
            print(json.dumps({"saved": False, "errors": [str(exc)]}, ensure_ascii=False, indent=2)); return 2
    if args.command == "build-release":
        try:
            result = build_release_package(
                args.historical_directory,
                args.working_database,
                args.output_directory,
                args.duplicate_resolution,
                args.rights_review,
                args.release_version,
                args.data_license,
            )
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"built": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 2
    if args.command == "check-release":
        try:
            report = check_release_package(args.release_directory, write_report=True)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return int(not report["ready_for_repository"])
        except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
            print(json.dumps({"ready_for_repository": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 2
    if args.command == "query-sql":
        try:
            result = execute_readonly_query(
                args.database,
                _read_query_sql(args.sql, args.sql_file),
                _read_query_parameters(args.parameters),
                max_rows=args.limit,
            )
            print(json.dumps({
                "columns": list(result.columns),
                "rows": [dict(zip(result.columns, row)) for row in result.rows],
                "row_count": len(result.rows),
                "truncated": result.truncated,
            }, ensure_ascii=False, indent=2, default=str))
            return 0
        except (ValueError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
            print(json.dumps({"query_ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 2
    if args.command == "export-query":
        try:
            report = export_readonly_query(
                args.database,
                _read_query_sql(args.sql, args.sql_file),
                args.output,
                _read_query_parameters(args.parameters),
                max_rows=args.max_rows,
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        except (ValueError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
            print(json.dumps({"exported": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 2
    with connect(args.database) as conn:
        stats = {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "studies", "trials", "ingredient_samples", "animal_groups", "ingredient_evaluations",
                "component_observations", "nutrition_observations", "source_provenance", "dictionaries",
            )
        }
    print(json.dumps(stats, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
