from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("sklearn")
pytest.importorskip("joblib")
pytest.importorskip("matplotlib")

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "metabolizable_energy_all_ingredients"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


models = load_module("all_ingredient_me_models", EXAMPLE / "run_models.py")
predictor = load_module("all_ingredient_me_predictor", EXAMPLE / "predict.py")


def test_published_queue_gates_missingness_and_ingredient_coverage():
    primary = pd.read_csv(EXAMPLE / "cohort" / "pig_me_all_ingredients_primary.csv")
    complete = pd.read_csv(EXAMPLE / "cohort" / "pig_me_complete_case_sensitivity.csv")
    assert models.cohort_counts(primary) == models.PRIMARY_GATE
    assert models.cohort_counts(complete) == models.COMPLETE_CASE_GATE
    assert primary["ingredient_name"].nunique() == 22
    assert primary[models.FEATURES].isna().any().any()
    assert not complete[models.FEATURES].isna().any().any()
    coverage = pd.read_csv(EXAMPLE / "cohort" / "ingredient_coverage.csv")
    assert len(coverage) == 22
    assert set(coverage["ingredient_name"]) == set(primary["ingredient_name"])


def test_nine_ge_conflicts_use_unique_source_position_without_averaging():
    resolved = pd.read_csv(EXAMPLE / "cohort" / "resolved_feature_conflicts.csv")
    excluded = pd.read_csv(EXAMPLE / "cohort" / "excluded_ambiguous_features.csv")
    assert len(resolved) == 9 and excluded.empty
    assert set(resolved["study_id"]) == {78}
    assert set(resolved["feature_name"]) == {"GE"}
    assert set(resolved["resolution_rule"]) == {
        "exact_target_workbook_sheet_column_match"
    }
    assert not resolved["averaging_performed"].astype(bool).any()
    for _, row in resolved.iterrows():
        candidates = json.loads(row["candidate_details"])
        matching = [
            item
            for item in candidates
            if item["workbook"] == row["target_source_workbook"]
            and item["sheet"] == row["target_source_sheet"]
            and item["column"] == row["target_source_column"]
        ]
        assert len(matching) == 1
        assert matching[0]["component_observation_id"] == row["selected_component_observation_id"]
        assert matching[0]["value"] == pytest.approx(row["selected_value"])


def test_study_78_is_primary_but_not_complete_case_for_reported_missing_features():
    primary = pd.read_csv(EXAMPLE / "cohort" / "pig_me_all_ingredients_primary.csv")
    complete = pd.read_csv(EXAMPLE / "cohort" / "pig_me_complete_case_sensitivity.csv")
    study_78 = primary[primary["study_id"].eq(78)]
    assert len(study_78) == 9
    assert study_78["GE"].notna().all()
    assert study_78[["EE", "NDF", "ADF"]].isna().all().all()
    assert not complete["study_id"].eq(78).any()


def test_fold_local_imputer_and_scaler_are_not_fitted_on_full_data():
    x = np.array(
        [[1, np.nan, 2, 3, 4, 5], [3, 2, 4, 5, 6, 7], [100, 100, 100, 100, 100, 100]],
        dtype=float,
    )
    y = np.array([1.0, 2.0, 3.0])
    estimator = models.build_estimator("ordinary_linear_regression").fit(x[:2], y[:2])
    assert estimator.named_steps["imputer"].statistics_[1] == pytest.approx(2.0)
    assert estimator.named_steps["imputer"].statistics_[1] != pytest.approx(
        np.nanmedian(x[:, 1])
    )
    assert estimator.named_steps["scaler"].n_features_in_ == 7


def test_group_audits_have_no_leakage_complete_folds_and_parameter_grids():
    audit = json.loads((EXAMPLE / "results" / "grouped_cv_metrics.json").read_text(encoding="utf-8"))
    for key, expected_folds in [("primary", 22), ("complete_case_sensitivity", 18)]:
        folds = audit[key]["outer_folds"]
        assert len(folds) == expected_folds
        for fold in folds:
            held = fold["held_out_study_id"]
            training = set(fold["training_study_ids"])
            assert held not in training
            assert len(fold["models"]["ridge_regression"]["inner_candidates"]) == 6
            assert len(fold["models"]["random_forest"]["inner_candidates"]) == 18
            for model_name in ["ridge_regression", "partial_least_squares", "random_forest"]:
                detail = fold["models"][model_name]
                candidate_scores = [item["study_macro_mae"] for item in detail["inner_candidates"]]
                selected = detail["selected_params"]
                winner = min(
                    detail["inner_candidates"],
                    key=lambda item: (
                        item["study_macro_mae"], json.dumps(item["params"], sort_keys=True)
                    ),
                )
                assert selected == winner["params"]
                assert winner["study_macro_mae"] == min(candidate_scores)
                for candidate in detail["inner_candidates"]:
                    assert {f["held_out_study_id"] for f in candidate["inner_folds"]} == training
                    for inner_fold in candidate["inner_folds"]:
                        assert inner_fold["held_out_study_id"] not in inner_fold["training_study_ids"]


