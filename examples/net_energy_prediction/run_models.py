"""Build the pig net-energy reuse example with nested study-group validation."""

from __future__ import annotations

import argparse
import itertools
import json
import platform
import sqlite3
from pathlib import Path
from typing import Any

import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import sklearn
from sklearn.cross_decomposition import PLSRegression
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

RANDOM_SEED = 20260813
FEATURES = ["CP", "EE", "Ash", "NDF", "ADF", "GE"]
MODEL_ORDER = [
    "training_mean", "ordinary_linear_regression", "ridge_regression",
    "partial_least_squares", "random_forest",
]
DISPLAY_NAMES = {
    "training_mean": "Training mean",
    "ordinary_linear_regression": "Ordinary linear regression",
    "ridge_regression": "Ridge regression",
    "partial_least_squares": "Partial least squares",
    "random_forest": "Random forest",
}
RIDGE_ALPHAS = [0.01, 0.1, 1.0, 10.0, 100.0, 1000.0]
PLS_COMPONENTS = [1, 2, 3, 4, 5, 6]
RF_GRID = [
    {"max_depth": depth, "min_samples_leaf": leaf, "max_features": features}
    for depth, leaf, features in itertools.product(
        [2, 3, None], [1, 2, 4], ["sqrt", 1.0]
    )
]


def _json_value(value: Any) -> Any:
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=_json_value) + "\n",
        encoding="utf-8",
    )


def read_cohort(database: Path, sql_path: Path) -> pd.DataFrame:
    """Execute the published extraction SQL through a physically read-only URI."""
    database = database.resolve()
    if not database.is_file():
        raise FileNotFoundError(f"SQLite database not found: {database}")
    uri = f"file:{database.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.execute("PRAGMA query_only = ON")
        return pd.read_sql_query(sql_path.read_text(encoding="utf-8"), connection)


def validate_cohort(
    cohort: pd.DataFrame,
    expected_observations: int = 48,
    expected_samples: int = 46,
    expected_studies: int = 7,
) -> dict[str, Any]:
    required = [
        "study_id", "study_title", "ingredient_sample_id", "ingredient_name",
        "nutrition_observation_id", *FEATURES, "NE", "composition_unit",
        "GE_unit", "target_unit", "basis",
    ]
    missing = [column for column in required if column not in cohort.columns]
    if missing:
        raise ValueError(f"Cohort is missing columns: {missing}")
    counts = {
        "observations": int(len(cohort)),
        "ingredient_samples": int(cohort["ingredient_sample_id"].nunique()),
        "studies": int(cohort["study_id"].nunique()),
        "duplicate_target_ids": int(cohort["nutrition_observation_id"].duplicated().sum()),
    }
    expected = {
        "observations": expected_observations,
        "ingredient_samples": expected_samples,
        "studies": expected_studies,
        "duplicate_target_ids": 0,
    }
    if counts != expected:
        raise RuntimeError(
            "Cohort stop gate failed; no model results were generated. "
            f"Expected {expected}, obtained {counts}."
        )
    numeric = cohort[[*FEATURES, "NE"]].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any() or not np.isfinite(numeric.to_numpy()).all():
        raise ValueError("The modeling cohort contains missing or non-finite values")
    if set(cohort["composition_unit"]) != {"%"}:
        raise ValueError("Composition features must all be expressed as %")
    if set(cohort["GE_unit"]) != {"MJ/kg"} or set(cohort["target_unit"]) != {"MJ/kg"}:
        raise ValueError("GE and NE must all be expressed as MJ/kg")
    if set(cohort["basis"]) != {"dry matter"}:
        raise ValueError("All cohort rows must use the dry matter basis")
    return counts


def parameter_candidates(model_name: str) -> list[dict[str, Any]]:
    if model_name == "ridge_regression":
        return [{"alpha": alpha} for alpha in RIDGE_ALPHAS]
    if model_name == "partial_least_squares":
        return [{"n_components": component} for component in PLS_COMPONENTS]
    if model_name == "random_forest":
        return [dict(item) for item in RF_GRID]
    return [{}]


