"""Build the all-ingredient pig ME reuse example with nested study-group CV."""

from __future__ import annotations

import argparse
import itertools
import json
import os
import platform
import sqlite3
import warnings
from pathlib import Path
from typing import Any

import joblib
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import sklearn
from joblib import Parallel, delayed
from sklearn.cross_decomposition import PLSRegression
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

RANDOM_SEED = 20260813
FEATURES = ["CP", "EE", "Ash", "NDF", "ADF", "GE"]
MODEL_ORDER = [
    "training_mean",
    "ordinary_linear_regression",
    "ridge_regression",
    "partial_least_squares",
    "random_forest",
]
DISPLAY_NAMES = {
    "training_mean": "Training mean",
    "ordinary_linear_regression": "Ordinary linear regression",
    "ridge_regression": "Ridge regression",
    "partial_least_squares": "Partial least squares",
    "random_forest": "Random forest",
}
RIDGE_ALPHAS = [0.01, 0.1, 1.0, 10.0, 100.0, 1000.0]
RF_GRID = [
    {"max_depth": depth, "min_samples_leaf": leaf, "max_features": max_features}
    for depth, leaf, max_features in itertools.product(
        [2, 3, None], [1, 2, 4], ["sqrt", 1.0]
    )
]
OVERALL_GATE = {
    "observations": 204,
    "ingredient_samples": 202,
    "studies": 22,
    "ingredient_types": 22,
}
RESOLVED_CONFLICT_GATE = 9
PRIMARY_GATE = OVERALL_GATE
COMPLETE_CASE_GATE = {
    "observations": 172,
    "ingredient_samples": 170,
    "studies": 18,
    "ingredient_types": 18,
}


def _json_value(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=_json_value)
        + "\n",
        encoding="utf-8",
    )


def read_candidates(database: Path, sql_path: Path) -> pd.DataFrame:
    """Read candidates from SQLite using an enforced read-only connection."""
    database = database.resolve()
    if not database.is_file():
        raise FileNotFoundError(f"SQLite database not found: {database}")
    uri = f"file:{database.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.execute("PRAGMA query_only = ON")
        frame = pd.read_sql_query(sql_path.read_text(encoding="utf-8"), connection)
    return frame


def cohort_counts(frame: pd.DataFrame) -> dict[str, int]:
    return {
        "observations": int(len(frame)),
        "ingredient_samples": int(frame["ingredient_sample_id"].nunique()),
        "studies": int(frame["study_id"].nunique()),
        "ingredient_types": int(frame["ingredient_name"].nunique()),
    }


def _require_gate(label: str, obtained: dict[str, int], expected: dict[str, int]) -> None:
    if obtained != expected:
        raise RuntimeError(
            f"{label} stop gate failed; expected {expected}, obtained {obtained}. "
            "No model results were generated."
        )


