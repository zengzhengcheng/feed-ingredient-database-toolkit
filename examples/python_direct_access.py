"""Directly query the released SQLite file and export selected rows as UTF-8 CSV."""

from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", help="feed_ingredient_release.sqlite")
    parser.add_argument("output", help="output CSV path")
    parser.add_argument("--ingredient", default="Corn")
    args = parser.parse_args()

    sql = """
    SELECT s.title, s.doi, i.canonical_name AS ingredient,
           o.canonical_metric AS metric, o.value_num, o.value_text,
           o.canonical_unit AS unit, o.canonical_basis AS basis
    FROM component_observations o
    JOIN ingredient_samples i USING (ingredient_sample_id)
    JOIN studies s USING (study_id)
    WHERE i.canonical_name LIKE ?
    ORDER BY s.title, i.canonical_name, o.canonical_metric
    """
    database = Path(args.database).resolve()
    destination = Path(args.output)
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as connection:
        cursor = connection.execute(sql, (f"%{args.ingredient}%",))
        columns = [item[0] for item in cursor.description]
        rows = cursor.fetchall()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(columns)
        writer.writerows(rows)
    print(f"Exported {len(rows)} rows to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
