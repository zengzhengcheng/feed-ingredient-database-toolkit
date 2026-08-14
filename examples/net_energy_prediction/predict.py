"""Apply a trusted saved pig net-energy demonstration model to a CSV file."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

FEATURES = ["CP", "EE", "Ash", "NDF", "ADF", "GE"]


def validate_header(path: Path) -> None:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        header = next(csv.reader(stream), None)
    if header is None:
        raise ValueError("Input CSV is empty")
    duplicates = sorted({name for name in header if header.count(name) > 1})
    if duplicates:
        raise ValueError(f"Input CSV has duplicate columns: {duplicates}")


def predict_csv(
    model_path: Path,
    metadata_path: Path,
    input_path: Path,
    output_path: Path,
    composition_unit: str,
    ge_unit: str,
    basis: str,
) -> pd.DataFrame:
    validate_header(input_path)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata["feature_order"] != FEATURES:
        raise ValueError(f"Unexpected model feature order: {metadata['feature_order']}")
    if composition_unit != "%":
        raise ValueError("CP, EE, Ash, NDF, and ADF must be declared as %")
    if ge_unit != "MJ/kg":
        raise ValueError("GE must be declared as MJ/kg")
    if basis.casefold() != "dry matter":
        raise ValueError("All six input features must be declared on a dry matter basis")
    frame = pd.read_csv(input_path)
    missing = [column for column in FEATURES if column not in frame.columns]
    if missing:
        raise ValueError(f"Input CSV is missing feature columns: {missing}")
    numeric = frame[FEATURES].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any() or not np.isfinite(numeric.to_numpy()).all():
        invalid = [column for column in FEATURES if numeric[column].isna().any()]
        raise ValueError(f"Feature columns must contain finite numeric values; invalid: {invalid}")
    # joblib/pickle loading is only safe for trusted artifacts.
    model = joblib.load(model_path)
    result = frame.copy()
    result["predicted_NE_MJ_per_kg_DM"] = np.asarray(
        model.predict(numeric[FEATURES].to_numpy(dtype=float)), dtype=float
    ).reshape(-1)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path, index=False, encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--model", type=Path, required=True, help="trusted .joblib pipeline")
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument(
        "--composition-unit", required=True, choices=["%"],
        help="unit declaration for CP/EE/Ash/NDF/ADF",
    )
    parser.add_argument(
        "--ge-unit", required=True, choices=["MJ/kg"], help="GE unit declaration"
    )
    parser.add_argument(
        "--basis", required=True, choices=["dry matter"],
        help="reporting-basis declaration",
    )
    args = parser.parse_args()
    result = predict_csv(
        args.model, args.metadata, args.input, args.output,
        args.composition_unit, args.ge_unit, args.basis,
    )
    print(f"Wrote {len(result)} predictions to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