def resolve_cohorts(
    candidates: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Resolve feature candidates without averaging and build both analysis queues."""
    target_columns = [
        "nutrition_observation_id",
        "study_id",
        "study_title",
        "ingredient_sample_id",
        "ingredient_name",
        "ME",
        "target_unit",
        "basis",
        "target_source_workbook",
        "target_source_sheet",
        "target_source_row",
        "target_source_column",
    ]
    missing = [name for name in target_columns if name not in candidates.columns]
    if missing:
        raise ValueError(f"Candidate table is missing columns: {missing}")
    targets = candidates[target_columns].drop_duplicates("nutrition_observation_id")
    _require_gate("Overall pig ME", cohort_counts(targets), OVERALL_GATE)

    output_rows: list[dict[str, Any]] = []
    conflict_rows: list[dict[str, Any]] = []
    exclusion_rows: list[dict[str, Any]] = []
    for target_id, group in candidates.groupby("nutrition_observation_id", sort=True):
        first = group.iloc[0]
        row = {column: first[column] for column in target_columns}
        row["composition_unit"] = "%"
        row["GE_unit"] = "MJ/kg"
        unresolved: list[str] = []
        for feature in FEATURES:
            subset = group.loc[group["feature_name"].eq(feature)].drop_duplicates(
                "component_observation_id"
            )
            if subset.empty:
                row[feature] = np.nan
                row[f"{feature}_component_observation_id"] = pd.NA
                row[f"{feature}_resolution"] = "missing"
                continue
            if len(subset) == 1:
                chosen = subset.iloc[0]
                rule = "unique_candidate"
            else:
                same_position = subset.loc[
                    subset["feature_source_workbook"].eq(first["target_source_workbook"])
                    & subset["feature_source_sheet"].eq(first["target_source_sheet"])
                    & subset["feature_source_column"].eq(first["target_source_column"])
                ]
                if len(same_position) != 1:
                    unresolved.append(feature)
                    row[feature] = np.nan
                    row[f"{feature}_component_observation_id"] = pd.NA
                    row[f"{feature}_resolution"] = "unresolved_multiple_candidates"
                    exclusion_rows.append(
                        {
                            "nutrition_observation_id": int(target_id),
                            "study_id": int(first["study_id"]),
                            "ingredient_sample_id": int(first["ingredient_sample_id"]),
                            "ingredient_name": first["ingredient_name"],
                            "feature_name": feature,
                            "candidate_component_observation_ids": json.dumps(
                                [int(v) for v in subset["component_observation_id"]]
                            ),
                            "candidate_values": json.dumps(
                                [float(v) for v in subset["feature_value"]]
                            ),
                            "reason": "multiple candidates without one exact target source-position match",
                        }
                    )
                    continue
                chosen = same_position.iloc[0]
                rule = "exact_target_workbook_sheet_column_match"
                candidates_payload = []
                for _, candidate in subset.sort_values("component_observation_id").iterrows():
                    candidates_payload.append(
                        {
                            "component_observation_id": int(candidate["component_observation_id"]),
                            "value": float(candidate["feature_value"]),
                            "workbook": candidate["feature_source_workbook"],
                            "sheet": candidate["feature_source_sheet"],
                            "row": int(candidate["feature_source_row"]),
                            "column": int(candidate["feature_source_column"]),
                        }
                    )
                conflict_rows.append(
                    {
                        "nutrition_observation_id": int(target_id),
                        "study_id": int(first["study_id"]),
                        "ingredient_sample_id": int(first["ingredient_sample_id"]),
                        "ingredient_name": first["ingredient_name"],
                        "feature_name": feature,
                        "target_source_workbook": first["target_source_workbook"],
                        "target_source_sheet": first["target_source_sheet"],
                        "target_source_row": int(first["target_source_row"]),
                        "target_source_column": int(first["target_source_column"]),
                        "candidate_count": int(len(subset)),
                        "candidate_details": json.dumps(
                            candidates_payload, ensure_ascii=False, sort_keys=True
                        ),
                        "selected_component_observation_id": int(
                            chosen["component_observation_id"]
                        ),
                        "selected_value": float(chosen["feature_value"]),
                        "resolution_rule": rule,
                        "averaging_performed": False,
                    }
                )
            row[feature] = float(chosen["feature_value"])
            row[f"{feature}_component_observation_id"] = int(
                chosen["component_observation_id"]
            )
            row[f"{feature}_resolution"] = rule
        row["unresolved_features"] = ";".join(unresolved)
        output_rows.append(row)

    resolved = pd.DataFrame(output_rows).sort_values(
        ["study_id", "ingredient_sample_id", "nutrition_observation_id"]
    ).reset_index(drop=True)
    conflicts = pd.DataFrame(conflict_rows)
    exclusion_columns = [
        "nutrition_observation_id",
        "study_id",
        "ingredient_sample_id",
        "ingredient_name",
        "feature_name",
        "candidate_component_observation_ids",
        "candidate_values",
        "reason",
    ]
    exclusions = pd.DataFrame(exclusion_rows, columns=exclusion_columns)
    if len(conflicts) != RESOLVED_CONFLICT_GATE or not exclusions.empty:
        raise RuntimeError(
            "Feature-conflict stop gate failed. Expected nine source-position-resolved "
            f"conflicts and zero unresolved exclusions; obtained {len(conflicts)} and "
            f"{len(exclusions)}."
        )
    primary = resolved.loc[resolved["unresolved_features"].eq("")].copy()
    _require_gate("Primary resolved cohort", cohort_counts(primary), PRIMARY_GATE)
    complete = primary.dropna(subset=FEATURES).copy()
    _require_gate("Complete-case sensitivity cohort", cohort_counts(complete), COMPLETE_CASE_GATE)
    if not np.isfinite(primary["ME"].to_numpy(dtype=float)).all():
        raise ValueError("ME target contains missing or non-finite values")
    for feature in FEATURES:
        values = primary[feature].dropna().to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise ValueError(f"{feature} contains non-finite values")
    summary = {
        "overall_gate": OVERALL_GATE,
        "resolved_feature_conflicts": int(len(conflicts)),
        "unresolved_feature_conflicts": int(len(exclusions)),
        "resolution_rule": "exact target workbook, sheet, and source column match",
        "averaging_performed": False,
        "primary": cohort_counts(primary),
        "complete_case_sensitivity": cohort_counts(complete),
        "study_78_complete_case_observations": int(complete["study_id"].eq(78).sum()),
        "study_78_missing_features": ["EE", "NDF", "ADF"],
    }
    return primary, complete, conflicts, exclusions, summary


def build_estimator(model_name: str, params: dict[str, Any] | None = None) -> Pipeline:
    params = params or {}
    imputer = SimpleImputer(
        strategy="median", add_indicator=True, keep_empty_features=True
    )
    if model_name == "ordinary_linear_regression":
        model: Any = LinearRegression()
    elif model_name == "ridge_regression":
        model = Ridge(alpha=float(params["alpha"]))
    elif model_name == "partial_least_squares":
        model = PLSRegression(
            n_components=int(params["n_components"]), scale=False, max_iter=1000
        )
    elif model_name == "random_forest":
        return Pipeline(
            [
                ("imputer", imputer),
                (
                    "model",
                    RandomForestRegressor(
                        n_estimators=500,
                        max_depth=params["max_depth"],
                        min_samples_leaf=int(params["min_samples_leaf"]),
                        max_features=params["max_features"],
                        random_state=RANDOM_SEED,
                        n_jobs=1,
                    ),
                ),
            ]
        )
    else:
        raise ValueError(f"Unknown model: {model_name}")
    return Pipeline([("imputer", imputer), ("scaler", StandardScaler()), ("model", model)])


def _predict(estimator: Pipeline, x: np.ndarray) -> np.ndarray:
    return np.asarray(estimator.predict(x), dtype=float).reshape(-1)


def _safe_pls_components(x: np.ndarray, y: np.ndarray, groups: np.ndarray) -> list[int]:
    safe = 12
    for train_index, _ in LeaveOneGroupOut().split(x, y, groups):
        transformed = SimpleImputer(
            strategy="median", add_indicator=True, keep_empty_features=True
        ).fit_transform(x[train_index])
        safe = min(safe, transformed.shape[1], len(train_index) - 1)
    if safe < 1:
        raise ValueError("No valid PLS component count remains in inner training folds")
    return list(range(1, int(safe) + 1))


def parameter_candidates(
    model_name: str, x: np.ndarray, y: np.ndarray, groups: np.ndarray
) -> list[dict[str, Any]]:
    if model_name == "ridge_regression":
        return [{"alpha": alpha} for alpha in RIDGE_ALPHAS]
    if model_name == "partial_least_squares":
        return [
            {"n_components": value}
            for value in _safe_pls_components(x, y, groups)
        ]
    if model_name == "random_forest":
        return [dict(item) for item in RF_GRID]
    return [{}]


def select_parameters(
    model_name: str, x: np.ndarray, y: np.ndarray, groups: np.ndarray
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Tune by equal-weight inner-study MAE using outer-training data only."""
    unique_groups = np.unique(groups)
    if len(unique_groups) < 2:
        raise ValueError("Nested group tuning requires at least two studies")
    candidates: list[dict[str, Any]] = []
    splits = list(LeaveOneGroupOut().split(x, y, groups))
    for params in parameter_candidates(model_name, x, y, groups):
        folds = []
        for train_index, valid_index in splits:
            train_studies = sorted({int(v) for v in groups[train_index]})
            valid_studies = sorted({int(v) for v in groups[valid_index]})
            if len(valid_studies) != 1 or set(train_studies) & set(valid_studies):
                raise AssertionError("Inner study leakage detected")
            estimator = build_estimator(model_name, params)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=UserWarning)
                estimator.fit(x[train_index], y[train_index])
            prediction = _predict(estimator, x[valid_index])
            folds.append(
                {
                    "held_out_study_id": valid_studies[0],
                    "training_study_ids": train_studies,
                    "n_train": int(len(train_index)),
                    "n_valid": int(len(valid_index)),
                    "mae": float(mean_absolute_error(y[valid_index], prediction)),
                }
            )
        candidates.append(
            {
                "params": params,
                "study_macro_mae": float(np.mean([item["mae"] for item in folds])),
                "inner_folds": folds,
            }
        )
    candidates.sort(
        key=lambda item: (
            item["study_macro_mae"],
            json.dumps(item["params"], sort_keys=True),
        )
    )
    return dict(candidates[0]["params"]), candidates


