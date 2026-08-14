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
EXAMPLE = ROOT / "examples" / "net_energy_prediction"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


models = load_module("net_energy_run_models", EXAMPLE / "run_models.py")
predictor = load_module("net_energy_predict", EXAMPLE / "predict.py")


def test_published_cohort_has_expected_counts_features_and_units():
    cohort = pd.read_csv(EXAMPLE / "cohort" / "pig_ne_modeling_cohort.csv")
    counts = models.validate_cohort(cohort)
    assert counts == {
        "observations": 48, "ingredient_samples": 46, "studies": 7,
        "duplicate_target_ids": 0,
    }
    assert not cohort[[*models.FEATURES, "NE"]].isna().any().any()


def test_nested_audit_has_no_outer_or_inner_study_leakage_and_complete_grids():
    audit = json.loads((EXAMPLE / "results" / "grouped_cv_metrics.json").read_text(encoding="utf-8"))
    assert len(audit["outer_folds"]) == 7
    expected_candidates = {
        "ridge_regression": 6, "partial_least_squares": 6, "random_forest": 18,
    }
    for fold in audit["outer_folds"]:
        held = fold["held_out_study_id"]
        training = set(fold["training_study_ids"])
        assert held not in training
        for model_name, count in expected_candidates.items():
            detail = fold["models"][model_name]
            assert set(detail["inner_study_ids"]) == training
            assert held not in detail["inner_study_ids"]
            assert len(detail["inner_candidates"]) == count
            for candidate in detail["inner_candidates"]:
                assert {item["held_out_study_id"] for item in candidate["inner_fold_mae"]} == training


def test_scaler_is_fitted_on_training_partition_not_full_data():
    x = np.array([
        [0, 0, 0, 0, 0, 0],
        [2, 2, 2, 2, 2, 2],
        [100, 100, 100, 100, 100, 100],
    ], dtype=float)
    y = np.array([1.0, 2.0, 3.0])
    estimator = models.build_estimator("ordinary_linear_regression")
    estimator.fit(x[:2], y[:2])
    assert np.allclose(estimator.named_steps["scaler"].mean_, np.mean(x[:2], axis=0))
    assert not np.allclose(estimator.named_steps["scaler"].mean_, np.mean(x, axis=0))


def test_oof_predictions_cover_every_target_once_for_every_model():
    cohort = pd.read_csv(EXAMPLE / "cohort" / "pig_ne_modeling_cohort.csv")
    predictions = pd.read_csv(EXAMPLE / "results" / "out_of_fold_predictions.csv")
    assert len(predictions) == 48 * 5
    assert set(predictions["model"]) == set(models.MODEL_ORDER)
    for _, frame in predictions.groupby("model"):
        assert len(frame) == 48
        assert frame["nutrition_observation_id"].is_unique
        assert set(frame["nutrition_observation_id"]) == set(cohort["nutrition_observation_id"])
        assert (frame["study_id"] == frame["held_out_study_id"]).all()


def test_metrics_recompute_exactly_from_row_level_oof_predictions():
    predictions = pd.read_csv(EXAMPLE / "results" / "out_of_fold_predictions.csv")
    expected_comparison = pd.read_csv(EXAMPLE / "results" / "model_comparison.csv")
    expected_per_study = pd.read_csv(EXAMPLE / "results" / "per_study_metrics.csv")
    comparison, per_study = models.calculate_metrics(predictions)
    pd.testing.assert_frame_equal(comparison, expected_comparison, check_exact=False, rtol=1e-12)
    pd.testing.assert_frame_equal(per_study, expected_per_study, check_exact=False, rtol=1e-12)


