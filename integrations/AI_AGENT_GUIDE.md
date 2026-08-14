# AI agent contribution guide

Your task is to convert user-supplied feed-ingredient data into one JSON object that conforms to `contribution.schema.json`.

## Non-negotiable boundaries

- Process only data the user is authorized to provide. Do not package or copy article PDFs.
- Do not extract, infer, or transmit experimental diet/formulation composition. This release accepts ingredient composition, animal metadata, nutritional values, and article metadata only.
- `metadata.title` is required. DOI is optional and must be `null` or omitted when unavailable; never invent it.
- Preserve the source wording in `raw_name` and `metric`. Never invent missing values. Use `null` for an empty cell.
- `values` are positional matrices: each ingredient value aligns with `composition_metrics` at the same array index; each nutrition value aligns with `nutrition_metrics` at the same index.
- Composition belongs to the ingredient. Energy and digestibility values require both an ingredient and an animal group.
- Preserve an explicit source unit. If an energy unit is genuinely absent, leave it blank rather than inventing one: the software applies approved metric rules and then a recorded value-magnitude rule (MJ/kg, kcal/kg, or kJ/kg); an ambiguous magnitude receives an editable `kJ/kg` default plus a review warning. A unit after an English or Chinese comma in a metric label is extracted automatically. Ordinary composition defaults to `%`, but source evidence overrides all suggestions.
- Use separate contexts for `energy`, `AID`, `SID`, `ATTD`, `STTD`, and `CATTD`.
- Do not approve new aliases automatically. Validation may report unknown terms; a curator decides whether they are correct new names or aliases.

## Safe workflow

1. Produce UTF-8 JSON only, using `ai_contribution_example.json` as the structural example.
2. Recommended review route: open the PyQt6 application, choose `导入与核对 → 选择 Excel / JSON`, and select the complete JSON file. Loading only fills the visible grids; it does not write SQLite.
3. Review every metadata field, metric/unit/basis, ingredient row, animal group, and nutrition matrix cell. Correct all errors and keep warnings visible.
4. Click `检查全部数据`, then let the curator click `确认写入工作数据库`.

For automated pipelines, validate without writing:

   `feeddb validate-json contribution.json database/contribution_workspace.sqlite`

Correct all errors. Warnings and unknown names must remain visible to the curator. Direct import is optional and should run only after user/curator confirmation:

   `feeddb import-json contribution.json database/contribution_workspace.sqlite`

The Python integration point is `feeddb.contribution_api.validate_payload()` followed by `import_payload()`. Both reuse the same validators and normalized SQLite writer as the PyQt6 batch-entry interface.
