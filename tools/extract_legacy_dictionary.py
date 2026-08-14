from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


INSERT_RE = re.compile(r"^INSERT INTO `(?P<table>[^`]+)` VALUES \((?P<values>.*)\);$")
TARGET_TABLES = {"ingredient_type", "ingredient_info", "nutrient_name", "digestible_nutrient_name"}


@dataclass(frozen=True)
class Mapping:
    category: str
    context: str
    raw_term: str
    canonical_term: str
    source_table: str
    source_id: str
    name_cn: str
    name_en: str
    terminology_type: str


def parse_values(text: str) -> list[str | None]:
    """Parse one single-line MySQL VALUES tuple from the legacy dump."""
    reader = csv.reader([text], delimiter=",", quotechar="'", escapechar="\\", skipinitialspace=True)
    values: list[str | None] = []
    for value in next(reader):
        stripped = value.strip()
        values.append(None if stripped.upper() == "NULL" else stripped)
    return values


def iter_rows(sql_path: Path):
    with sql_path.open(encoding="utf-8-sig", errors="strict") as stream:
        for line_no, line in enumerate(stream, start=1):
            match = INSERT_RE.match(line.rstrip("\r\n"))
            if match and match.group("table") in TARGET_TABLES:
                yield match.group("table"), parse_values(match.group("values")), line_no


def aliases(name_cn: str | None, name_en: str | None, other_names: str | None) -> list[str]:
    terms = [name_cn or "", name_en or ""]
    if other_names:
        terms.extend(other_names.split(";"))
    seen: set[str] = set()
    answer: list[str] = []
    for term in terms:
        cleaned = term.strip()
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            answer.append(cleaned)
    return answer


def extract(sql_path: Path) -> tuple[list[Mapping], list[dict[str, str]]]:
    ingredient_types: dict[str, tuple[str, str, str]] = {}
    base_mappings: list[Mapping] = []
    ingredient_info_rows: list[tuple[list[str | None], int]] = []

    for table, values, line_no in iter_rows(sql_path):
        if table == "ingredient_type" and len(values) == 4:
            source_id, name_cn, name_en, other_names = values
            canonical = (name_en or name_cn or "").strip()
            if not source_id or not canonical:
                continue
            ingredient_types[source_id] = ((name_cn or "").strip(), (name_en or "").strip(), canonical)
            for raw in aliases(name_cn, name_en, other_names):
                base_mappings.append(Mapping("ingredient", "", raw, canonical, table, source_id, name_cn or "", name_en or "", ""))
        elif table == "nutrient_name" and len(values) == 5:
            source_id, name_cn, name_en, other_names, nutrient_type = values
            canonical = (name_en or name_cn or "").strip()
            if not source_id or not canonical:
                continue
            context = "energy" if (nutrient_type or "").strip().casefold() == "energy" else "composition"
            for raw in aliases(name_cn, name_en, other_names):
                base_mappings.append(Mapping("metric", context, raw, canonical, table, source_id, name_cn or "", name_en or "", nutrient_type or ""))
        elif table == "digestible_nutrient_name" and len(values) == 5:
            source_id, digestible_type, name_cn, name_en, other_names = values
            # Three legacy TDF rows stored the alias string in NAME_EN and left
            # OTHERS_NAME empty. Preserve the aliases but keep TDF canonical.
            if name_en and ";" in name_en and not other_names:
                other_names, name_en = name_en, None
            canonical = (name_en or name_cn or "").strip()
            context = (digestible_type or "").strip().upper()
            if not source_id or not context or not canonical:
                continue
            for raw in aliases(name_cn, name_en, other_names):
                base_mappings.append(Mapping("metric", context, raw, canonical, table, source_id, name_cn or "", name_en or "", "digestibility"))
        elif table == "ingredient_info" and len(values) == 10:
            ingredient_info_rows.append((values, line_no))

    for values, _line_no in ingredient_info_rows:
        ingredient_id, ingredient_type_id, _study_id, _variety, _id_in_file, name_in_file, *_rest = values
        type_record = ingredient_types.get(ingredient_type_id or "")
        raw = (name_in_file or "").strip()
        if not type_record or not raw:
            continue
        name_cn, name_en, canonical = type_record
        base_mappings.append(Mapping("ingredient", "", raw, canonical, "ingredient_info", ingredient_id or "", name_cn, name_en, "observed_nameInFile"))

    grouped: dict[tuple[str, str, str], dict[str, list[Mapping]]] = defaultdict(lambda: defaultdict(list))
    for mapping in base_mappings:
        grouped[(mapping.category, mapping.context, mapping.raw_term.casefold())][mapping.canonical_term.casefold()].append(mapping)

    approved: list[Mapping] = []
    conflicts: list[dict[str, str]] = []
    for (_category, _context, _raw_key), canonical_groups in grouped.items():
        if len(canonical_groups) == 1:
            records = next(iter(canonical_groups.values()))
            preferred = sorted(records, key=lambda item: (item.source_table == "ingredient_info", item.source_id))[0]
            approved.append(preferred)
            continue
        records = [item for group in canonical_groups.values() for item in group]
        for item in sorted(records, key=lambda row: (row.canonical_term.casefold(), row.source_table, row.source_id)):
            conflicts.append({
                "category": item.category,
                "context": item.context,
                "raw_term": item.raw_term,
                "canonical_term": item.canonical_term,
                "source_table": item.source_table,
                "source_id": item.source_id,
                "reason": "The same legacy alias maps to more than one canonical term in the same context",
            })
    approved.sort(key=lambda row: (row.category, row.context, row.raw_term.casefold(), row.canonical_term.casefold()))
    return approved, conflicts


def write_csv(sql_path: Path, output_path: Path, conflict_path: Path) -> tuple[int, int]:
    approved, conflicts = extract(sql_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as stream:
        fields = ["category", "context", "raw_term", "canonical_term", "source_table", "source_id", "name_cn", "name_en", "terminology_type", "note"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in approved:
            record = row.__dict__.copy()
            record["note"] = f"Legacy alias; {row.source_table} id={row.source_id}"
            writer.writerow(record)
    with conflict_path.open("w", encoding="utf-8-sig", newline="") as stream:
        fields = ["category", "context", "raw_term", "canonical_term", "source_table", "source_id", "reason"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(conflicts)
    return len(approved), len(conflicts)


def main() -> int:
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Extract the old code's alias dictionaries from the historical SQL dump")
    parser.add_argument("sql", nargs="?", type=Path, default=project.parent / "ingredient含文章发数据.sql")
    parser.add_argument("--output", type=Path, default=project / "feeddb" / "data" / "legacy_name_dictionary.csv")
    parser.add_argument("--conflicts", type=Path, default=project / "config" / "legacy_dictionary_conflicts.csv")
    args = parser.parse_args()
    approved, conflicts = write_csv(args.sql, args.output, args.conflicts)
    print(f"approved_aliases={approved} conflict_rows={conflicts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
