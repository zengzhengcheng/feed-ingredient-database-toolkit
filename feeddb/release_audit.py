from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import load_workbook

from .excel_io import INGREDIENT_SHEET_NAMES, PARAMETER_SHEET_NAMES


@dataclass
class SourceFileRecord:
    relative_path: str
    category: str
    contributor: str
    collection_id: str
    file_name: str
    extension: str
    size_bytes: int
    modified_utc: str
    file_role: str
    database_action: str
    public_release_action: str
    rights_status: str
    rights_note: str
    workbook_status: str = "not_applicable"
    sheet_names: str = ""
    recognized_data_sheet: str = ""
    doi: str = ""
    title: str = ""
    audit_issue: str = ""


def _clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def _role(path: Path) -> tuple[str, str, str, str, str]:
    name = path.name
    suffix = path.suffix.lower()
    if name.startswith("~$") and suffix == ".xlsx":
        return (
            "excel_temporary_lock",
            "exclude_from_import",
            "exclude",
            "not_applicable",
            "Temporary Excel lock artifact; listed for complete accounting.",
        )
    if suffix == ".xlsx" and "原始" in path.stem:
        return (
            "raw_historical_workbook",
            "archive_as_source_evidence_do_not_double_count",
            "pending_rights_review",
            "pending",
            "Original workbook version; retain provenance but do not import beside its curated counterpart without record-level reconciliation.",
        )
    if suffix == ".xlsx":
        return (
            "curated_historical_workbook",
            "candidate_for_database_import",
            "pending_rights_review",
            "pending",
            "Historical structured data workbook; author must confirm redistribution rights.",
        )
    if suffix == ".pdf" and "副本" in path.stem:
        return (
            "article_pdf_copy",
            "bibliographic_verification_only",
            "exclude",
            "copyrighted_source_not_for_redistribution",
            "Duplicate/copy of a source article PDF; do not publish in the data package.",
        )
    if suffix == ".pdf":
        return (
            "article_pdf",
            "bibliographic_verification_only",
            "exclude",
            "copyrighted_source_not_for_redistribution",
            "Source article PDF; cite the article but do not redistribute the PDF.",
        )
    if suffix == ".txt":
        return (
            "supporting_text",
            "manual_review",
            "manual_review",
            "pending",
            "Supporting text requires content and rights review before release.",
        )
    return (
        "other_file",
        "manual_review",
        "manual_review",
        "pending",
        "Unrecognized file type requires manual review.",
    )


def _workbook_metadata(path: Path) -> dict[str, str]:
    if path.name.startswith("~$"):
        return {"workbook_status": "temporary_lock", "audit_issue": "not_opened"}
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        return {"workbook_status": "unreadable", "audit_issue": f"{type(exc).__name__}: {exc}"}

    sheet_names = [sheet.title for sheet in workbook.worksheets]
    accepted = {name.casefold() for name in INGREDIENT_SHEET_NAMES}
    data_sheet = next((name for name in sheet_names if name.strip().casefold() in accepted), "")
    parameter_names = {name.casefold() for name in PARAMETER_SHEET_NAMES}
    parameter_sheet = next(
        (sheet for sheet in workbook.worksheets if sheet.title.strip().casefold() in parameter_names),
        None,
    )
    doi = ""
    title = ""
    if parameter_sheet is not None:
        headers = [_clean(cell.value).casefold() for cell in parameter_sheet[1]]
        values = [cell.value for cell in parameter_sheet[2]]
        metadata = dict(zip(headers, values))
        doi = _clean(metadata.get("doi"))
        title = _clean(metadata.get("title"))
    workbook.close()
    return {
        "workbook_status": "recognized_legacy" if data_sheet else "unrecognized_structure",
        "sheet_names": " | ".join(sheet_names),
        "recognized_data_sheet": data_sheet,
        "doi": doi,
        "title": title,
        "audit_issue": "" if data_sheet else "No recognized ingredient data sheet",
    }


