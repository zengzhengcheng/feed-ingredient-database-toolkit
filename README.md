# Feed Ingredient Database Toolkit

Tools for validating, reviewing, importing, querying, exporting, and extending feed-ingredient composition and animal-context nutritional-value datasets.

Current public software release: **v1.0.0**.

[中文说明](README_CN.md) · [User guide](docs/USER_GUIDE.md) · [Excel guide](docs/excel_entry_guide.md) · [Dictionary guide](docs/dictionary_guide.md)

## What the toolkit does

- Loads complete Excel or JSON submissions into an editable review draft before anything is written to SQLite.
- Preserves raw names, units, reported values, and source locations while storing reviewed canonical terms separately.
- Keeps unknown ingredients, metrics, and units visible as review candidates instead of deleting observations.
- Supports alias-aware fuzzy search, bounded read-only SQL rules, physical-table browsing, and CSV/JSON/XLSX export.
- Separates the name-alias dictionary from the `metric_catalog`, which controls common metric groups and editable default units.
- Provides an editable working database for curation and a strict read-only mode for a published SQLite database.
- Offers a PyQt6 desktop interface with runtime Chinese/English switching.
- Includes reproducible pig net-energy and metabolizable-energy database-reuse examples with explicit scientific limits.

## Repository boundary and ScienceDB

This repository contains software, tests, blank contribution templates, seed dictionaries, read-only SQL rules, documentation, and reproducible reuse examples. It does **not** contain source articles, formal data workbooks, the published SQLite database, ScienceDB exchange tables, user working databases, diet/formulation data, or other non-software project assets.

The formal dataset, 110 public workbooks, exchange tables, and read-only release SQLite database are distributed separately through ScienceDB.

- ScienceDB dataset: `ScienceDB URL: pending author release`
- Related article or project: **Not applicable**

## Installation

The verified Windows workflow uses a Conda environment named `pyqt312`:

```powershell
git clone https://github.com/zengzhengcheng/feed-ingredient-database-toolkit.git
cd feed-ingredient-database-toolkit
conda create -n pyqt312 python=3.12 -y
conda activate pyqt312
python -m pip install -e .
```

The core package requires Python 3.10 or later, OpenPyXL 3.1 or later, and PyQt6 6.6–6.11. Install the optional modeling dependencies only when rebuilding the NE/ME examples. The saved model artifacts require scikit-learn 1.5.1, which is pinned in the optional dependency set:

```powershell
python -m pip install -e ".[modeling]"
```

## Five-minute quick start

1. Install the toolkit in the `pyqt312` environment.
2. Double-click `run_gui.bat`, or run `conda run -n pyqt312 feeddb-gui`.
3. Open **Import & Review / 导入与核对** and select one complete Excel or JSON file.
4. Review article metadata, ingredients, composition, animal groups, nutritional values, units, basis, and warnings.
5. Run the full validation check. Loading a file has not changed SQLite yet.
6. Explicitly confirm the batch to write it to the editable working database.
7. Use **Query & Export / 查询与导出** to search and export the reviewed data.

The default working database is `database/contribution_workspace.sqlite`. It is user-owned and ignored by Git.

## Desktop GUI

Start the desktop application with either command:

```powershell
.\run_gui.bat
conda run -n pyqt312 feeddb-gui
```

The application covers five functional areas: data files, import/review, query/export, dictionary/metric catalog, and database management/table browsing. The status-bar `中文 / English` switch retranslates the interface without restarting. It changes display text only; stored metric names, context codes, units, raw fields, and numeric values do not change.

To inspect a ScienceDB SQLite file without enabling import or dictionary changes:

```powershell
.\run_gui.bat --database path\to\feed_ingredient_release.sqlite --read-only
```

## Excel and JSON contribution workflow

Use [templates/feed_ingredient_contribution_template.xlsx](templates/feed_ingredient_contribution_template.xlsx) for a new Excel contribution. Its sheets are:

- `Instructions_说明`: bilingual instructions; ignored by the parser.
- `Article Metadata`: article-level metadata.
- `Ingredient Data`: trial, ingredient, animal-group, composition, and nutritional-value rows.

Important entry rules:

- Article title is required. DOI is optional and may be blank.
- Composition and nutritional values may be blank. Never use `0` as a missing-value marker.
- Supply a complete file, not isolated cells. The interface is a review workbench, not a cell-by-cell data-entry form.
- A selected file is loaded only as a draft. The user must validate and explicitly confirm before the batch is written.
- Diet/formulation composition is outside the contribution scope.

The JSON schema and example are in [integrations/contribution.schema.json](integrations/contribution.schema.json) and [integrations/ai_contribution_example.json](integrations/ai_contribution_example.json).

## Query, fuzzy search, SQL rules, and export

The query page searches titles, DOI values, ingredients, metrics, and animal information. Approved aliases participate in fuzzy matching. Similar names are suggestions for review, not automatic merge decisions.

