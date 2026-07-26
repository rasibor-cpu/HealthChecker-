"""Configurable clinical rules engine — observational flags only, never diagnoses."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.health_vault.models import Measurement

_DEFAULT_PATH = Path(__file__).resolve().parent / "config" / "clinical_rules.json"

FLAG_NORMAL = "Normal"
FLAG_BORDERLINE = "Borderline"
FLAG_ABNORMAL = "Abnormal"
FLAG_CRITICAL = "Critical"
FLAG_UNKNOWN = "Unknown"


class ClinicalRulesEngine:
    """
    HC-201 absolute/flag classifier.

    HC-301 expanded multi-condition rules live in
    `backend.health_vault.guardian.rule_engine.ExpandedClinicalRulesEngine`,
    which wraps this engine. Thresholds remain configurable JSON — not diagnoses.
    """

    def __init__(self, rules: dict[str, Any] | None = None, path: Path | None = None) -> None:
        if rules is not None:
            self.rules = rules
        else:
            p = path or _DEFAULT_PATH
            self.rules = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"metrics": {}}

    def reload(self, path: Path | None = None) -> None:
        p = path or _DEFAULT_PATH
        self.rules = json.loads(p.read_text(encoding="utf-8"))

    def classify(self, measurement: Measurement | dict[str, Any]) -> str:
        if isinstance(measurement, Measurement):
            metric = measurement.metric
            value = measurement.value
            units = measurement.units
        else:
            metric = str(measurement.get("metric") or "")
            value = measurement.get("value")
            units = measurement.get("units")

        # Missing / non-numeric values are never Normal
        if value is None or value == "":
            return FLAG_UNKNOWN

        spec = (self.rules.get("metrics") or {}).get(metric)
        if not spec:
            return FLAG_UNKNOWN
        try:
            num = float(value)
        except (TypeError, ValueError):
            return FLAG_UNKNOWN

        allowed_units = spec.get("units") or []
        if units and allowed_units and units not in allowed_units:
            # Unit mismatch → unknown rather than inventing conversion
            return FLAG_UNKNOWN

        if spec.get("impossible_below") is not None and num < float(spec["impossible_below"]):
            return FLAG_UNKNOWN
        if spec.get("impossible_above") is not None and num > float(spec["impossible_above"]):
            return FLAG_UNKNOWN
        if spec.get("critical_above") is not None and num >= float(spec["critical_above"]):
            return FLAG_CRITICAL
        if spec.get("critical_below") is not None and num <= float(spec["critical_below"]):
            return FLAG_CRITICAL

        for flag, key in (
            (FLAG_NORMAL, "normal"),
            (FLAG_BORDERLINE, "borderline"),
            (FLAG_ABNORMAL, "abnormal"),
        ):
            rng = spec.get(key)
            if isinstance(rng, (list, tuple)) and len(rng) == 2:
                lo, hi = float(rng[0]), float(rng[1])
                if lo <= num <= hi:
                    return flag
        return FLAG_UNKNOWN

    def apply(self, measurements: list[Any]) -> list[Any]:
        out = []
        for m in measurements:
            flag = self.classify(m)
            if isinstance(m, Measurement):
                m.abnormal_flag = flag
                out.append(m)
            elif isinstance(m, dict):
                m = dict(m)
                m["abnormal_flag"] = flag
                out.append(m)
            else:
                out.append(m)
        return out

    def evaluate_expanded(
        self,
        store: Any,
        *,
        patient_id: str = "default-patient",
        context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """HC-301 bridge: expanded guardian rules (authoritative Python path)."""
        from backend.health_vault.guardian.rule_engine import ExpandedClinicalRulesEngine

        return ExpandedClinicalRulesEngine(store, clinical=self).evaluate(
            patient_id=patient_id,
            context=context,
        )