def audit_source_tree(source_root: str | Path) -> tuple[list[SourceFileRecord], list[dict[str, object]], dict[str, object]]:
    root = Path(source_root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)
    records: list[SourceFileRecord] = []
    by_collection: dict[str, list[SourceFileRecord]] = defaultdict(list)

    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root)
        parts = relative.parts
        category = parts[0] if len(parts) >= 2 else ""
        contributor = parts[1] if len(parts) >= 3 else ""
        collection_id = parts[2] if len(parts) >= 4 else relative.parent.as_posix()
        role, db_action, release_action, rights_status, rights_note = _role(path)
        stat = path.stat()
        record = SourceFileRecord(
            relative_path=relative.as_posix(),
            category=category,
            contributor=contributor,
            collection_id=collection_id,
            file_name=path.name,
            extension=path.suffix.lower(),
            size_bytes=stat.st_size,
            modified_utc=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
            file_role=role,
            database_action=db_action,
            public_release_action=release_action,
            rights_status=rights_status,
            rights_note=rights_note,
        )
        if path.suffix.lower() == ".xlsx":
            for key, value in _workbook_metadata(path).items():
                setattr(record, key, value)
        records.append(record)
        by_collection[relative.parent.as_posix()].append(record)

    collections: list[dict[str, object]] = []
    for collection_path, items in sorted(by_collection.items()):
        curated = [item for item in items if item.file_role == "curated_historical_workbook"]
        raw = [item for item in items if item.file_role == "raw_historical_workbook"]
        pdfs = [item for item in items if item.extension == ".pdf"]
        locks = [item for item in items if item.file_role == "excel_temporary_lock"]
        issues: list[str] = []
        if not curated:
            issues.append("missing_curated_workbook")
        if len(curated) > 1:
            issues.append("multiple_curated_workbooks")
        if not pdfs:
            issues.append("missing_source_pdf")
        if len(pdfs) > 1:
            issues.append("multiple_source_pdfs")
        if any(item.workbook_status == "unreadable" for item in curated + raw):
            issues.append("unreadable_workbook")
        collections.append(
            {
                "collection_path": collection_path,
                "category": items[0].category,
                "contributor": items[0].contributor,
                "collection_id": items[0].collection_id,
                "file_count": len(items),
                "curated_workbooks": " | ".join(item.file_name for item in curated),
                "raw_workbooks": " | ".join(item.file_name for item in raw),
                "pdf_files": " | ".join(item.file_name for item in pdfs),
                "temporary_lock_files": " | ".join(item.file_name for item in locks),
                "doi": " | ".join(sorted({item.doi for item in curated if item.doi})),
                "audit_status": "review_needed" if issues else "structurally_complete",
                "audit_issues": " | ".join(issues),
            }
        )

    role_counts = Counter(record.file_role for record in records)
    extension_counts = Counter(record.extension or "[no extension]" for record in records)
    curated = [record for record in records if record.file_role == "curated_historical_workbook"]
    summary: dict[str, object] = {
        "source_root": root.as_posix(),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope_rule": "Every file is inventoried; database and public-release actions are recorded separately.",
        "total_files": len(records),
        "total_collections": len(collections),
        "file_roles": dict(sorted(role_counts.items())),
        "extensions": dict(sorted(extension_counts.items())),
        "curated_workbooks": len(curated),
        "recognized_curated_workbooks": sum(record.workbook_status == "recognized_legacy" for record in curated),
        "unrecognized_or_unreadable_curated_workbooks": sum(record.workbook_status != "recognized_legacy" for record in curated),
        "curated_workbooks_with_doi": sum(bool(record.doi) for record in curated),
        "unique_curated_dois": len({record.doi.casefold() for record in curated if record.doi}),
        "collections_requiring_review": sum(collection["audit_status"] == "review_needed" for collection in collections),
        "public_release_pending_rights_review": sum(record.public_release_action == "pending_rights_review" for record in records),
        "public_release_excluded_by_default": sum(record.public_release_action == "exclude" for record in records),
    }
    return records, collections, summary


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_source_audit(source_root: str | Path, output_directory: str | Path) -> dict[str, object]:
    output = Path(output_directory)
    records, collections, summary = audit_source_tree(source_root)
    _write_csv(output / "metadata" / "source_file_inventory.csv", [asdict(record) for record in records])
    _write_csv(output / "metadata" / "source_collection_inventory.csv", collections)
    _write_csv(
        output / "metadata" / "rights_and_exclusions.csv",
        [
            {
                "relative_path": record.relative_path,
                "file_role": record.file_role,
                "public_release_action": record.public_release_action,
                "rights_status": record.rights_status,
                "rights_note": record.rights_note,
            }
            for record in records
        ],
    )
    public_studies: list[dict[str, object]] = []
    seen_dois: set[str] = set()
    for record in records:
        if record.file_role != "curated_historical_workbook" or not record.doi:
            continue
        doi_key = record.doi.casefold()
        if doi_key in seen_dois:
            continue
        seen_dois.add(doi_key)
        public_studies.append(
            {
                "doi": record.doi,
                "title": record.title,
                "source_reference_type": "DOI",
                "copyrighted_article_file_included": "no",
            }
        )
    _write_csv(output / "public_metadata" / "study_doi_index.csv", public_studies)
    summary_path = output / "validation" / "source_audit_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary
