from __future__ import annotations

import sqlite3
import csv
from difflib import get_close_matches
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

from .normalization import comparison_key, register_sqlite_functions
from .schema import SCHEMA_SQL, SCHEMA_VERSION


class ClosingConnection(sqlite3.Connection):
    """Commit/rollback like sqlite3's context manager, then release the file handle."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


ESSENTIAL_AMINO_ACIDS = [
    "Arginine", "Histidine", "Isoleucine", "Leucine", "Lysine", "Methionine",
    "Phenylalanine", "Threonine", "Tryptophan", "Valine",
]
NONESSENTIAL_AMINO_ACIDS = [
    "Alanine", "Aspartic acid", "Cysteine", "Glutamic acid", "Glycine",
    "Proline", "Serine", "Tyrosine",
]
TRACE_ELEMENTS = [
    "Iron", "Copper", "Zinc", "Manganese", "Selenium", "Iodine", "Cobalt",
    "Chromium", "Molybdenum",
]
VITAMINS = [
    ("Vitamin A", "IU/kg"), ("Vitamin D3", "IU/kg"), ("Vitamin E", "mg/kg"),
    ("Vitamin K3", "mg/kg"), ("Thiamine (B1)", "mg/kg"), ("Riboflavin (B2)", "mg/kg"),
    ("Pyridoxine (B6)", "mg/kg"), ("Vitamin B12", "mg/kg"), ("Niacin", "mg/kg"),
    ("Pantothenic acid", "mg/kg"), ("Folic acid", "mg/kg"), ("Biotin", "mg/kg"),
    ("Choline", "mg/kg"),
]
ANTINUTRITIONAL_FACTORS = [
    ("Phytic acid", "%"), ("Trypsin inhibitor", "TIU/mg"), ("Tannins", "%"),
    ("Total glucosinolates", "µmol/g"), ("Free gossypol", "mg/kg"), ("Lectins", "HAU/mg"),
    ("Saponins", "%"), ("Oxalate", "mg/kg"), ("Cyanide", "mg/kg"),
]
MYCOTOXINS = [
    "Aflatoxin B1", "Total aflatoxins", "Deoxynivalenol (DON)", "Zearalenone (ZEN)",
    "Fumonisins B1+B2", "Ochratoxin A", "T-2 toxin", "HT-2 toxin", "Citrinin",
]
METRIC_CATALOG_SEEDS = [
    *(('composition', '常用组分', metric, '%', 'dry matter') for metric in
      ("DM", "CP", "EE", "CF", "NDF", "ADF", "Ash", "Starch", "Calcium", "Total phosphorus")),
    *(('composition', '微量元素', metric, 'mg/kg', 'dry matter') for metric in TRACE_ELEMENTS),
    *(('composition', '维生素', metric, unit, 'dry matter') for metric, unit in VITAMINS),
    *(('composition', '抗营养因子', metric, unit, 'dry matter') for metric, unit in ANTINUTRITIONAL_FACTORS),
    *(('composition', '霉菌毒素', metric, 'µg/kg', 'dry matter') for metric in MYCOTOXINS),
    *(('energy', '常用能量', metric, 'kJ/kg', 'dry matter') for metric in
      ("GE", "DE", "ME", "NE", "AME", "AMEn")),
    *(('AID', '必需氨基酸', metric, '%', 'not applicable') for metric in ESSENTIAL_AMINO_ACIDS),
    *(('AID', '非必需氨基酸', metric, '%', 'not applicable') for metric in NONESSENTIAL_AMINO_ACIDS),
    *(('SID', '必需氨基酸', metric, '%', 'not applicable') for metric in ESSENTIAL_AMINO_ACIDS),
    *(('SID', '非必需氨基酸', metric, '%', 'not applicable') for metric in NONESSENTIAL_AMINO_ACIDS),
    *(('ATTD', '常用消化率', metric, '%', 'not applicable') for metric in
      ("DM", "CP", "EE", "NDF", "ADF", "Energy", "Calcium", "Phosphorus")),
    ('STTD', '常用消化率', 'Phosphorus', '%', 'not applicable'),
]


def connect(path: str | Path) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    register_sqlite_functions(conn)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def validation_dictionary_connection(path: str | Path) -> sqlite3.Connection:
    """Open dictionary state for preflight without creating or changing a database file."""
    db_path = Path(path).expanduser().resolve()
    if db_path.is_file():
        uri = f"file:{quote(db_path.as_posix(), safe='/:')}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, factory=ClosingConnection)
        conn.row_factory = sqlite3.Row
        register_sqlite_functions(conn)
        conn.execute("PRAGMA query_only = ON")
        return conn
    conn = sqlite3.connect(":memory:", factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    register_sqlite_functions(conn)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_SQL)
    seed_dictionaries(conn)
    return conn


def initialize(path: str | Path) -> None:
    with connect(path) as conn:
        existing_metadata = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_metadata'"
            )
        }
        if existing_metadata:
            row = conn.execute(
                "SELECT metadata_value FROM schema_metadata WHERE metadata_key='schema_version'"
            ).fetchone()
            if row and row[0] != SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema {row[0]} is not compatible with schema {SCHEMA_VERSION}. "
                    "Rebuild a new working database from the source workbooks."
                )
        legacy_tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('ingredients','observations')"
            )
        }
        if legacy_tables:
            raise RuntimeError(
                "This database uses the superseded prototype schema. Choose a new database file; "
                "the prototype is intentionally not rewritten automatically."
            )
        conn.executescript(SCHEMA_SQL)
        conn.execute(
            "INSERT INTO schema_metadata(metadata_key,metadata_value) VALUES('schema_version',?) "
            "ON CONFLICT(metadata_key) DO UPDATE SET metadata_value=excluded.metadata_value",
            (SCHEMA_VERSION,),
        )


def upsert_dictionary(
    conn: sqlite3.Connection,
    category: str,
    raw_term: str,
    canonical_term: str,
    status: str = "approved",
    note: str | None = None,
    context: str = "",
) -> None:
    normalized_context = (context.strip() or "composition") if category == "metric" else ""
    conn.execute(
        """
        INSERT INTO dictionaries(category, context, raw_term, canonical_term, status, note)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(category, context, raw_term) DO UPDATE SET
            canonical_term=excluded.canonical_term,
            status=excluded.status,
            note=excluded.note,
            updated_at=CURRENT_TIMESTAMP
        """,
        (category, normalized_context, raw_term.strip(), canonical_term.strip(), status, note),
    )


def apply_dictionary_mapping(
    conn: sqlite3.Connection,
    category: str,
    raw_term: str,
    canonical_term: str,
    status: str = "approved",
    note: str | None = None,
    context: str = "",
) -> None:
    """Save a mapping and update canonical fields without changing data validity."""
    normalized_context = (context.strip() or "composition") if category == "metric" else ""
    upsert_dictionary(conn, category, raw_term, canonical_term, status, note, normalized_context)
    if category == "ingredient":
        conn.execute("UPDATE ingredient_samples SET canonical_name=?,name_mapping_status=? WHERE CASEFOLD(raw_name)=CASEFOLD(?)", (canonical_term, status, raw_term))
    elif category == "metric":
        # A component name is shared by composition and digestibility records.
        # AID/SID/ATTD/STTD remain observation metadata; they are not separate
        # metric identities that require the same spelling to be reviewed again.
        conn.execute(
            "UPDATE dictionaries SET canonical_term=?,status=?,note=?,updated_at=CURRENT_TIMESTAMP "
            "WHERE category='metric' AND CASEFOLD(raw_term)=CASEFOLD(?)",
            (canonical_term, status, note, raw_term),
        )
        conn.execute(
            "UPDATE component_observations SET canonical_metric=?,metric_mapping_status=? "
            "WHERE CASEFOLD(raw_metric)=CASEFOLD(?)",
            (canonical_term, status, raw_term),
        )
        conn.execute(
            "UPDATE nutrition_observations SET canonical_metric=?,metric_mapping_status=? "
            "WHERE CASEFOLD(raw_metric)=CASEFOLD(?)",
            (canonical_term, status, raw_term),
        )
    elif category == "unit":
        for table in ("component_observations", "nutrition_observations"):
            conn.execute(f"UPDATE {table} SET canonical_unit=?,unit_mapping_status=? WHERE CASEFOLD(raw_unit)=CASEFOLD(?)", (canonical_term, status, raw_term))
    elif category == "basis":
        for table in ("component_observations", "nutrition_observations"):
            conn.execute(f"UPDATE {table} SET canonical_basis=?,basis_mapping_status=? WHERE CASEFOLD(raw_basis)=CASEFOLD(?)", (canonical_term, status, raw_term))
    elif category == "species":
        conn.execute("UPDATE studies SET canonical_species=?,species_mapping_status=? WHERE CASEFOLD(raw_species)=CASEFOLD(?)", (canonical_term, status, raw_term))
        conn.execute("UPDATE animal_groups SET canonical_species=?,species_mapping_status=? WHERE CASEFOLD(raw_species)=CASEFOLD(?)", (canonical_term, status, raw_term))


def canonicalize(conn: sqlite3.Connection, category: str, raw_term: object, context: str = "") -> tuple[str, str]:
    raw = "" if raw_term is None else str(raw_term).strip()
    if not raw:
        return "", "missing"
    normalized_context = (context.strip() or "composition") if category == "metric" else ""
    row = conn.execute(
        "SELECT canonical_term, status FROM dictionaries WHERE category=? AND context=? AND CASEFOLD(raw_term)=CASEFOLD(?)",
        (category, normalized_context, raw),
    ).fetchone()
    if row and row["status"] != "candidate":
        return row["canonical_term"], row["status"]
    if category == "metric":
        approved = conn.execute(
            """SELECT canonical_term FROM dictionaries
               WHERE category='metric' AND status='approved' AND CASEFOLD(raw_term)=CASEFOLD(?)
               GROUP BY CASEFOLD(canonical_term)""",
            (raw,),
        ).fetchall()
        if len(approved) == 1:
            canonical = approved[0]["canonical_term"]
            upsert_dictionary(
                conn, category, raw, canonical, "approved",
                "Reused an approved component name across measurement contexts",
                normalized_context,
            )
            return canonical, "approved"
    if row:
        return row["canonical_term"], row["status"]
    if not row:
        upsert_dictionary(
            conn, category, raw, raw, "candidate",
            "Not found in the migrated legacy alias dictionary; review only this unmatched term",
            normalized_context,
        )
    return raw, "candidate"


def lookup_dictionary(
    conn: sqlite3.Connection, category: str, raw_term: object, context: str = ""
) -> tuple[str, str] | None:
    """Look up a term without creating a candidate row."""
    raw = "" if raw_term is None else str(raw_term).strip()
    if not raw:
        return None
    normalized_context = (context.strip() or "composition") if category == "metric" else ""
    row = conn.execute(
        """SELECT canonical_term,status FROM dictionaries
           WHERE category=? AND context=? AND CASEFOLD(raw_term)=CASEFOLD(?)""",
        (category, normalized_context, raw),
    ).fetchone()
    if row and row["status"] != "candidate":
        return row["canonical_term"], row["status"]
    if category == "metric":
        approved = conn.execute(
            """SELECT canonical_term FROM dictionaries
               WHERE category='metric' AND status='approved' AND CASEFOLD(raw_term)=CASEFOLD(?)
               GROUP BY CASEFOLD(canonical_term)""",
            (raw,),
        ).fetchall()
        if len(approved) == 1:
            return approved[0]["canonical_term"], "approved"
    return (row["canonical_term"], row["status"]) if row else None


def suggest_dictionary_terms(
    conn: sqlite3.Connection,
    category: str,
    raw_term: object,
    context: str = "",
    limit: int = 5,
) -> list[tuple[str, str]]:
    """Suggest visually similar approved aliases for typo prevention, without changing the dictionary."""
    raw = "" if raw_term is None else str(raw_term).strip()
    if not raw:
        return []
    normalized_context = (context.strip() or "composition") if category == "metric" else ""
    if category == "metric":
        rows = conn.execute(
            """SELECT DISTINCT raw_term,canonical_term FROM dictionaries
               WHERE category='metric' AND status='approved' ORDER BY raw_term"""
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT raw_term,canonical_term FROM dictionaries
               WHERE category=? AND context=? AND status='approved'
               ORDER BY raw_term""",
            (category, normalized_context),
        ).fetchall()
    aliases = {comparison_key(row["raw_term"]): (str(row["raw_term"]), str(row["canonical_term"])) for row in rows}
    matches = get_close_matches(comparison_key(raw), list(aliases), n=limit, cutoff=0.72)
    return [aliases[key] for key in matches]


