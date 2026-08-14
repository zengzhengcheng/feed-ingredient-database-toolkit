from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class UnitResolution:
    raw_metric_label: str
    parsed_metric: str
    raw_unit: str
    canonical_unit: str
    resolution_method: str
    rule_reference: str
    status: str
    warning: str = ""


@dataclass(frozen=True)
class MetricUnitRule:
    context: str
    canonical_metric: str
    allowed_unit: str
    is_default_when_omitted: bool
    status: str
    reference: str


def _key(value: object) -> str:
    return "" if value is None else str(value).strip().casefold()


class UnitResolver:
    """Resolve unit spelling and omissions without changing reported values."""

    def __init__(self, data_directory: str | Path | None = None):
        data = Path(data_directory) if data_directory else Path(__file__).resolve().parent / "data"
        self.aliases: dict[str, str] = {}
        with (data / "unit_aliases.csv").open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                if row.get("status", "approved") == "approved":
                    self.aliases[_key(row["raw_unit"])] = row["canonical_unit"].strip()
        self.rules: dict[tuple[str, str], list[MetricUnitRule]] = {}
        with (data / "metric_unit_rules.csv").open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                reference = ":".join(filter(None, (
                    row.get("source_kind", ""), row.get("source_table", ""), row.get("source_id", "")
                ))) or "metric_unit_rules.csv"
                rule = MetricUnitRule(
                    row["context"].strip(), row["canonical_metric"].strip(), row["allowed_unit"].strip(),
                    row.get("is_default_when_omitted", "").strip() in {"1", "true", "True"},
                    row.get("status", "approved").strip(), reference,
                )
                self.rules.setdefault((_key(rule.context), _key(rule.canonical_metric)), []).append(rule)
        self.option_profiles: dict[tuple[str, str], list[str]] = {}
        option_path = data / "unit_option_profiles.csv"
        if option_path.exists():
            with option_path.open(encoding="utf-8-sig", newline="") as stream:
                rows = sorted(csv.DictReader(stream), key=lambda row: int(row.get("priority", "999") or 999))
                for row in rows:
                    if row.get("status", "approved").strip() != "approved":
                        continue
                    key = (_key(row.get("profile_type", "")), _key(row.get("profile_value", "")))
                    unit = row.get("canonical_unit", "").strip()
                    if unit and unit not in self.option_profiles.setdefault(key, []):
                        self.option_profiles[key].append(unit)
        units = sorted(self.aliases, key=len, reverse=True)
        alternatives = "|".join(re.escape(unit) for unit in units)
        self._embedded = re.compile(
            rf"^(?P<metric>.*?)\s*(?:[（(\[]\s*|[,，;；]\s*|\s+)(?P<unit>{alternatives})\s*[）)\]]?\s*$",
            re.IGNORECASE,
        )
        self._comma_suffix = re.compile(
            r"^(?P<metric>.+?)[,，]\s*(?P<unit>[^,，]+?)\s*$",
            re.IGNORECASE,
        )

    def canonicalize_unit(self, raw_unit: object) -> tuple[str, str]:
        raw = "" if raw_unit is None else str(raw_unit).strip()
        canonical = self.aliases.get(_key(raw))
        return (canonical, "approved") if canonical else (raw, "candidate")

    @property
    def available_units(self) -> list[str]:
        """Return every approved canonical unit known to the importer."""
        preferred = ["%", "g/kg", "mg/kg", "µg/kg", "MJ/kg", "kJ/kg", "kcal/kg", "Mcal/kg"]
        known = set(self.aliases.values())
        return [unit for unit in preferred if unit in known] + sorted(known.difference(preferred), key=str.casefold)

    def possible_units(
        self,
        context: object,
        canonical_metric: object = "",
        group_name: object = "",
        default_unit: object = "",
    ) -> list[str]:
        """Rank likely units first, then retain the complete approved unit vocabulary."""
        ordered: list[str] = []

        def add(values):
            for value in values:
                unit = "" if value is None else str(value).strip()
                if unit and unit not in ordered:
                    ordered.append(unit)

        add([default_unit])
        add(
            rule.allowed_unit
            for rule in self.rules.get((_key(context), _key(canonical_metric)), [])
            if rule.status == "approved"
        )
        add(self.option_profiles.get(("metric", f"{_key(context)}|{_key(canonical_metric)}"), []))
        add(self.option_profiles.get(("group", _key(group_name)), []))
        add(self.option_profiles.get(("context", _key(context)), []))
        add(self.option_profiles.get(("global", "all"), []))
        add(self.available_units)
        return ordered

    @staticmethod
    def infer_energy_unit_from_value(reported_value: object) -> tuple[str, str]:
        """Infer an energy concentration unit only when the magnitude is distinctive."""
        try:
            number = float(reported_value)
        except (TypeError, ValueError):
            return "", ""
        if not math.isfinite(number) or number == 0:
            return "", ""
        magnitude = abs(number)
        if 0.1 <= magnitude <= 100:
            return "MJ/kg", "energy magnitude 0.1–100"
        if 500 <= magnitude < 8_000:
            return "kcal/kg", "energy magnitude 500–<8000"
        if 8_000 <= magnitude <= 100_000:
            return "kJ/kg", "energy magnitude 8000–100000"
        return "", ""

    def split_embedded_unit(self, raw_metric_label: object) -> tuple[str, str]:
        label = "" if raw_metric_label is None else str(raw_metric_label).strip()
        match = self._embedded.match(label)
        if match and match.group("metric").strip():
            return match.group("metric").rstrip(" ,，;；"), match.group("unit").strip()
        # A unit not yet in the dictionary can still be separated safely when it
        # is the last comma-delimited suffix and has unit-like syntax. Ordinary
        # descriptors such as "absorbance (532 nm)" are deliberately retained.
        suffix = self._comma_suffix.match(label)
        if suffix and self._looks_like_unit_suffix(suffix.group("unit")):
            return suffix.group("metric").rstrip(" ,，"), suffix.group("unit").strip()
        return label, ""

    def _looks_like_unit_suffix(self, value: object) -> bool:
        unit = "" if value is None else str(value).strip()
        folded = _key(unit).replace("％", "%")
        if folded in self.aliases:
            return True
        if not unit or len(unit) > 40 or re.search(r"[。；;]", unit):
            return False
        return bool(
            "%" in folded
            or "/" in folded
            or re.search(r"\b(?:per|ppm|ppb|iu|cfu|ftu|tiu|meq|mol)\b", folded)
            or re.search(r"(?:kg|g|mg|µg|μg|ug|kj|mj|kcal|mcal|mol)\s*[·.]?\s*[-−]\s*1\b", folded)
        )

    def resolve(
        self,
        raw_metric_label: object,
        context: object,
        canonical_metric: object,
        explicit_unit: object = "",
        metric_catalog_default: object = "",
        reported_value: object = None,
    ) -> UnitResolution:
        label = "" if raw_metric_label is None else str(raw_metric_label).strip()
        parsed_metric, embedded_unit = self.split_embedded_unit(label)
        explicit = "" if explicit_unit is None else str(explicit_unit).strip()
        if explicit:
            unit, status = self.canonicalize_unit(explicit)
            return UnitResolution(label, parsed_metric, explicit, unit, "explicit_field", "source unit field", status)
        if embedded_unit:
            unit, status = self.canonicalize_unit(embedded_unit)
            return UnitResolution(label, parsed_metric, embedded_unit, unit, "embedded_in_metric", "source metric label", status)

        metric_rules = self.rules.get((_key(context), _key(canonical_metric)), [])
        rules = [r for r in metric_rules if r.status == "approved"]
        defaults = [r for r in rules if r.is_default_when_omitted]
        if len(defaults) == 1:
            rule = defaults[0]
            return UnitResolution(label, parsed_metric, "", rule.allowed_unit, "approved_metric_rule", rule.reference, "approved")
        if len(defaults) > 1 or (len(rules) > 1 and not defaults):
            return UnitResolution(
                label, parsed_metric, "", "", "unresolved", "metric_unit_rules.csv", "candidate",
                "Multiple allowed units exist without one reliable default.",
            )
        if _key(context) == "energy":
            inferred_unit, magnitude_rule = self.infer_energy_unit_from_value(reported_value)
            if inferred_unit:
                return UnitResolution(
                    label, parsed_metric, "", inferred_unit, "value_magnitude_rule",
                    f"value_rule:{magnitude_rule}", "approved",
                    "Unit inferred from a distinctive energy-value magnitude; reported value was not changed.",
                )
        review_rules = [r for r in metric_rules if r.status == "review_required"]
        if review_rules:
            rule = review_rules[0]
            return UnitResolution(
                label, parsed_metric, "", "", "unresolved", rule.reference, "candidate",
                "This metric has an explicit review rule; a generic context default must not be guessed.",
            )
        catalog = "" if metric_catalog_default is None else str(metric_catalog_default).strip()
        if catalog:
            unit, status = self.canonicalize_unit(catalog)
            return UnitResolution(label, parsed_metric, "", unit, "metric_catalog_default", "metric_catalog.default_unit", status)
        context_default = {
            "energy": "kJ/kg",
            "composition": "%",
            "aid": "%",
            "sid": "%",
            "attd": "%",
            "sttd": "%",
            "cattd": "%",
        }.get(_key(context), "")
        if context_default:
            warning = ""
            if _key(context) == "energy" and reported_value is not None:
                warning = "Energy magnitude was not distinctive; the editable kJ/kg context default was assigned."
            return UnitResolution(
                label, parsed_metric, "", context_default, "context_default",
                f"context:{_key(context)}", "approved", warning,
            )
        return UnitResolution(
            label, parsed_metric, "", "", "unresolved", "", "candidate",
            "No explicit unit or approved default rule was available.",
        )


DEFAULT_UNIT_RESOLVER = UnitResolver()