def test_saved_models_share_the_fixed_input_contract_and_predict():
    metadata = json.loads((EXAMPLE / "models" / "model_metadata.json").read_text(encoding="utf-8"))
    assert metadata["feature_order"] == models.FEATURES
    sample = pd.read_csv(EXAMPLE / "cohort" / "pig_ne_modeling_cohort.csv").iloc[[0]]
    for model_name in models.MODEL_ORDER[1:]:
        pipeline = predictor.joblib.load(EXAMPLE / "models" / f"{model_name}.joblib")
        values = np.asarray(
            pipeline.predict(sample[models.FEATURES].to_numpy(dtype=float)), dtype=float
        ).reshape(-1)
        assert values.shape == (1,)
        assert np.isfinite(values).all()


def test_predictor_validates_units_missing_duplicate_and_nonnumeric_inputs(tmp_path: Path):
    sample = pd.read_csv(EXAMPLE / "cohort" / "pig_ne_modeling_cohort.csv")[
        models.FEATURES
    ].iloc[[0]]
    valid = tmp_path / "valid.csv"
    sample.to_csv(valid, index=False)
    output = tmp_path / "prediction.csv"
    result = predictor.predict_csv(
        EXAMPLE / "models" / "partial_least_squares.joblib",
        EXAMPLE / "models" / "model_metadata.json",
        valid, output, "%", "MJ/kg", "dry matter",
    )
    assert len(result) == 1 and output.is_file()
    with pytest.raises(ValueError, match="declared as %"):
        predictor.predict_csv(
            EXAMPLE / "models" / "partial_least_squares.joblib",
            EXAMPLE / "models" / "model_metadata.json",
            valid, output, "g/kg", "MJ/kg", "dry matter",
        )
    missing = tmp_path / "missing.csv"
    sample.drop(columns=["ADF"]).to_csv(missing, index=False)
    with pytest.raises(ValueError, match="missing feature"):
        predictor.predict_csv(
            EXAMPLE / "models" / "partial_least_squares.joblib",
            EXAMPLE / "models" / "model_metadata.json",
            missing, output, "%", "MJ/kg", "dry matter",
        )
    duplicate = tmp_path / "duplicate.csv"
    duplicate.write_text("CP,EE,Ash,NDF,ADF,GE,CP\n1,2,3,4,5,6,7\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate columns"):
        predictor.validate_header(duplicate)
    nonnumeric = tmp_path / "nonnumeric.csv"
    bad = sample.astype(object)
    bad.loc[bad.index[0], "CP"] = "not-a-number"
    bad.to_csv(nonnumeric, index=False)
    with pytest.raises(ValueError, match="finite numeric"):
        predictor.predict_csv(
            EXAMPLE / "models" / "partial_least_squares.joblib",
            EXAMPLE / "models" / "model_metadata.json",
            nonnumeric, output, "%", "MJ/kg", "dry matter",
        )


def test_fixed_random_seed_repeats_random_forest_predictions():
    rng = np.random.default_rng(7)
    x = rng.normal(size=(20, 6))
    y = x[:, 0] - 0.5 * x[:, 1]
    params = {"max_depth": 2, "min_samples_leaf": 2, "max_features": "sqrt"}
    first = models.build_estimator("random_forest", params).fit(x, y).predict(x)
    second = models.build_estimator("random_forest", params).fit(x, y).predict(x)
    assert np.array_equal(first, second)


def test_read_cohort_uses_repeatable_read_only_access(tmp_path: Path):
    database = tmp_path / "mini.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE values_table (value INTEGER)")
        connection.execute("INSERT INTO values_table VALUES (7)")
    sql = tmp_path / "read.sql"
    sql.write_text("SELECT value FROM values_table", encoding="utf-8")
    before = (database.stat().st_size, database.stat().st_mtime_ns)
    first = models.read_cohort(database, sql)
    second = models.read_cohort(database, sql)
    after = (database.stat().st_size, database.stat().st_mtime_ns)
    pd.testing.assert_frame_equal(first, second)
    assert first.to_dict(orient="records") == [{"value": 7}]
    assert before == after
    assert not database.with_name(database.name + "-wal").exists()