def seed_dictionaries(conn: sqlite3.Connection) -> None:
    """Load the old code's alias dictionary plus small non-name helper lists."""
    data_directory = Path(__file__).resolve().parent / "data"
    dictionary_paths = [
        (data_directory / "legacy_name_dictionary.csv", False),
        (data_directory / "legacy_source_dictionary.csv", True),
    ]
    for dictionary_path, only_if_missing in dictionary_paths:
        if not dictionary_path.exists():
            continue
        with dictionary_path.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                if only_if_missing:
                    normalized_context = (row.get("context", "").strip() or "composition") if row["category"] == "metric" else ""
                    conn.execute(
                        """INSERT OR IGNORE INTO dictionaries(category,context,raw_term,canonical_term,status,note)
                        VALUES(?,?,?,?,?,?)""",
                        (row["category"], normalized_context, row["raw_term"].strip(), row["canonical_term"].strip(),
                         row.get("status") or "approved", row.get("note") or "Migrated from the historical Python name dictionary"),
                    )
                else:
                    upsert_dictionary(
                        conn,
                        row["category"],
                        row["raw_term"],
                        row["canonical_term"],
                        row.get("status") or "approved",
                        row.get("note") or "Migrated from the legacy SQL name dictionary",
                        row.get("context", ""),
                    )
    override_path = data_directory / "name_resolution_overrides.csv"
    if override_path.exists():
        with override_path.open(encoding="utf-8-sig", newline="") as stream:
            override_rows = list(csv.DictReader(stream))
            for row in override_rows:
                upsert_dictionary(
                    conn, row["category"], row["raw_term"], row["canonical_term"],
                    row.get("status") or "candidate", row.get("note") or "Curated resolution override",
                    row.get("context", ""),
                )
            # Component identity is global: AID/SID/ATTD/STTD are observation
            # metadata, not separate name dictionaries. A curated redirect also
            # collapses legacy aliases that pointed at the superseded canonical
            # term, so approved decisions propagate through alias chains.
            for row in override_rows:
                if row["category"] != "metric":
                    continue
                conn.execute(
                    """UPDATE dictionaries
                       SET canonical_term=?,status=?,note=?,updated_at=CURRENT_TIMESTAMP
                       WHERE category='metric'
                         AND (CASEFOLD(raw_term)=CASEFOLD(?) OR CASEFOLD(canonical_term)=CASEFOLD(?))""",
                    (
                        row["canonical_term"].strip(), row.get("status") or "candidate",
                        row.get("note") or "Curated global component-name resolution",
                        row["raw_term"].strip(), row["raw_term"].strip(),
                    ),
                )
    seeds: Iterable[tuple[str, str, str]] = [
        ("unit", "%", "%"),
        ("unit", "g/kg", "g/kg"),
        ("unit", "MJ/kg", "MJ/kg"),
        ("unit", "kJ/kg", "kJ/kg"),
        ("unit", "mg/kg", "mg/kg"),
        ("unit", "µg/kg", "µg/kg"),
        ("unit", "μg/kg", "µg/kg"),
        ("unit", "IU/kg", "IU/kg"),
        ("unit", "TIU/mg", "TIU/mg"),
        ("unit", "µmol/g", "µmol/g"),
        ("unit", "HAU/mg", "HAU/mg"),
        ("basis", "DM", "dry matter"),
        ("basis", "dry matter", "dry matter"),
        ("basis", "as-fed", "as-fed"),
        ("basis", "not applicable", "not applicable"),
        ("basis", "N/A", "not applicable"),
        ("species", "pig", "Pig"),
        ("species", "chicken", "Chicken"),
    ]
    for category, raw, canonical in seeds:
        upsert_dictionary(conn, category, raw, canonical)
    seed_metric_catalog(conn)