User-authored rules must be one read-only `SELECT` or `WITH` statement. Mutation statements are rejected and rules run through a read-only SQLite connection. Examples are in [examples/query_rules](examples/query_rules).

```powershell
feeddb query-sql path\to\working.sqlite --sql-file examples\query_rules\01_原料成分查询.sql --limit 1000
feeddb export-query path\to\working.sqlite result.xlsx --sql-file examples\query_rules\02_动物营养价值查询.sql
```

Exports support CSV, JSON, and XLSX. See [docs/database_query_and_export_guide.md](docs/database_query_and_export_guide.md).

## Dictionary and metric catalog

The name dictionary answers one question: “Is this raw spelling an alias of an existing canonical item?”

```text
raw term -> approved alias mapping -> canonical term
```

The separate `metric_catalog` controls common selectable metrics, groups, default units, and default basis. Adding an alias does not redefine a catalog group; editing a group does not silently merge names.

Unknown names remain attached to their observations as `candidate` mappings. A curator can map a candidate to an existing canonical item, create a genuinely new item, or defer the decision. The raw term is retained in every case.

For units, explicit source evidence has priority. A recognized unit after an English or Chinese comma in a metric label is extracted. New energy metrics default to editable `kJ/kg`; ordinary composition defaults to editable `%`; approved metric-specific rules and explicit units take priority. The software does not silently rescale reported values.

See [docs/dictionary_guide.md](docs/dictionary_guide.md) and [docs/dictionary_inventory.md](docs/dictionary_inventory.md).

## Direct SQLite use

The published database is a standard SQLite 3 file. Open it with DB Browser for SQLite, Python, R, Julia, or another compatible client. Published copies should be treated as read-only; create a separate working database for edits or new imports.

```python
import sqlite3

database = "path/to/feed_ingredient_release.sqlite"
with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
    rows = connection.execute(
        "SELECT canonical_name, count(*) FROM ingredients GROUP BY canonical_name"
    ).fetchall()
print(rows[:10])
```

A parameterized example is provided in [examples/python_direct_access.py](examples/python_direct_access.py).

## User-owned AI or agent JSON interface

The toolkit contains no bundled AI service and requires no API key. A user's own AI, script, agent, or local model may create a UTF-8 JSON object that follows the contribution schema. The recommended route remains human-in-the-loop: load the JSON in the GUI, review the visible draft, validate it, and explicitly save it.

```powershell
feeddb validate-json contribution.json database\contribution_workspace.sqlite
feeddb import-json contribution.json database\contribution_workspace.sqlite
```

Direct import should be used only after review. New terms are not auto-approved unless a curator explicitly requests that behavior. See [integrations/AI_AGENT_GUIDE.md](integrations/AI_AGENT_GUIDE.md).

## Reproducible NE and ME reuse examples

- [Pig net-energy example](examples/net_energy_prediction/README.md): 48 observations, 46 ingredient samples, seven studies, nested study-group validation, and five prespecified predictors.
- [All-ingredient pig metabolizable-energy example](examples/metabolizable_energy_all_ingredients/README.md): missing-value handling fitted only within training folds, a complete-case sensitivity analysis, and source-position-based conflict resolution.

These examples demonstrate traceable reuse of a read-only database. They are not universal best models, feeding recommendations, nutrient requirement equations, or independent external validations. Poor folds and negative performance values are retained rather than silently removed.

## Tests and verified environment

Run the complete automated suite:

```powershell
conda run -n pyqt312 python -m pytest -q
```

Run the dedicated offscreen GUI smoke test:

```powershell
.\run_gui.bat --smoke-test
```

The latest verification result and environment details are recorded in [RELEASE_VALIDATION.json](RELEASE_VALIDATION.json).

## Documentation map

- [Chinese/English user guide](docs/USER_GUIDE.md)
- [Excel entry guide](docs/excel_entry_guide.md)
- [Plain-language dictionary guide](docs/dictionary_guide.md)
- [Dictionary inventory](docs/dictionary_inventory.md)
- [Data model](docs/data_model.md)
- [System function map](docs/SYSTEM_FUNCTION_MAP_20260807.md)
- [AI/agent JSON guide](integrations/AI_AGENT_GUIDE.md)
- [Query and export guide](docs/database_query_and_export_guide.md)
- [Release and rebuild guide](docs/release_guide.md)
- [Read-only SQL rules](examples/query_rules)
- [Net-energy example](examples/net_energy_prediction/README.md)
- [Metabolizable-energy example](examples/metabolizable_energy_all_ingredients/README.md)

## Citation and related-project status

Related article or project: **Not applicable**.

The ScienceDB record URL and dataset citation will be added after the author completes the repository record. Until then, do not substitute a local file path for a persistent dataset citation.

## License

Released under the [MIT License](LICENSE). Copyright (c) 2026 zengzhengcheng.
