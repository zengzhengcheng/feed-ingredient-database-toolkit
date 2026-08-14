from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .database import connect, initialize
from .excel_io import import_directory


BUSINESS_TABLES = (
    "studies", "trials", "ingredient_samples", "animal_groups", "ingredient_evaluations",
    "component_observations", "nutrition_observations", "source_provenance",
)


@dataclass(frozen=True)
class DeletionPreview:
    database: str
    target_type: str
    target_id: str
    counts: dict[str, int]
    dictionaries_affected: bool = False
    metric_catalog_affected: bool = False
    source_files_affected: bool = False
    warning: str = "Excel source files are not deleted; rebuilding from retained Excel can restore these data."


def _database_file(path: str | Path, *, must_exist: bool = True) -> Path:
    raw = str(path)
    if any(mark in raw for mark in ("*", "?", "[", "]")):
        raise ValueError("数据库路径不能包含通配符。")
    target = Path(path).expanduser().resolve()
    if target.suffix.casefold() not in {".sqlite", ".db"}:
        raise ValueError("目标必须是明确的 .sqlite 或 .db 文件。")
    if must_exist and not target.is_file():
        raise FileNotFoundError(f"数据库文件不存在：{target}")
    if target.exists() and not target.is_file():
        raise ValueError("数据库目标不能是目录。")
    return target


def _ensure_working_database(path: str | Path) -> Path:
    target = _database_file(path)
    lowered = target.as_posix().casefold()
    if target.name.casefold() == "feed_ingredient_release.sqlite" or "01_sciencedb_data_candidate" in lowered:
        raise PermissionError("发布数据库只读；请先复制为工作数据库。")
    return target


def _ids(conn: sqlite3.Connection, sql: str, parameters: tuple[object, ...]) -> list[int]:
    return [int(row[0]) for row in conn.execute(sql, parameters)]


def preview_delete_study(database_path: str | Path, study_id: int) -> DeletionPreview:
    target = _ensure_working_database(database_path)
    with connect(target) as conn:
        study = conn.execute("SELECT title FROM studies WHERE study_id=?", (study_id,)).fetchone()
        if study is None:
            raise KeyError(f"研究不存在：{study_id}")
        trials = _ids(conn, "SELECT trial_id FROM trials WHERE study_id=?", (study_id,))
        samples = _ids(conn, "SELECT ingredient_sample_id FROM ingredient_samples WHERE study_id=?", (study_id,))
        trial_marks = ",".join("?" for _ in trials) or "NULL"
        sample_marks = ",".join("?" for _ in samples) or "NULL"
        groups = _ids(conn, f"SELECT animal_group_id FROM animal_groups WHERE trial_id IN ({trial_marks})", tuple(trials))
        evaluations = _ids(
            conn,
            f"SELECT ingredient_evaluation_id FROM ingredient_evaluations WHERE ingredient_sample_id IN ({sample_marks})",
            tuple(samples),
        )
        comp = _ids(conn, f"SELECT component_observation_id FROM component_observations WHERE ingredient_sample_id IN ({sample_marks})", tuple(samples))
        eval_marks = ",".join("?" for _ in evaluations) or "NULL"
        nutr = _ids(conn, f"SELECT nutrition_observation_id FROM nutrition_observations WHERE ingredient_evaluation_id IN ({eval_marks})", tuple(evaluations))
        entity_ids = {
            "study": [study_id], "trial": trials, "ingredient_sample": samples,
            "animal_group": groups, "ingredient_evaluation": evaluations,
            "component_observation": comp, "nutrition_observation": nutr,
        }
        provenance = 0
        for entity_type, values in entity_ids.items():
            if values:
                marks = ",".join("?" for _ in values)
                provenance += conn.execute(
                    f"SELECT count(*) FROM source_provenance WHERE entity_type=? AND entity_id IN ({marks})",
                    (entity_type, *values),
                ).fetchone()[0]
    counts = {
        "studies": 1, "trials": len(trials), "ingredient_samples": len(samples),
        "animal_groups": len(groups), "ingredient_evaluations": len(evaluations),
        "component_observations": len(comp), "nutrition_observations": len(nutr),
        "source_provenance": provenance,
    }
    return DeletionPreview(str(target), "study", str(study_id), counts)