def build_estimator(model_name: str, params: dict[str, Any] | None = None) -> Pipeline:
    params = params or {}
    if model_name == "ordinary_linear_regression":
        return Pipeline([("scaler", StandardScaler()), ("model", LinearRegression())])
    if model_name == "ridge_regression":
        return Pipeline([
            ("scaler", StandardScaler()),
            ("model", Ridge(alpha=float(params["alpha"]))),
        ])
    if model_name == "partial_least_squares":
        return Pipeline([
            ("scaler", StandardScaler()),
            ("model", PLSRegression(
                n_components=int(params["n_components"]), scale=False, max_iter=1000
            )),
        ])
    if model_name == "random_forest":
        return Pipeline([("model", RandomForestRegressor(
            n_estimators=500,
            max_depth=params["max_depth"],
            min_samples_leaf=int(params["min_samples_leaf"]),
            max_features=params["max_features"],
            random_state=RANDOM_SEED,
            n_jobs=1,
        ))])
    raise ValueError(f"Unknown model: {model_name}")


def _predict(estimator: Pipeline, x: np.ndarray) -> np.ndarray:
    return np.asarray(estimator.predict(x), dtype=float).reshape(-1)


def select_parameters(
    model_name: str, x: np.ndarray, y: np.ndarray, groups: np.ndarray
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Select parameters by study-macro inner MAE using training studies only."""
    if len(np.unique(groups)) < 2:
        raise ValueError("Nested group tuning requires at least two training studies")
    logo = LeaveOneGroupOut()
    candidate_results: list[dict[str, Any]] = []
    for params in parameter_candidates(model_name):
        fold_maes: list[float] = []
        fold_groups: list[int] = []
        for inner_train, inner_valid in logo.split(x, y, groups):
            train_groups = set(groups[inner_train].tolist())
            valid_groups = set(groups[inner_valid].tolist())
            if train_groups & valid_groups:
                raise AssertionError("Inner study leakage detected")
            estimator = build_estimator(model_name, params)
            estimator.fit(x[inner_train], y[inner_train])
            predictions = _predict(estimator, x[inner_valid])
            fold_maes.append(float(mean_absolute_error(y[inner_valid], predictions)))
            fold_groups.append(int(groups[inner_valid][0]))
        candidate_results.append({
            "params": params,
            "study_macro_mae": float(np.mean(fold_maes)),
            "inner_fold_mae": [
                {"held_out_study_id": group, "mae": mae}
                for group, mae in zip(fold_groups, fold_maes)
            ],
        })
    candidate_results.sort(key=lambda item: (
        item["study_macro_mae"], json.dumps(item["params"], sort_keys=True)
    ))
    return dict(candidate_results[0]["params"]), candidate_results


def nested_oof_predictions(cohort: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    x = cohort[FEATURES].to_numpy(dtype=float)
    y = cohort["NE"].to_numpy(dtype=float)
    groups = cohort["study_id"].to_numpy()
    output_rows: list[dict[str, Any]] = []
    audit: dict[str, Any] = {"outer_strategy": "LeaveOneGroupOut", "outer_folds": []}
    for fold_number, (train_index, test_index) in enumerate(
        LeaveOneGroupOut().split(x, y, groups), start=1
    ):
        train_studies = sorted({int(v) for v in groups[train_index]})
        held_studies = sorted({int(v) for v in groups[test_index]})
        if len(held_studies) != 1 or set(train_studies) & set(held_studies):
            raise AssertionError("Outer study leakage detected")
        held_study = held_studies[0]
        fold_audit: dict[str, Any] = {
            "outer_fold": fold_number,
            "held_out_study_id": held_study,
            "training_study_ids": train_studies,
            "n_train": int(len(train_index)),
            "n_test": int(len(test_index)),
            "models": {
                "training_mean": {"selected_params": {}},
                "ordinary_linear_regression": {"selected_params": {}},
            },
        }
        fitted: dict[str, tuple[np.ndarray, dict[str, Any]]] = {
            "training_mean": (
                np.repeat(float(np.mean(y[train_index])), len(test_index)), {}
            )
        }
        ols = build_estimator("ordinary_linear_regression")
        ols.fit(x[train_index], y[train_index])
        fitted["ordinary_linear_regression"] = (_predict(ols, x[test_index]), {})
        for model_name in ["ridge_regression", "partial_least_squares", "random_forest"]:
            selected, candidates = select_parameters(
                model_name, x[train_index], y[train_index], groups[train_index]
            )
            estimator = build_estimator(model_name, selected)
            estimator.fit(x[train_index], y[train_index])
            fitted[model_name] = (_predict(estimator, x[test_index]), selected)
            fold_audit["models"][model_name] = {
                "selected_params": selected,
                "inner_candidates": candidates,
                "inner_study_ids": train_studies,
            }
        audit["outer_folds"].append(fold_audit)
        for model_name in MODEL_ORDER:
            predictions, selected = fitted[model_name]
            for row_position, prediction in zip(test_index, predictions):
                source = cohort.iloc[int(row_position)]
                actual = float(source["NE"])
                output_rows.append({
                    "model": model_name,
                    "model_display_name": DISPLAY_NAMES[model_name],
                    "outer_fold": fold_number,
                    "held_out_study_id": held_study,
                    "study_id": int(source["study_id"]),
                    "ingredient_sample_id": int(source["ingredient_sample_id"]),
                    "nutrition_observation_id": int(source["nutrition_observation_id"]),
                    "observed_NE_MJ_per_kg_DM": actual,
                    "predicted_NE_MJ_per_kg_DM": float(prediction),
                    "residual_observed_minus_predicted": actual - float(prediction),
                    "selected_params": json.dumps(selected, sort_keys=True),
                })
    predictions = pd.DataFrame(output_rows)
    if len(predictions) != len(cohort) * len(MODEL_ORDER):
        raise AssertionError("The OOF prediction table is incomplete")
    key = ["model", "nutrition_observation_id"]
    if predictions.duplicated(key).any():
        raise AssertionError("A target observation has duplicate OOF predictions")
    return predictions.sort_values(key).reset_index(drop=True), audit


def calculate_metrics(predictions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    per_study_rows: list[dict[str, Any]] = []
    for (model, study_id), frame in predictions.groupby(["model", "study_id"], sort=True):
        observed = frame["observed_NE_MJ_per_kg_DM"].to_numpy()
        predicted = frame["predicted_NE_MJ_per_kg_DM"].to_numpy()
        per_study_rows.append({
            "model": model,
            "model_display_name": DISPLAY_NAMES[model],
            "study_id": int(study_id),
            "n_observations": int(len(frame)),
            "mae": float(mean_absolute_error(observed, predicted)),
            "rmse": float(mean_squared_error(observed, predicted) ** 0.5),
            "mean_prediction_bias_predicted_minus_observed": float(np.mean(predicted - observed)),
        })
    per_study = pd.DataFrame(per_study_rows)
    baseline_macro = float(
        per_study.loc[per_study["model"] == "training_mean", "mae"].mean()
    )
    comparison_rows: list[dict[str, Any]] = []
    for model in MODEL_ORDER:
        frame = predictions[predictions["model"] == model]
        observed = frame["observed_NE_MJ_per_kg_DM"].to_numpy()
        predicted = frame["predicted_NE_MJ_per_kg_DM"].to_numpy()
        macro = float(per_study.loc[per_study["model"] == model, "mae"].mean())
        difference = macro - baseline_macro
        comparison_rows.append({
            "model": model,
            "model_display_name": DISPLAY_NAMES[model],
            "study_macro_mae": macro,
            "pooled_mae": float(mean_absolute_error(observed, predicted)),
            "pooled_rmse": float(mean_squared_error(observed, predicted) ** 0.5),
            "out_of_sample_r2": float(r2_score(observed, predicted)),
            "study_macro_mae_difference_vs_baseline": difference,
            "study_macro_mae_percent_change_vs_baseline": (
                100.0 * difference / baseline_macro if baseline_macro else np.nan
            ),
        })
    comparison = pd.DataFrame(comparison_rows).sort_values(
        ["study_macro_mae", "model"]
    ).reset_index(drop=True)
    comparison.insert(0, "study_macro_rank", np.arange(1, len(comparison) + 1, dtype=np.int64))
    return comparison, per_study.sort_values(["model", "study_id"]).reset_index(drop=True)


def _linear_details(pipeline: Pipeline) -> dict[str, Any]:
    scaler: StandardScaler = pipeline.named_steps["scaler"]
    model = pipeline.named_steps["model"]
    coef = np.asarray(model.coef_, dtype=float).reshape(-1)
    intercept = float(np.asarray(model.intercept_).reshape(-1)[0])
    original_coef = coef / scaler.scale_
    original_intercept = intercept - float(np.dot(coef, scaler.mean_ / scaler.scale_))
    return {
        "standardized_feature_coefficients": dict(zip(FEATURES, coef.tolist())),
        "standardized_intercept": intercept,
        "original_unit_coefficients": dict(zip(FEATURES, original_coef.tolist())),
        "original_unit_intercept": original_intercept,
        "scaler_mean": dict(zip(FEATURES, scaler.mean_.tolist())),
        "scaler_scale": dict(zip(FEATURES, scaler.scale_.tolist())),
    }


def fit_final_models(cohort: pd.DataFrame, models_dir: Path) -> dict[str, Any]:
    models_dir.mkdir(parents=True, exist_ok=True)
    x = cohort[FEATURES].to_numpy(dtype=float)
    y = cohort["NE"].to_numpy(dtype=float)
    groups = cohort["study_id"].to_numpy()
    metadata: dict[str, Any] = {}
    for model_name in MODEL_ORDER[1:]:
        if model_name in {"ridge_regression", "partial_least_squares", "random_forest"}:
            selected, candidates = select_parameters(model_name, x, y, groups)
        else:
            selected, candidates = {}, []
        estimator = build_estimator(model_name, selected)
        estimator.fit(x, y)
        joblib.dump(estimator, models_dir / f"{model_name}.joblib")
        details: dict[str, Any]
        if model_name in {"ordinary_linear_regression", "ridge_regression"}:
            details = _linear_details(estimator)
        elif model_name == "partial_least_squares":
            scaler: StandardScaler = estimator.named_steps["scaler"]
            model: PLSRegression = estimator.named_steps["model"]
            details = {
                "x_weights": np.asarray(model.x_weights_).tolist(),
                "x_loadings": np.asarray(model.x_loadings_).tolist(),
                "y_loadings": np.asarray(model.y_loadings_).tolist(),
                "standardized_x_coefficients": np.asarray(model.coef_).reshape(-1).tolist(),
                "scaler_mean": dict(zip(FEATURES, scaler.mean_.tolist())),
                "scaler_scale": dict(zip(FEATURES, scaler.scale_.tolist())),
            }
        else:
            model: RandomForestRegressor = estimator.named_steps["model"]
            details = {
                "feature_importances": dict(zip(FEATURES, model.feature_importances_.tolist()))
            }
        metadata[model_name] = {
            "selected_hyperparameters": selected,
            "full_data_inner_candidates": candidates,
            "model_details": details,
        }
    return metadata


def make_plots(
    predictions: pd.DataFrame, comparison: pd.DataFrame, results_dir: Path
) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    figure, axes = plt.subplots(2, 3, figsize=(11, 7), sharex=True, sharey=True)
    limits = [
        float(min(predictions["observed_NE_MJ_per_kg_DM"].min(), predictions["predicted_NE_MJ_per_kg_DM"].min())),
        float(max(predictions["observed_NE_MJ_per_kg_DM"].max(), predictions["predicted_NE_MJ_per_kg_DM"].max())),
    ]
    for axis, model in zip(axes.flat, MODEL_ORDER):
        frame = predictions[predictions["model"] == model]
        axis.scatter(
            frame["observed_NE_MJ_per_kg_DM"],
            frame["predicted_NE_MJ_per_kg_DM"],
            s=24, alpha=0.75,
        )
        axis.plot(limits, limits, linestyle="--", color="black", linewidth=1)
        axis.set_title(DISPLAY_NAMES[model], fontsize=9)
        axis.set_xlim(limits)
        axis.set_ylim(limits)
        axis.grid(alpha=0.2)
    axes.flat[-1].axis("off")
    figure.supxlabel("Observed NE (MJ/kg DM)")
    figure.supylabel("Out-of-fold predicted NE (MJ/kg DM)")
    figure.tight_layout()
    figure.savefig(results_dir / "observed_vs_predicted.png", dpi=300, bbox_inches="tight")
    plt.close(figure)

    ordered = comparison.sort_values("study_macro_mae", ascending=True)
    figure, axis = plt.subplots(figsize=(8, 4.6))
    positions = np.arange(len(ordered))
    width = 0.36
    axis.barh(positions - width / 2, ordered["study_macro_mae"], height=width, label="Study-macro MAE")
    axis.barh(positions + width / 2, ordered["pooled_mae"], height=width, label="Pooled MAE")
    axis.set_yticks(positions, ordered["model_display_name"])
    axis.set_xlabel("MAE (MJ/kg DM; lower is better)")
    axis.grid(axis="x", alpha=0.2)
    axis.legend()
    figure.tight_layout()
    figure.savefig(results_dir / "model_comparison.png", dpi=300, bbox_inches="tight")
    plt.close(figure)


def write_model_card(path: Path, comparison: pd.DataFrame, counts: dict[str, Any]) -> None:
    best = comparison.iloc[0]
    baseline = comparison[comparison["model"] == "training_mean"].iloc[0]
    text = f"""# Model card: pig ingredient net-energy reuse example

## Intended use

This is a transparent data-reuse demonstration. It connects composition and pig net-energy observations from the released feed-ingredient database. It is not a recommended feeding value, a universal net-energy equation, or an externally validated production model.

## Training data

- {counts['observations']} NE observations from {counts['ingredient_samples']} ingredient samples and {counts['studies']} studies.
- Target: pig NE, MJ/kg dry matter.
- Features in fixed order: {', '.join(FEATURES)}.
- CP, EE, Ash, NDF, and ADF are percentages of dry matter; GE is MJ/kg dry matter.

## Evaluation

Outer validation leaves out one whole study at a time. Ridge, PLS, and random-forest parameters are selected inside each outer training set using a second study-grouped leave-one-study-out loop. The primary metric is the equal-weight mean of the seven held-out-study MAEs.

The lowest primary metric in this prespecified comparison was obtained by **{best['model_display_name']}** ({best['study_macro_mae']:.6f} MJ/kg DM). The training-mean baseline was {baseline['study_macro_mae']:.6f} MJ/kg DM. “Lowest” refers only to this cohort and validation design.

## Saved models

Four non-baseline pipelines were refitted on all observations after out-of-fold evaluation. Full-data hyperparameters were selected with study-grouped internal validation. The pipeline files contain preprocessing where applicable. Their parameter records and readable coefficients/components are in models/model_metadata.json.

## Limitations and safety

- Seven studies are insufficient to establish broad external validity.
- Study protocols, ingredient distributions, analytical methods, and sampling are heterogeneous.
- Two ingredient samples have more than one eligible NE observation; targets remain separate and never cross a study boundary.
- Negative out-of-sample R² values and poorly performing held-out studies are retained.
- Loading a joblib file can execute code. Only load model files obtained from this trusted release, or rerun run_models.py to rebuild them locally.

## Software

Python {platform.python_version()}; scikit-learn {sklearn.__version__}; pandas {pd.__version__}; NumPy {np.__version__}; joblib {joblib.__version__}; matplotlib {matplotlib.__version__}. Random seed: {RANDOM_SEED}.
"""
    path.write_text(text, encoding="utf-8")


def run(database: Path, output_dir: Path, sql_path: Path | None = None) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    sql_path = sql_path or Path(__file__).with_name("extract_pig_ne_cohort.sql")
    cohort = read_cohort(database, sql_path)
    counts = validate_cohort(cohort)
    cohort_dir, results_dir, models_dir = (
        output_dir / "cohort", output_dir / "results", output_dir / "models"
    )
    cohort_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    cohort.to_csv(cohort_dir / "pig_ne_modeling_cohort.csv", index=False, encoding="utf-8")
    summary = {
        **counts,
        "study_observation_counts": {
            str(int(key)): int(value) for key, value in cohort.groupby("study_id").size().items()
        },
        "feature_order": FEATURES,
        "feature_units": {
            **{name: "% dry matter" for name in FEATURES[:-1]}, "GE": "MJ/kg dry matter"
        },
        "target": "NE",
        "target_unit": "MJ/kg dry matter",
        "database_access": "SQLite URI mode=ro with PRAGMA query_only=ON",
    }
    write_json(cohort_dir / "cohort_summary.json", summary)

    predictions, audit = nested_oof_predictions(cohort)
    comparison, per_study = calculate_metrics(predictions)
    predictions.to_csv(results_dir / "out_of_fold_predictions.csv", index=False, encoding="utf-8")
    comparison.to_csv(results_dir / "model_comparison.csv", index=False, encoding="utf-8")
    per_study.to_csv(results_dir / "per_study_metrics.csv", index=False, encoding="utf-8")
    audit.update({
        "primary_metric": "Equal-weight mean of held-out-study MAE",
        "random_seed": RANDOM_SEED,
        "model_comparison": comparison.to_dict(orient="records"),
    })
    write_json(results_dir / "grouped_cv_metrics.json", audit)
    make_plots(predictions, comparison, results_dir)

    final_details = fit_final_models(cohort, models_dir)
    model_metadata = {
        "artifact_purpose": "Data-reuse demonstration; not a universal equation or feeding recommendation",
        "feature_order": FEATURES,
        "feature_units": {
            **{name: "% dry matter" for name in FEATURES[:-1]}, "GE": "MJ/kg dry matter"
        },
        "target": "Pig ingredient NE",
        "target_unit": "MJ/kg dry matter",
        "reporting_basis": "dry matter",
        "training_observations": counts["observations"],
        "training_ingredient_samples": counts["ingredient_samples"],
        "training_studies": counts["studies"],
        "random_seed": RANDOM_SEED,
        "parameter_ranges": {
            "ridge_alpha": RIDGE_ALPHAS,
            "pls_components": PLS_COMPONENTS,
            "random_forest": {
                "n_estimators": 500,
                "max_depth": [2, 3, None],
                "min_samples_leaf": [1, 2, 4],
                "max_features": ["sqrt", 1.0],
            },
        },
        "dependencies": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scikit_learn": sklearn.__version__,
            "joblib": joblib.__version__,
            "matplotlib": matplotlib.__version__,
        },
        "models": final_details,
    }
    write_json(models_dir / "model_metadata.json", model_metadata)
    write_model_card(output_dir / "model_card.md", comparison, counts)
    return {
        "counts": counts,
        "comparison": comparison.to_dict(orient="records"),
        "output_dir": str(output_dir),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, help="released SQLite database (opened read-only)")
    parser.add_argument(
        "--output-dir", type=Path, default=Path(__file__).resolve().parent,
        help="example directory receiving cohort, result, and model artifacts",
    )
    parser.add_argument("--sql", type=Path, default=None, help="alternate extraction SQL")
    args = parser.parse_args()
    print(json.dumps(run(args.database, args.output_dir, args.sql), ensure_ascii=False, indent=2, default=_json_value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
