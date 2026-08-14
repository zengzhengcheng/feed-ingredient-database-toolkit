from __future__ import annotations

import unicodedata


def comparison_key(value: object) -> str:
    """Return the one lowercase-only key used for name and unit comparisons.

    Raw text is never rewritten. NFKC makes full-width/compatibility forms
    comparable and ``casefold`` is the Unicode-safe equivalent of lowercasing.
    """
    if value is None:
        return ""
    return unicodedata.normalize("NFKC", str(value).strip()).casefold()


def register_sqlite_functions(connection) -> None:
    """Expose the same comparison rule to every project SQLite connection."""
    connection.create_function("CASEFOLD", 1, comparison_key, deterministic=True)