def delete_study(database_path: str | Path, study_id: int, *, confirm: bool = False) -> DeletionPreview:
    preview = preview_delete_study(database_path, study_id)
    if not confirm:
        return preview
    target = Path(preview.database)
    with connect(target) as conn:
        source_rows = conn.execute(
            "SELECT source_workbook FROM studies WHERE study_id=?", (study_id,)
        ).fetchall()
        sources = [row[0] for row in source_rows]
        for source in sources:
            conn.execute("DELETE FROM source_provenance WHERE source_workbook=?", (source,))
        conn.execute("DELETE FROM studies WHERE study_id=?", (study_id,))
        conn.execute(
            "INSERT INTO operation_log(operation_type,target_type,target_id,counts_json,message) VALUES(?,?,?,?,?)",
            ("delete", "study", str(study_id), json.dumps(preview.counts, ensure_ascii=False), preview.warning),
        )
    return preview


def preview_clear_database(database_path: str | Path, *, keep_dictionaries: bool = True) -> DeletionPreview:
    target = _ensure_working_database(database_path)
    with connect(target) as conn:
        counts = {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in BUSINESS_TABLES}
    return DeletionPreview(
        str(target), "database_data", target.name, counts,
        dictionaries_affected=not keep_dictionaries,
        metric_catalog_affected=not keep_dictionaries,
    )


def clear_database(
    database_path: str | Path, *, keep_dictionaries: bool = True, confirm: bool = False
) -> DeletionPreview:
    preview = preview_clear_database(database_path, keep_dictionaries=keep_dictionaries)
    if not confirm:
        return preview
    with connect(preview.database) as conn:
        conn.execute("DELETE FROM source_provenance")
        conn.execute("DELETE FROM studies")
        conn.execute("DELETE FROM import_runs")
        if not keep_dictionaries:
            conn.execute("DELETE FROM dictionaries")
            conn.execute("DELETE FROM metric_catalog")
        conn.execute(
            "INSERT INTO operation_log(operation_type,target_type,target_id,counts_json,message) VALUES(?,?,?,?,?)",
            ("clear", "database_data", Path(preview.database).name, json.dumps(preview.counts), preview.warning),
        )
    return preview


def delete_database_file(
    database_path: str | Path,
    *,
    confirm_filename: str = "",
    backup: bool = True,
) -> dict[str, object]:
    target = _ensure_working_database(database_path)
    if confirm_filename != target.name:
        return {"deleted": False, "database": str(target), "required_confirmation": target.name}
    backup_path: Path | None = None
    if backup:
        backup_path = target.with_name(f"{target.stem}.before-delete{target.suffix}")
        if backup_path.exists():
            raise FileExistsError(f"删除前备份已存在：{backup_path}")
        shutil.copy2(target, backup_path)
    target.unlink()
    return {"deleted": True, "database": str(target), "backup": str(backup_path) if backup_path else ""}


def rebuild_database(
    excel_directory: str | Path,
    output_database: str | Path,
    *,
    duplicate_resolution_file: str | Path | None = None,
) -> dict[str, object]:
    output = _database_file(output_database, must_exist=False)
    if output.name.casefold() == "feed_ingredient_release.sqlite":
        raise PermissionError("请使用新的工作库文件名；发布快照由build-release生成。")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{output.stem}-building-", suffix=output.suffix, dir=output.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    temporary.unlink()
    try:
        result = import_directory(excel_directory, temporary, duplicate_resolution_file)
        conn = connect(temporary)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = len(conn.execute("PRAGMA foreign_key_check").fetchall())
        finally:
            conn.close()
        if integrity != "ok" or foreign_keys:
            raise RuntimeError(f"重建数据库校验失败：integrity={integrity}, foreign_keys={foreign_keys}")
        if output.exists():
            raise FileExistsError(f"输出数据库已存在；请选择新路径：{output}")
        os.replace(temporary, output)
        return {
            "database": str(output), "source_directory": str(Path(excel_directory).resolve()),
            "studies": result.studies, "observations": result.observations,
            "held": result.held, "unmapped": result.unmapped,
            "integrity": integrity, "foreign_key_errors": foreign_keys,
            "source_files_deleted": False,
        }
    finally:
        if temporary.exists():
            temporary.unlink()
