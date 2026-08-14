# Model card: all-ingredient pig metabolizable-energy example

## Intended use

This is a transparent database-reuse demonstration, not a universal ME equation, a feeding recommendation, or an externally validated production model.

## Data and conflict handling

- Primary queue: 204 ME observations, 202 samples, 22 studies, and 22 ingredient types.
- Complete-case sensitivity queue: 172 observations, 170 samples, 18 studies, and 18 ingredient types.
- Target: pig ME in MJ/kg dry matter. Chicken observations are excluded by the extraction SQL.
- Fixed predictors: CP, EE, Ash, NDF, ADF, GE. CP, EE, Ash, NDF, and ADF are % dry matter; GE is MJ/kg dry matter.
- Nine study 78 samples each had two GE candidates. The selected GE uniquely shared the ME target's workbook, sheet, and source column. No GE values were averaged. Those samples remain in the primary queue but not the complete-case queue because EE, NDF, and ADF are missing.

## Preprocessing and validation

Missing predictors are retained in the primary CSV. Within every training fold, median imputation and missingness indicators are fitted only on that fold. Linear, ridge, and PLS pipelines then standardize the transformed predictors. Random forest uses the same training-only imputer without scaling.

Outer validation leaves out one whole study. Ridge, PLS, and random-forest parameters are selected in a second leave-one-study-out loop using only the outer training studies. Every held-out study and every poor fold is retained. The primary metric is the equal-weight mean of held-out-study MAEs.

The lowest prespecified primary metric was **Random forest** (0.846456 MJ/kg DM); the training-mean baseline was 1.953313 MJ/kg DM. This ranking applies only to this cohort and validation design.

## Saved pipelines and safety

Four non-baseline pipelines were refitted on the full primary queue after grouped internal selection. They accept missing predictors under the input contract documented in README.md. Loading joblib can execute code; load only trusted local artifacts or rebuild them with run_models.py.

## Limitations

Ingredient coverage, study protocols, analytical methods, and missingness are heterogeneous. Study-group validation reduces but does not remove transportability risk. Predicted values must not replace laboratory analysis or expert ration formulation.

## Software

Python 3.12.7; scikit-learn 1.5.1; pandas 2.2.2; NumPy 1.26.4; joblib 1.4.2; matplotlib 3.9.2. Random seed: 20260813.
