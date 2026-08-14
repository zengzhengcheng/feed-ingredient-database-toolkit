# Model card: pig ingredient net-energy reuse example

## Intended use

This is a transparent data-reuse demonstration. It connects composition and pig net-energy observations from the released feed-ingredient database. It is not a recommended feeding value, a universal net-energy equation, or an externally validated production model.

## Training data

- 48 NE observations from 46 ingredient samples and 7 studies.
- Target: pig NE, MJ/kg dry matter.
- Features in fixed order: CP, EE, Ash, NDF, ADF, GE.
- CP, EE, Ash, NDF, and ADF are percentages of dry matter; GE is MJ/kg dry matter.

## Evaluation

Outer validation leaves out one whole study at a time. Ridge, PLS, and random-forest parameters are selected inside each outer training set using a second study-grouped leave-one-study-out loop. The primary metric is the equal-weight mean of the seven held-out-study MAEs.

The lowest primary metric in this prespecified comparison was obtained by **Partial least squares** (0.667713 MJ/kg DM). The training-mean baseline was 2.332842 MJ/kg DM. “Lowest” refers only to this cohort and validation design.

## Saved models

Four non-baseline pipelines were refitted on all observations after out-of-fold evaluation. Full-data hyperparameters were selected with study-grouped internal validation. The pipeline files contain preprocessing where applicable. Their parameter records and readable coefficients/components are in models/model_metadata.json.

## Limitations and safety

- Seven studies are insufficient to establish broad external validity.
- Study protocols, ingredient distributions, analytical methods, and sampling are heterogeneous.
- Two ingredient samples have more than one eligible NE observation; targets remain separate and never cross a study boundary.
- Negative out-of-sample R² values and poorly performing held-out studies are retained.
- Loading a joblib file can execute code. Only load model files obtained from this trusted release, or rerun run_models.py to rebuild them locally.

## Software

Python 3.12.7; scikit-learn 1.5.1; pandas 2.2.2; NumPy 1.26.4; joblib 1.4.2; matplotlib 3.9.2. Random seed: 20260813.
