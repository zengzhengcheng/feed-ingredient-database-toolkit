from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from feeddb.database import connect, initialize, seed_dictionaries
from feeddb.contribution_batch import ContributionBatch, IngredientGridRow, MetricSpec, validate_contribution_batch
from feeddb.unit_resolution import UnitResolver


ROOT = Path(__file__).resolve().parents[1]
FORMAL_DB_CANDIDATES = (
    ROOT.parent / "05_ScienceDB_upload_candidate_20260814" / "database" / "feed_ingredient_release.sqlite",
    ROOT.parents[2] / "05_ScienceDB_upload_candidate_20260814" / "database" / "feed_ingredient_release.sqlite",
)


def test_new_blank_energy_default_is_mj_without_changing_value():
    resolver = UnitResolver()
    reported_value = 250
    result = resolver.resolve("Novel energy", "energy", "Novel energy", reported_value=reported_value)
    assert (result.raw_unit, result.canonical_unit, result.resolution_method) == ("", "MJ/kg", "context_default")
    assert reported_value == 250
    assert "editable MJ/kg" in result.warning


def test_explicit_and_embedded_old_energy_units_are_preserved():
    resolver = UnitResolver()
    explicit_kj = resolver.resolve("ME", "energy", "ME", explicit_unit="kJ/kg", reported_value=13.6)
    explicit_kcal = resolver.resolve("ME", "energy", "ME", explicit_unit="kcal/kg", reported_value=13.6)
    embedded_kj = resolver.resolve("ME，kJ/kg", "energy", "ME", reported_value=13.6)
    assert (explicit_kj.canonical_unit, explicit_kj.resolution_method) == ("kJ/kg", "explicit_field")
    assert (explicit_kcal.canonical_unit, explicit_kcal.resolution_method) == ("kcal/kg", "explicit_field")
    assert (embedded_kj.canonical_unit, embedded_kj.resolution_method) == ("kJ/kg", "embedded_in_metric")


def test_metric_rules_precede_new_context_default_and_digestibility_ge_is_percent():
    resolver = UnitResolver()
    me = resolver.resolve("ME", "energy", "ME")
    aid_ge = resolver.resolve("Gross energy", "AID", "GE")
    attd_ge = resolver.resolve("GE", "ATTD", "GE")
    assert (me.canonical_unit, me.resolution_method) == ("MJ/kg", "approved_metric_rule")
    assert (aid_ge.canonical_unit, aid_ge.resolution_method) == ("%", "approved_metric_rule")
    assert (attd_ge.canonical_unit, attd_ge.resolution_method) == ("%", "approved_metric_rule")


def test_fresh_metric_catalog_uses_mj_for_energy(tmp_path: Path):
    database = tmp_path / "candidate.sqlite"
    initialize(database)
    with connect(database) as conn:
        seed_dictionaries(conn)
        units = {row[0] for row in conn.execute("SELECT default_unit FROM metric_catalog WHERE context='energy'")}
    assert units == {"MJ/kg"}


def test_json_schema_and_example_use_paired_mj_value():
    schema = json.loads((ROOT / "integrations" / "contribution.schema.json").read_text(encoding="utf-8"))
    example = json.loads((ROOT / "integrations" / "ai_contribution_example.json").read_text(encoding="utf-8"))
    conditional_default = schema["$defs"]["nutritionMetric"]["allOf"][0]["then"]["properties"]["unit"]["default"]
    assert conditional_default == "MJ/kg"
    assert example["nutrition_metrics"][0]["unit"] == "MJ/kg"
    assert example["nutrition_rows"][0]["values"][0] == 13.5
    assert example["nutrition_metrics"][1] == {
        "metric": "Lysine", "context": "SID", "unit": "%", "basis": "dry matter"
    }
    assert example["nutrition_rows"][0]["values"][1] == 94
    assert "94 means 94%" in schema["$defs"]["nutritionRow"]["properties"]["values"]["description"]


def test_formal_release_energy_context_is_1274_mj_per_kg_and_is_read_only():
    formal_db = next((path for path in FORMAL_DB_CANDIDATES if path.is_file()), None)
    if formal_db is None:
        pytest.skip("optional formal ScienceDB SQLite is not distributed in the software repository")
    with sqlite3.connect(f"file:{formal_db.as_posix()}?mode=ro", uri=True) as conn:
        total, non_mj = conn.execute(
            """
            SELECT count(*),sum(CASE WHEN canonical_unit!='MJ/kg' THEN 1 ELSE 0 END)
            FROM nutrition_observations WHERE measurement_context='energy'
            """
        ).fetchone()
    assert (total, non_mj) == (1274, 0)


def test_composition_ge_mj_scale_conflict_is_visible_to_reviewer(tmp_path: Path):
    batch = ContributionBatch(
        title="GE scale review",
        composition_metrics=[MetricSpec("GE", "composition", "MJ/kg")],
        ingredients=[IngredientGridRow("I1", "corn", values=["4280"])],
    )
    check = validate_contribution_batch(tmp_path / "ge_scale.sqlite", batch)
    assert any("MJ/kg能量值高于常见范围" in warning for warning in check.warnings)