def seed_metric_catalog(conn: sqlite3.Connection) -> None:
    """Create the editable common-metric groups used by the batch-entry selector."""
    for order, (context, group_name, metric, unit, basis) in enumerate(METRIC_CATALOG_SEEDS, start=1):
        normalized_context = context if context == "composition" or context == "energy" else context.upper()
        conn.execute(
            """INSERT OR IGNORE INTO metric_catalog(
                   context,group_name,canonical_metric,default_unit,default_basis,sort_order,active,note
               ) VALUES(?,?,?,?,?,?,1,?)""",
            (normalized_context, group_name, metric, unit, basis, order, "Built-in editable common metric group"),
        )
        conn.execute(
            """INSERT OR IGNORE INTO dictionaries(category,context,raw_term,canonical_term,status,note)
               VALUES('metric',?,?,?,?,?)""",
            (normalized_context, metric, metric, "approved", "Seeded from the editable metric catalog"),
        )


def upsert_metric_catalog(
    conn: sqlite3.Connection,
    context: str,
    group_name: str,
    canonical_metric: str,
    default_unit: str = "%",
    default_basis: str = "",
    active: bool = True,
    note: str = "",
) -> None:
    normalized_context = context.strip() if context.strip().casefold() in {"composition", "energy"} else context.strip().upper()
    conn.execute(
        """INSERT INTO metric_catalog(
               context,group_name,canonical_metric,default_unit,default_basis,active,note
           ) VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(context,group_name,canonical_metric) DO UPDATE SET
               default_unit=excluded.default_unit,default_basis=excluded.default_basis,
               active=excluded.active,note=excluded.note,updated_at=CURRENT_TIMESTAMP""",
        (
            normalized_context, group_name.strip(), canonical_metric.strip(), default_unit.strip(),
            default_basis.strip(), int(active), note,
        ),
    )
    upsert_dictionary(
        conn, "metric", canonical_metric, canonical_metric, "approved",
        "Canonical term maintained through the metric catalog", normalized_context,
    )
    if default_unit.strip():
        upsert_dictionary(conn, "unit", default_unit, default_unit, "approved", "Unit used by the metric catalog")


def update_metric_catalog(
    conn: sqlite3.Connection,
    metric_catalog_id: int,
    context: str,
    group_name: str,
    canonical_metric: str,
    default_unit: str = "%",
    default_basis: str = "",
    active: bool = True,
    note: str = "",
) -> None:
    """Edit one catalog row while keeping the alias dictionary as a separate concern."""
    normalized_context = context.strip() if context.strip().casefold() in {"composition", "energy"} else context.strip().upper()
    conn.execute(
        """UPDATE metric_catalog SET context=?,group_name=?,canonical_metric=?,default_unit=?,
               default_basis=?,active=?,note=?,updated_at=CURRENT_TIMESTAMP
           WHERE metric_catalog_id=?""",
        (
            normalized_context, group_name.strip(), canonical_metric.strip(), default_unit.strip(),
            default_basis.strip(), int(active), note.strip(), metric_catalog_id,
        ),
    )
    upsert_dictionary(
        conn, "metric", canonical_metric, canonical_metric, "approved",
        "Canonical term maintained through the metric catalog", normalized_context,
    )
    if default_unit.strip():
        upsert_dictionary(conn, "unit", default_unit, default_unit, "approved", "Unit used by the metric catalog")
