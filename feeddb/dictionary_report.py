from __future__ import annotations

import csv
from pathlib import Path

from .term_review import collect_candidate_term_activity


def collect_unmatched_terms(database_path: str | Path) -> list[dict[str, object]]:
    """Return one row per active candidate term."""
    rows = collect_candidate_term_activity(database_path)
    return [{
        "category": row["category"],
        "raw_term_to_review": row["raw_term"],
        "current_proposed_term": row["proposed_term"],
        "occurrences": row["occurrences"],
        "source_examples": " ; ".join(row["source_examples"]),
        "review_action": "",
        "reviewed_canonical_term": "",
        "review_note": "",
    } for row in rows]


def write_unmatched_terms(database_path: str | Path, output_path: str | Path) -> list[dict[str, object]]:
    rows = collect_unmatched_terms(database_path)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "category", "raw_term_to_review",
        "current_proposed_term", "occurrences", "source_examples", "review_action",
        "reviewed_canonical_term", "review_note",
    ]
    with destination.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return rows
