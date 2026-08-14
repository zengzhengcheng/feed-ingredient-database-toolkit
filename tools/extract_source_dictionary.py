from __future__ import annotations

import argparse
import csv
import runpy
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceMapping:
    category: str
    context: str
    raw_term: str
    canonical_term: str
    source_table: str
    source_id: str
    name_cn: str
    name_en: str
    terminology_type: str
    status: str
    note: str


def _key(category: str, context: str, raw: str) -> tuple[str, str, str]:
    return category, context, raw.strip().casefold()


def _load_sql_dictionary(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _load_namedict(path: Path):
    namespace = runpy.run_path(str(path))
    dictionary = namespace["NameDict"]()
    dictionary.init()
    return dictionary


def _deduplicate(values: list[object]) -> list[str]:
    answer: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        normalized = text.casefold()
        if text and normalized not in seen:
            seen.add(normalized)
            answer.append(text)
    return answer


def extract(
    source_path: Path,
    sql_dictionary_path: Path,
) -> tuple[list[SourceMapping], list[dict[str, str]]]:
    sql_rows = _load_sql_dictionary(sql_dictionary_path)
    sql_by_key = {
        _key(row["category"], row.get("context", ""), row["raw_term"]): row
        for row in sql_rows
    }
    sql_by_canonical: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in sql_rows:
        sql_by_canonical.setdefault(
            (row["category"], row["canonical_term"].strip().casefold()), []
        ).append(row)

    namedict = _load_namedict(source_path)
    proposed: list[SourceMapping] = []

    def add(category: str, context: str, raw: str, canonical: str, source_id: str, status: str = "approved") -> None:
        proposed.append(SourceMapping(
            category=category,
            context=context,
            raw_term=raw,
            canonical_term=canonical,
            source_table="nameManager/articleNameDict.py",
            source_id=source_id,
            name_cn="",
            name_en=canonical,
            terminology_type="source_code_alias",
            status=status,
            note="Alias used by the historical Python ingestion code",
        ))

    ingredient_groups = namedict["name"]
    ingredient_canonicals = _deduplicate(list(ingredient_groups.get("usualname", [])))
    for canonical in ingredient_canonicals:
        aliases = ingredient_groups.get(canonical, [])
        candidates = _deduplicate([canonical, *aliases])
        canonical_rows = sql_by_canonical.get(("ingredient", canonical.casefold()), [])
        final_canonical = canonical_rows[0]["canonical_term"] if canonical_rows else canonical
        direct = sql_by_key.get(_key("ingredient", "", canonical))
        if direct:
            final_canonical = direct["canonical_term"]
        for raw in candidates:
            add("ingredient", "", raw, final_canonical, canonical)

    # The historical "others" group intentionally pooled unrelated names. Keep
    # those aliases visible for compatibility, but never approve the merge.
    for raw in _deduplicate(list(ingredient_groups.get("others", []))):
        add("ingredient", "", raw, raw, "others", "deprecated")

    component_groups = namedict["component"]
    for canonical, aliases in component_groups.items():
        if canonical in {"usualname", "unuse", "unusual"} or not isinstance(aliases, list):
            continue
        canonical_rows = sql_by_canonical.get(("metric", canonical.casefold()), [])
        contexts = sorted({row.get("context", "") for row in canonical_rows}) or ["composition"]
        for context in contexts:
            matching = [row for row in canonical_rows if row.get("context", "") == context]
            direct = sql_by_key.get(_key("metric", context, canonical))
            final_canonical = direct["canonical_term"] if direct else (matching[0]["canonical_term"] if matching else canonical)
            for raw in _deduplicate([canonical, *aliases]):
                add("metric", context, raw, final_canonical, canonical)

    for canonical, aliases in namedict["animal"].items():
        for raw in _deduplicate([canonical, *list(aliases)]):
            add("species", "", raw, canonical, canonical)

    output_by_key: dict[tuple[str, str, str], SourceMapping] = {}
    conflicts: list[dict[str, str]] = []
    for mapping in proposed:
        mapping_key = _key(mapping.category, mapping.context, mapping.raw_term)
        sql_row = sql_by_key.get(mapping_key)
        if sql_row:
            if sql_row["canonical_term"].strip().casefold() != mapping.canonical_term.casefold():
                conflicts.append({
                    "category": mapping.category,
                    "context": mapping.context,
                    "raw_term": mapping.raw_term,
                    "sql_canonical_term": sql_row["canonical_term"],
                    "source_canonical_term": mapping.canonical_term,
                    "decision": "Keep SQL mapping; record source-code disagreement for review",
                })
            continue
        existing = output_by_key.get(mapping_key)
        if existing and existing.canonical_term.casefold() != mapping.canonical_term.casefold():
            conflicts.append({
                "category": mapping.category,
                "context": mapping.context,
                "raw_term": mapping.raw_term,
                "sql_canonical_term": existing.canonical_term,
                "source_canonical_term": mapping.canonical_term,
                "decision": "Do not seed ambiguous source-code alias",
            })
            output_by_key.pop(mapping_key, None)
            continue
        if mapping_key not in output_by_key:
            output_by_key[mapping_key] = mapping

    mappings = sorted(
        output_by_key.values(),
        key=lambda row: (row.category, row.context, row.raw_term.casefold(), row.canonical_term.casefold()),
    )
    conflicts.sort(key=lambda row: (row["category"], row["context"], row["raw_term"].casefold()))
    return mappings, conflicts


def write_csv(source_path: Path, sql_dictionary_path: Path, output_path: Path, conflict_path: Path) -> tuple[int, int]:
    mappings, conflicts = extract(source_path, sql_dictionary_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(SourceMapping.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(asdict(mapping) for mapping in mappings)
    conflict_path.parent.mkdir(parents=True, exist_ok=True)
    with conflict_path.open("w", encoding="utf-8-sig", newline="") as stream:
        fields = ["category", "context", "raw_term", "sql_canonical_term", "source_canonical_term", "decision"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(conflicts)
    return len(mappings), len(conflicts)


def main() -> int:
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Extract aliases used by the historical Python ingestion code")
    parser.add_argument("source", type=Path)
    parser.add_argument(
        "--sql-dictionary",
        type=Path,
        default=project / "feeddb" / "data" / "legacy_name_dictionary.csv",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=project / "feeddb" / "data" / "legacy_source_dictionary.csv",
    )
    parser.add_argument(
        "--conflicts",
        type=Path,
        default=project / "config" / "source_dictionary_conflicts.csv",
    )
    args = parser.parse_args()
    mappings, conflicts = write_csv(args.source, args.sql_dictionary, args.output, args.conflicts)
    print(f"source_aliases={mappings} conflict_rows={conflicts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