def _outer_fold(
    fold_number: int,
    train_index: np.ndarray,
    test_index: np.ndarray,
    cohort: pd.DataFrame,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    x = cohort[FEATURES].to_numpy(dtype=float)
    y = cohort["ME"].to_numpy(dtype=float)
    groups = cohort["study_id"].to_numpy(dtype=int)
    train_studies = sorted({int(v) for v in groups[train_index]})
    held_studies = sorted({int(v) for v in groups[test_index]})
    if len(held_studies) != 1 or set(train_studies) & set(held_studies):
        raise AssertionError("Outer study leakage detected")
    held_study = held_studies[0]
    predictions: dict[str, tuple[np.ndarray, dict[str, Any]]] = {
        "training_mean": (
            np.repeat(float(np.mean(y[train_index])), len(test_index)),
            {},
        )
    }
    audit_models: dict[str, Any] = {
        "training_mean": {"selected_params": {}},
        "ordinary_linear_regression": {"selected_params": {}},
    }
    ordinary = build_estimator("ordinary_linear_regression")
    ordinary.fit(x[train_index], y[train_index])
    predictions["ordinary_linear_regression"] = (_predict(ordinary, x[test_index]), {})
    for model_name in ["ridge_regression", "partial_least_squares", "random_forest"]:
        selected, inner_candidates = select_parameters(
            model_name, x[train_index], y[train_index], groups[train_index]
        )
        estimator = build_estimator(model_name, selected)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=UserWarning)
            estimator.fit(x[train_index], y[train_index])
        predictions[model_name] = (_predict(estimator, x[test_index]), selected)
        audit_models[model_name] = {
            "selected_params": selected,
            "inner_candidates": inner_candidates,
        }
    rows: list[dict[str, Any]] = []
    for model_name in MODEL_ORDER:
        values, selected = predictions[model_name]
        for position, prediction in zip(test_index, values):
            source = cohort.iloc[int(position)]
            actual = float(source["ME"])
            rows.append(
                {
                    "model": model_name,
                    "model_display_name": DISPLAY_NAMES[model_name],
                    "outer_fold": fold_number,
                    "held_out_study_id": held_study,
                    "study_id": int(source["study_id"]),
                    "study_title": source["study_title"],
                    "ingredient_sample_id": int(source["ingredient_sample_id"]),
                    "ingredient_name": source["ingredient_name"],
                    "nutrition_observation_id": int(source["nutrition_observation_id"]),
                    "observed_ME_MJ_per_kg_DM": actual,
                    "predicted_ME_MJ_per_kg_DM": float(prediction),
                    "residual_observed_minus_predicted": actual - float(prediction),
                    "selected_params": json.dumps(selected, sort_keys=True),
                }
            )
    audit = {
        "outer_fold": fold_number,
        "held_out_study_id": held_study,
        "training_study_ids": train_studies,
        "n_train": int(len(train_index)),
        "n_test": int(len(test_index)),
        "models": audit_models,
    }
    return rows, audit


