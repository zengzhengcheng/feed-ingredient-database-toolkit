# Pig net-energy multi-model reuse example

This example joins pig ingredient composition and net-energy observations from the read-only SQLite release, creates a traceable cohort, and compares five prespecified predictors without splitting records from the same study across training and validation data.

It is a database-reuse demonstration, not a feeding recommendation, universal net-energy equation, or externally validated model.

## Install optional dependencies

```powershell
python -m pip install -e ".[modeling]"
```

The ordinary database CLI and GUI do not require these packages.

## Rebuild every artifact

```powershell
python examples/net_energy_prediction/run_models.py path/to/feed_ingredient_release.sqlite
```

The database is opened with SQLite URI `mode=ro` and `PRAGMA query_only=ON`. The script stops before model generation unless it obtains exactly 48 eligible NE observations, 46 ingredient samples, seven studies, and complete CP, EE, Ash, NDF, ADF, and GE values.

The SQL contract is in `extract_pig_ne_cohort.sql`. Composition features are percentages of dry matter, GE and NE are MJ/kg dry matter, and no values or reporting bases are converted.

## Validation design

- Outer loop: `LeaveOneGroupOut` by `study_id` (seven held-out-study folds).
- Primary metric: equal-weight mean of the seven held-out-study MAEs.
- Secondary metrics: pooled out-of-fold MAE, RMSE, and R².
- Training-mean and ordinary linear regression have no tuned hyperparameters.
- Ridge, PLS, and random forest are tuned separately inside every outer training fold by a second leave-one-study-out loop.
- Full parameter grids are fixed in `run_models.py`; poor folds and negative R² values are retained.
- Standardization is inside linear/ridge/PLS pipelines and is fitted only on each training partition.
- Random seed: `20260813`; random forests use 500 trees and one execution thread.

## Published artifacts

- `cohort/pig_ne_modeling_cohort.csv`: complete rows and stable database identifiers.
- `cohort/cohort_summary.json`: counts, units, feature order, and study counts.
- `results/out_of_fold_predictions.csv`: one out-of-fold prediction per target and model.
- `results/per_study_metrics.csv`: per-study MAE, RMSE, and mean prediction bias.
- `results/model_comparison.csv`: primary and secondary metrics.
- `results/grouped_cv_metrics.json`: outer membership, inner candidates, and selected parameters.
- `results/*.png`: observed-versus-predicted and model-comparison figures.
- `models/*.joblib`: four final non-baseline pipelines refitted on the full cohort.
- `models/model_metadata.json`: feature contract, versions, grids, selected parameters, and model details.
- `model_card.md`: intended use, validation, limitations, and serialization warning.

## Apply a trusted saved model

```powershell
python examples/net_energy_prediction/predict.py new_rows.csv predictions.csv `
  --model examples/net_energy_prediction/models/partial_least_squares.joblib `
  --metadata examples/net_energy_prediction/models/model_metadata.json `
  --composition-unit "%" --ge-unit "MJ/kg" --basis "dry matter"
```

`new_rows.csv` must contain unique, finite numeric columns:

```text
CP, EE, Ash, NDF, ADF: % dry matter
GE: MJ/kg dry matter
```

The output adds `predicted_NE_MJ_per_kg_DM`. Missing or duplicate columns, nonnumeric values, and mismatched unit/basis declarations stop with an explicit error. Joblib uses pickle-compatible serialization and can execute code while loading; only load artifacts from this trusted release, or rerun `run_models.py` locally.

## Current result

For the 48-observation queue, PLS had the lowest prespecified study-macro MAE (0.667713 MJ/kg DM), compared with 2.332842 MJ/kg DM for the training-mean baseline. This is conditional on the seven studies, fixed feature set, and nested validation design; it does not establish general predictive validity.