@pytest.mark.parametrize(
    "prefix,n_rows",
    [("primary", 204), ("sensitivity", 172)],
)
def test_predictions_cover_each_target_once_and_metrics_recompute(prefix: str, n_rows: int):
    predictions = pd.read_csv(EXAMPLE / "results" / f"{prefix}_out_of_fold_predictions.csv")
    comparison = pd.read_csv(EXAMPLE / "results" / f"{prefix}_model_comparison.csv")
    per_study = pd.read_csv(EXAMPLE / "results" / f"{prefix}_per_study_metrics.csv")
    assert len(predictions) == n_rows * 5
    assert set(predictions["model"]) == set(models.MODEL_ORDER)
    for _, frame in predictions.groupby("model"):
        assert len(frame) == n_rows
        assert frame["nutrition_observation_id"].is_unique
        assert frame["study_id"].eq(frame["held_out_study_id"]).all()
    recalculated_comparison, recalculated_per_study = models.calculate_metrics(predictions)
    pd.testing.assert_frame_equal(
        recalculated_comparison, comparison, check_exact=False, rtol=1e-12
    )
    pd.testing.assert_frame_equal(
        recalculated_per_study, per_study, check_exact=False, rtol=1e-12
    )


def test_saved_models_accept_missing_values_and_share_contract():
    metadata = json.loads((EXAMPLE / "models" / "model_metadata.json").read_text(encoding="utf-8"))
    assert metadata["feature_order"] == models.FEATURES
    assert metadata["missing_predictors_allowed"] is True
    sample = pd.DataFrame([[10, np.nan, 5, 20, np.nan, 18]], columns=models.FEATURES)
    for model_name in models.MODEL_ORDER[1:]:
        pipeline = predictor.joblib.load(EXAMPLE / "models" / f"{model_name}.joblib")
        value = np.asarray(pipeline.predict(sample.to_numpy(dtype=float)), dtype=float).reshape(-1)
        assert value.shape == (1,) and np.isfinite(value).all()


def test_predictor_enforces_minimum_missing_duplicate_and_numeric_contract(tmp_path: Path):
    valid = pd.DataFrame([[10, np.nan, 5, 20, np.nan, 18]], columns=models.FEATURES)
    validated, numeric = predictor.validate_input(valid)
    assert len(validated) == 1 and numeric.isna().sum().sum() == 2
    with pytest.raises(ValueError, match="At least three"):
        predictor.validate_input(pd.DataFrame([[10, np.nan, np.nan, np.nan, np.nan, 18]], columns=models.FEATURES))
    bad = valid.astype(object)
    bad.loc[0, "CP"] = "not-a-number"
    with pytest.raises(ValueError, match="Invalid nonnumeric"):
        predictor.validate_input(bad)
    duplicate = pd.DataFrame([[1] * 7], columns=[*models.FEATURES, "CP"])
    with pytest.raises(ValueError, match="Duplicate"):
        predictor.validate_input(duplicate)
    source = tmp_path / "input.csv"
    output = tmp_path / "output.csv"
    valid.to_csv(source, index=False)
    result = predictor.predict_file(
        EXAMPLE / "models" / "ridge_regression.joblib", source, output
    )
    assert output.is_file() and result.loc[0, "n_missing_features"] == 2


def test_read_only_sqlite_access_does_not_create_wal(tmp_path: Path):
    database = tmp_path / "mini.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE values_table (value INTEGER)")
        connection.execute("INSERT INTO values_table VALUES (7)")
    sql = tmp_path / "read.sql"
    sql.write_text("SELECT value FROM values_table", encoding="utf-8")
    before = (database.stat().st_size, database.stat().st_mtime_ns)
    first = models.read_candidates(database, sql)
    second = models.read_candidates(database, sql)
    pd.testing.assert_frame_equal(first, second)
    assert before == (database.stat().st_size, database.stat().st_mtime_ns)
    assert not database.with_name(database.name + "-wal").exists()


def test_random_forest_seed_is_repeatable_with_missing_values():
    x = np.array([[1, np.nan, 2, 3, 4, 5], [2, 2, 3, 4, 5, 6], [3, 3, 4, 5, 6, 7]] * 4)
    y = np.arange(len(x), dtype=float)
    params = {"max_depth": 2, "min_samples_leaf": 1, "max_features": "sqrt"}
    first = models.build_estimator("random_forest", params).fit(x, y).predict(x)
    second = models.build_estimator("random_forest", params).fit(x, y).predict(x)
    assert np.array_equal(first, second)