def nested_oof_predictions(
    cohort: pd.DataFrame, jobs: int
) -> tuple[pd.DataFrame, dict[str, Any]]:
    x = cohort[FEATURES].to_numpy(dtype=float)
    y = cohort["ME"].to_numpy(dtype=float)
    groups = cohort["study_id"].to_numpy(dtype=int)
    splits = list(LeaveOneGroupOut().split(x, y, groups))
    completed = Parallel(n_jobs=jobs, backend="loky", verbose=10)(
        delayed(_outer_fold)(number, train, test, cohort)
        for number, (train, test) in enumerate(splits, start=1)
    )
    rows = [row for fold_rows, _ in completed for row in fold_rows]
    audits = [audit for _, audit in completed]
    predictions = pd.DataFrame(rows).sort_values(
        ["model", "nutrition_observation_id"]
    ).reset_index(drop=True)
    if len(predictions) != len(cohort) * len(MODEL_ORDER):
        raise AssertionError("Out-of-fold prediction table is incomplete")
    if predictions.duplicated(["model", "nutrition_observation_id"]).any():
        raise AssertionError("A target has duplicate out-of-fold predictions")
    return predictions, {
        "outer_strategy": "LeaveOneGroupOut by study",
        "inner_strategy": "LeaveOneGroupOut by study within each outer training set",
        "outer_folds": audits,
    }


