"""Predict pig ingredient ME from a validated CSV and a trusted saved pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

FEATURES = ["CP", "EE", "Ash", "NDF", "ADF", "GE"]


def validate_input(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if frame.columns.duplicated().any():
        duplicated = frame.columns[frame.columns.duplicated()].tolist()
        raise ValueError(f"Duplicate input columns: {duplicated}")
    missing_columns = [column for column in FEATURES if column not in frame.columns]
    if missing_columns:
        raise ValueError(f"Missing required feature columns: {missing_columns}")
    raw = frame[FEATURES].copy()
    numeric = raw.apply(pd.to_numeric, errors="coerce")
    invalid = raw.notna() & raw.astype(str).apply(lambda column: column.str.strip().ne("")) & numeric.isna()
    if invalid.any().any():
        positions = [
            f"row {int(row) + 2}, column {column}"
            for row, column in zip(*np.where(invalid.to_numpy()))
        ]
        raise ValueError("Invalid nonnumeric values at " + "; ".join(positions))
    if np.isinf(numeric.to_numpy(dtype=float)).any():
        raise ValueError("Infinite predictor values are not allowed")
    nonmissing = numeric.notna().sum(axis=1)
    bad_rows = [int(index) + 2 for index in numeric.index[nonmissing.lt(3)]]
    if bad_rows:
        raise ValueError(
            "At least three of CP, EE, Ash, NDF, ADF, and GE must be present; "
            f"failed CSV rows: {bad_rows}"
        )
    return frame.copy(), numeric


def predict_file(model_path: Path, input_path: Path, output_path: Path) -> pd.DataFrame:
    """Load a trusted joblib pipeline, validate CSV values, and write predictions."""
    original = pd.read_csv(input_path, keep_default_na=True)
    output, numeric = validate_input(original)
    model = joblib.load(model_path)
    predictions = np.asarray(model.predict(numeric[FEATURES].to_numpy(dtype=float))).reshape(-1)
    output["predicted_ME_MJ_per_kg_DM"] = predictions
    output["n_missing_features"] = numeric[FEATURES].isna().sum(axis=1).to_numpy()
    output["missing_features"] = numeric[FEATURES].apply(
        lambda row: ";".join(row.index[row.isna()].tolist()), axis=1
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(output_path, index=False, encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path, help="trusted .joblib pipeline")
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("output_csv", type=Path)
    parser.add_argument("--composition-unit", required=True, choices=["%"])
    parser.add_argument("--ge-unit", required=True, choices=["MJ/kg"])
    parser.add_argument("--target-unit", required=True, choices=["MJ/kg"])
    parser.add_argument("--basis", required=True, choices=["dry matter"])
    args = parser.parse_args()
    print(
        "Security note: joblib loading can execute code; use only a trusted local model artifact."
    )
    result = predict_file(args.model, args.input_csv, args.output_csv)
    print(f"Wrote {len(result)} predictions to {args.output_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
