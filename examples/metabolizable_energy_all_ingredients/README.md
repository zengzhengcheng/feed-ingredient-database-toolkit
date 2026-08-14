# All-ingredient pig metabolizable-energy reuse example

This independent example extracts all eligible pig ingredient ME observations on a dry-matter basis, resolves documented duplicate GE candidates by source position, compares five prespecified algorithms with nested study-group validation, and publishes a complete-case sensitivity analysis. It does not use chicken observations and does not modify the database or source workbooks.

## Rebuild

From the repository root, using the optional ML dependencies:

```powershell
python examples/metabolizable_energy_all_ingredients/run_models.py `
  path/to/ScienceDB/database/feed_ingredient_release.sqlite `
  --output-dir examples/metabolizable_energy_all_ingredients `
  --jobs 8
```

The database is opened with SQLite URI `mode=ro` and `PRAGMA query_only=ON`. The run stops if cohort counts or the nine documented conflict resolutions differ from the audited gates.

## Queues and preprocessing

The primary CSV retains missing predictor values. Within every inner and outer training fold, the pipeline fits median imputation and missingness indicators using only that training fold. Linear regression, ridge regression, and PLS then standardize the transformed predictors. Random forest uses the same fold-local imputation without scaling.

The complete-case queue is a sensitivity analysis, not a replacement for the primary queue. Study 78 remains in the primary queue after its nine GE values are uniquely matched to the ME source columns. Its samples are absent from the complete-case queue because EE, NDF, and ADF were not reported.

## Validation

The outer loop leaves one complete study out. Inside each outer training set, a second leave-one-study-out loop selects ridge, PLS, and random-forest settings by equal-weight study-macro MAE. The training-mean baseline and ordinary linear regression have no tuned hyperparameters. No record-level random split, fold deletion, or silent GE averaging is used.

## Prediction input contract

Input CSVs must contain exactly one column for each required predictor name: `CP`, `EE`, `Ash`, `NDF`, `ADF`, and `GE`. Extra identifier columns are preserved. Blank cells are treated as missing; invalid strings and infinity are rejected. Every row must contain at least three of the six predictors.

- CP, EE, Ash, NDF, ADF: percent of dry matter (`%`)
- GE: MJ/kg dry matter
- output ME: MJ/kg dry matter

Example:

```powershell
python examples/metabolizable_energy_all_ingredients/predict.py `
  examples/metabolizable_energy_all_ingredients/models/ridge_regression.joblib `
  new_ingredients.csv predictions.csv `
  --composition-unit % --ge-unit MJ/kg --target-unit MJ/kg --basis "dry matter"
```

Saved joblib files must be treated as executable artifacts: load only trusted local copies, or rebuild them from the read-only database.

## Published artifacts

- `cohort/`: primary and complete-case queues, ingredient coverage, nine resolved conflicts, zero-row unresolved-exclusion audit, and JSON summary.
- `results/`: all out-of-fold predictions, model comparisons, per-study metrics, missingness, grouped-CV audit, and figures.
- `models/`: four full-data pipelines and readable metadata.
- `model_card.md`: intended use, evaluation design, and limitations.

This example demonstrates database reuse. Its predictions are not feeding recommendations and should not replace chemical analysis or expert formulation.