def calculate_metrics(predictions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    per_study_rows = []
    for (model, study_id), frame in predictions.groupby(["model", "study_id"], sort=True):
        observed = frame["observed_ME_MJ_per_kg_DM"].to_numpy(dtype=float)
        predicted = frame["predicted_ME_MJ_per_kg_DM"].to_numpy(dtype=float)
        per_study_rows.append(
            {
                "model": model,
                "model_display_name": DISPLAY_NAMES[model],
                "study_id": int(study_id),
                "n_observations": int(len(frame)),
                "mae": float(mean_absolute_error(observed, predicted)),
                "rmse": float(mean_squared_error(observed, predicted) ** 0.5),
                "mean_prediction_bias_predicted_minus_observed": float(
                    np.mean(predicted - observed)
                ),
            }
        )
    per_study = pd.DataFrame(per_study_rows)
    baseline_macro = float(
        per_study.loc[per_study["model"].eq("training_mean"), "mae"].mean()
    )
    rows = []
    for model in MODEL_ORDER:
        frame = predictions.loc[predictions["model"].eq(model)]
        observed = frame["observed_ME_MJ_per_kg_DM"].to_numpy(dtype=float)
        predicted = frame["predicted_ME_MJ_per_kg_DM"].to_numpy(dtype=float)
        macro = float(per_study.loc[per_study["model"].eq(model), "mae"].mean())
        difference = macro - baseline_macro
        rows.append(
            {
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
            }
        )
    comparison = pd.DataFrame(rows).sort_values(["study_macro_mae", "model"]).reset_index(drop=True)
    comparison.insert(0, "study_macro_rank", np.arange(1, len(comparison) + 1, dtype=np.int64))
    return comparison, per_study.sort_values(["model", "study_id"]).reset_index(drop=True)


def missingness_summary(primary: pd.DataFrame) -> pd.DataFrame:
    rows = []
    scopes = [("overall", "all", primary)] + [
        ("study", str(int(study_id)), frame)
        for study_id, frame in primary.groupby("study_id", sort=True)
    ]
    for scope, scope_id, frame in scopes:
        for feature in FEATURES:
            missing = int(frame[feature].isna().sum())
            rows.append(
                {
                    "scope": scope,
                    "scope_id": scope_id,
                    "feature": feature,
                    "n_observations": int(len(frame)),
                    "n_missing": missing,
                    "percent_missing": 100.0 * missing / len(frame),
                }
            )
    return pd.DataFrame(rows)


def ingredient_coverage(primary: pd.DataFrame, complete: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for name in sorted(primary["ingredient_name"].unique()):
        main = primary.loc[primary["ingredient_name"].eq(name)]
        sensitivity = complete.loc[complete["ingredient_name"].eq(name)]
        rows.append(
            {
                "ingredient_name": name,
                "primary_observations": int(len(main)),
                "primary_samples": int(main["ingredient_sample_id"].nunique()),
                "primary_studies": int(main["study_id"].nunique()),
                "complete_case_observations": int(len(sensitivity)),
                "complete_case_samples": int(sensitivity["ingredient_sample_id"].nunique()),
                "complete_case_studies": int(sensitivity["study_id"].nunique()),
            }
        )
    return pd.DataFrame(rows)


def transformed_feature_names(pipeline: Pipeline) -> list[str]:
    imputer: SimpleImputer = pipeline.named_steps["imputer"]
    return [str(v) for v in imputer.get_feature_names_out(FEATURES)]


def fit_final_models(cohort: pd.DataFrame, models_dir: Path) -> dict[str, Any]:
    models_dir.mkdir(parents=True, exist_ok=True)
    x = cohort[FEATURES].to_numpy(dtype=float)
    y = cohort["ME"].to_numpy(dtype=float)
    groups = cohort["study_id"].to_numpy(dtype=int)
    metadata: dict[str, Any] = {}
    for model_name in MODEL_ORDER[1:]:
        if model_name in {"ridge_regression", "partial_least_squares", "random_forest"}:
            selected, candidates = select_parameters(model_name, x, y, groups)
        else:
            selected, candidates = {}, []
        estimator = build_estimator(model_name, selected)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=UserWarning)
            estimator.fit(x, y)
        joblib.dump(estimator, models_dir / f"{model_name}.joblib")
        names = transformed_feature_names(estimator)
        model = estimator.named_steps["model"]
        if model_name in {"ordinary_linear_regression", "ridge_regression"}:
            details = {
                "transformed_feature_coefficients": dict(
                    zip(names, np.asarray(model.coef_, dtype=float).reshape(-1).tolist())
                ),
                "transformed_intercept": float(np.asarray(model.intercept_).reshape(-1)[0]),
            }
        elif model_name == "partial_least_squares":
            details = {
                "transformed_feature_names": names,
                "x_weights": np.asarray(model.x_weights_).tolist(),
                "x_loadings": np.asarray(model.x_loadings_).tolist(),
                "y_loadings": np.asarray(model.y_loadings_).tolist(),
                "coefficients": np.asarray(model.coef_).reshape(-1).tolist(),
            }
        else:
            details = {
                "feature_importances": dict(zip(names, model.feature_importances_.tolist()))
            }
        metadata[model_name] = {
            "selected_hyperparameters": selected,
            "full_data_inner_candidates": candidates,
            "transformed_feature_names": names,
            "model_details": details,
        }
    return metadata


def make_plots(
    primary_predictions: pd.DataFrame,
    primary_comparison: pd.DataFrame,
    sensitivity_comparison: pd.DataFrame,
    results_dir: Path,
) -> None:
    limits = [
        float(
            min(
                primary_predictions["observed_ME_MJ_per_kg_DM"].min(),
                primary_predictions["predicted_ME_MJ_per_kg_DM"].min(),
            )
        ),
        float(
            max(
                primary_predictions["observed_ME_MJ_per_kg_DM"].max(),
                primary_predictions["predicted_ME_MJ_per_kg_DM"].max(),
            )
        ),
    ]
    figure, axes = plt.subplots(2, 3, figsize=(11, 7), sharex=True, sharey=True)
    for axis, model_name in zip(axes.flat, MODEL_ORDER):
        frame = primary_predictions.loc[primary_predictions["model"].eq(model_name)]
        axis.scatter(
            frame["observed_ME_MJ_per_kg_DM"],
            frame["predicted_ME_MJ_per_kg_DM"],
            s=18,
            alpha=0.7,
        )
        axis.plot(limits, limits, "--", color="black", linewidth=1)
        axis.set_title(DISPLAY_NAMES[model_name], fontsize=9)
        axis.grid(alpha=0.2)
    axes.flat[-1].axis("off")
    figure.supxlabel("Observed ME (MJ/kg DM)")
    figure.supylabel("Out-of-fold predicted ME (MJ/kg DM)")
    figure.tight_layout()
    figure.savefig(results_dir / "observed_vs_predicted.png", dpi=300, bbox_inches="tight")
    plt.close(figure)

    primary_map = primary_comparison.set_index("model")["study_macro_mae"]
    sensitivity_map = sensitivity_comparison.set_index("model")["study_macro_mae"]
    position = np.arange(len(MODEL_ORDER))
    width = 0.38
    figure, axis = plt.subplots(figsize=(9, 4.8))
    axis.bar(position - width / 2, [primary_map[m] for m in MODEL_ORDER], width, label="Primary")
    axis.bar(position + width / 2, [sensitivity_map[m] for m in MODEL_ORDER], width, label="Complete case")
    axis.set_xticks(position, [DISPLAY_NAMES[m] for m in MODEL_ORDER], rotation=20, ha="right")
    axis.set_ylabel("Study-macro MAE (MJ/kg DM; lower is better)")
    axis.grid(axis="y", alpha=0.2)
    axis.legend()
    figure.tight_layout()
    figure.savefig(results_dir / "model_comparison.png", dpi=300, bbox_inches="tight")
    plt.close(figure)


def write_model_card(
    path: Path,
    primary: pd.DataFrame,
    complete: pd.DataFrame,
    comparison: pd.DataFrame,
) -> None:
    best = comparison.iloc[0]
    baseline = comparison.loc[comparison["model"].eq("training_mean")].iloc[0]
    path.write_text(
        f"""# Model card: all-ingredient pig metabolizable-energy example

## Intended use

This is a transparent database-reuse demonstration, not a universal ME equation, a feeding recommendation, or an externally validated production model.

## Data and conflict handling

- Primary queue: {len(primary)} ME observations, {primary['ingredient_sample_id'].nunique()} samples, {primary['study_id'].nunique()} studies, and {primary['ingredient_name'].nunique()} ingredient types.
- Complete-case sensitivity queue: {len(complete)} observations, {complete['ingredient_sample_id'].nunique()} samples, {complete['study_id'].nunique()} studies, and {complete['ingredient_name'].nunique()} ingredient types.
- Target: pig ME in MJ/kg dry matter. Chicken observations are excluded by the extraction SQL.
- Fixed predictors: {', '.join(FEATURES)}. CP, EE, Ash, NDF, and ADF are % dry matter; GE is MJ/kg dry matter.
- Nine study 78 samples each had two GE candidates. The selected GE uniquely shared the ME target's workbook, sheet, and source column. No GE values were averaged. Those samples remain in the primary queue but not the complete-case queue because EE, NDF, and ADF are missing.

## Preprocessing and validation

Missing predictors are retained in the primary CSV. Within every training fold, median imputation and missingness indicators are fitted only on that fold. Linear, ridge, and PLS pipelines then standardize the transformed predictors. Random forest uses the same training-only imputer without scaling.

Outer validation leaves out one whole study. Ridge, PLS, and random-forest parameters are selected in a second leave-one-study-out loop using only the outer training studies. Every held-out study and every poor fold is retained. The primary metric is the equal-weight mean of held-out-study MAEs.

The lowest prespecified primary metric was **{best['model_display_name']}** ({best['study_macro_mae']:.6f} MJ/kg DM); the training-mean baseline was {baseline['study_macro_mae']:.6f} MJ/kg DM. This ranking applies only to this cohort and validation design.

## Saved pipelines and safety

Four non-baseline pipelines were refitted on the full primary queue after grouped internal selection. They accept missing predictors under the input contract documented in README.md. Loading joblib can execute code; load only trusted local artifacts or rebuild them with run_models.py.

## Limitations

Ingredient coverage, study protocols, analytical methods, and missingness are heterogeneous. Study-group validation reduces but does not remove transportability risk. Predicted values must not replace laboratory analysis or expert ration formulation.

## Software

Python {platform.python_version()}; scikit-learn {sklearn.__version__}; pandas {pd.__version__}; NumPy {np.__version__}; joblib {joblib.__version__}; matplotlib {matplotlib.__version__}. Random seed: {RANDOM_SEED}.
""",
        encoding="utf-8",
    )


def run(database: Path, output_dir: Path, sql_path: Path | None = None, jobs: int = 1) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    sql_path = sql_path or Path(__file__).with_name("extract_pig_me_cohort.sql")
    candidates = read_candidates(database, sql_path)
    primary, complete, conflicts, exclusions, queue_summary = resolve_cohorts(candidates)
    cohort_dir = output_dir / "cohort"
    results_dir = output_dir / "results"
    models_dir = output_dir / "models"
    for directory in [cohort_dir, results_dir, models_dir]:
        directory.mkdir(parents=True, exist_ok=True)

    primary.to_csv(cohort_dir / "pig_me_all_ingredients_primary.csv", index=False, encoding="utf-8")
    complete.to_csv(cohort_dir / "pig_me_complete_case_sensitivity.csv", index=False, encoding="utf-8")
    conflicts.to_csv(cohort_dir / "resolved_feature_conflicts.csv", index=False, encoding="utf-8")
    exclusions.to_csv(cohort_dir / "excluded_ambiguous_features.csv", index=False, encoding="utf-8")
    coverage = ingredient_coverage(primary, complete)
    coverage.to_csv(cohort_dir / "ingredient_coverage.csv", index=False, encoding="utf-8")
    queue_summary.update(
        {
            "feature_order": FEATURES,
            "feature_units": {**{f: "% dry matter" for f in FEATURES[:-1]}, "GE": "MJ/kg dry matter"},
            "target": "pig ME",
            "target_unit": "MJ/kg dry matter",
            "database_access": "SQLite URI mode=ro with PRAGMA query_only=ON",
            "primary_study_observation_counts": {
                str(int(k)): int(v) for k, v in primary.groupby("study_id").size().items()
            },
        }
    )
    write_json(cohort_dir / "cohort_summary.json", queue_summary)
    missingness_summary(primary).to_csv(results_dir / "missingness_summary.csv", index=False, encoding="utf-8")

    primary_predictions, primary_audit = nested_oof_predictions(primary, jobs)
    sensitivity_predictions, sensitivity_audit = nested_oof_predictions(complete, jobs)
    primary_comparison, primary_per_study = calculate_metrics(primary_predictions)
    sensitivity_comparison, sensitivity_per_study = calculate_metrics(sensitivity_predictions)
    primary_predictions.to_csv(results_dir / "primary_out_of_fold_predictions.csv", index=False, encoding="utf-8")
    sensitivity_predictions.to_csv(results_dir / "sensitivity_out_of_fold_predictions.csv", index=False, encoding="utf-8")
    primary_comparison.to_csv(results_dir / "primary_model_comparison.csv", index=False, encoding="utf-8")
    sensitivity_comparison.to_csv(results_dir / "sensitivity_model_comparison.csv", index=False, encoding="utf-8")
    primary_per_study.to_csv(results_dir / "primary_per_study_metrics.csv", index=False, encoding="utf-8")
    sensitivity_per_study.to_csv(results_dir / "sensitivity_per_study_metrics.csv", index=False, encoding="utf-8")
    audit = {
        "primary_metric": "equal-weight mean of held-out-study MAE",
        "random_seed": RANDOM_SEED,
        "preprocessing": "training-fold median imputation with missing indicators; scaling for OLS/ridge/PLS",
        "primary": {**primary_audit, "model_comparison": primary_comparison.to_dict(orient="records")},
        "complete_case_sensitivity": {**sensitivity_audit, "model_comparison": sensitivity_comparison.to_dict(orient="records")},
    }
    write_json(results_dir / "grouped_cv_metrics.json", audit)
    make_plots(primary_predictions, primary_comparison, sensitivity_comparison, results_dir)

    final_details = fit_final_models(primary, models_dir)
    model_metadata = {
        "artifact_purpose": "data-reuse demonstration; not a universal equation or feeding recommendation",
        "feature_order": FEATURES,
        "feature_units": {**{f: "% dry matter" for f in FEATURES[:-1]}, "GE": "MJ/kg dry matter"},
        "target": "pig ingredient ME",
        "target_unit": "MJ/kg dry matter",
        "reporting_basis": "dry matter",
        "missing_predictors_allowed": True,
        "minimum_nonmissing_predictors_for_prediction": 3,
        "imputation": "median plus missing indicators, fitted inside each training fold",
        "training_queue": queue_summary["primary"],
        "complete_case_sensitivity_queue": queue_summary["complete_case_sensitivity"],
        "resolved_feature_conflicts": queue_summary["resolved_feature_conflicts"],
        "conflict_resolution_rule": queue_summary["resolution_rule"],
        "random_seed": RANDOM_SEED,
        "parameter_ranges": {
            "ridge_alpha": RIDGE_ALPHAS,
            "pls_components": "1 through the safe transformed-feature/training-sample limit in every inner fold",
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
    write_model_card(output_dir / "model_card.md", primary, complete, primary_comparison)
    return {
        "primary": queue_summary["primary"],
        "complete_case_sensitivity": queue_summary["complete_case_sensitivity"],
        "resolved_feature_conflicts": queue_summary["resolved_feature_conflicts"],
        "primary_model_comparison": primary_comparison.to_dict(orient="records"),
        "output_dir": str(output_dir),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, help="released SQLite database (opened read-only)")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--sql", type=Path, default=None)
    parser.add_argument("--jobs", type=int, default=max(1, min(8, os.cpu_count() or 1)))
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    print(json.dumps(run(args.database, args.output_dir, args.sql, args.jobs), ensure_ascii=False, indent=2, default=_json_value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
