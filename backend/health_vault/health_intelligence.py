"""Executive Health Intelligence — observational only, never diagnoses."""

from __future__ import annotations

import logging
import math
from typing import Any
from uuid import uuid4

from backend.health_vault.metric_normalization import canonicalize_metric
from backend.health_vault.models import (
    ConfidenceScore,
    EvidenceReference,
    HealthObservation,
    utc_now,
)
from backend.health_vault.trend_engine import HIGHER_BETTER, LOWER_BETTER, TrendEngine
from backend.health_vault.vault_store import VaultStore

logger = logging.getLogger("hc315.health_intelligence")

# Metric → human observational phrasing
_LABELS = {
    "egfr": "Kidney function (eGFR)",
    "creatinine": "Creatinine",
    "creatinine_serum": "Serum creatinine",
    "creatinine_urine": "Urine creatinine",
    "glucose": "Glucose",
    "glucose_fasting": "Fasting glucose",
    "glucose_random": "Random glucose",
    "glucose_postprandial": "Postprandial glucose",
    "glucose_cgm_interstitial": "CGM interstitial glucose",
    "glucose_capillary": "Capillary glucose",
    "glucose_serum_plasma": "Serum/plasma glucose",
    "hba1c": "HbA1c",
    "systolic": "Systolic blood pressure",
    "diastolic": "Diastolic blood pressure",
    "sleep_score": "Sleep score",
    "sleep_duration": "Sleep duration",
    "resting_hr": "Resting heart rate",
    "pulse": "Pulse rate",
    "hrv": "HRV",
    "heart_rhythm": "Heart rhythm",
    "weight": "Weight",
    "steps": "Step count",
    "uacr": "UACR (kidney protein)",
    "protein": "Proteinuria",
    "ldl": "LDL Cholesterol",
    "hdl": "HDL Cholesterol",
    "cholesterol": "Total Cholesterol",
    "triglycerides": "Triglycerides",
}

FORBIDDEN_DIAGNOSES = {
    "diabetes", "hypertension", "chronic kidney disease", "ckd",
    "kidney disease", "renal failure", "nephropathy", "neuropathy",
    "cardiovascular disease", "diabetic", "hypertensive"
}
FORBIDDEN_MEDICATIONS = {
    "insulin", "metformin", "lisinopril", "losartan", "atorvastatin",
    "statin", "prescribe", "medication recommendation"
}


def _validate_safety_boundaries(obs: HealthObservation) -> None:
    """Enforces safety rules: no diagnoses, no medication recommendation, no clinical advice."""
    for text in [obs.fact, obs.interpretation, obs.explanation or ""]:
        lower_text = text.lower()
        # Check diagnoses
        for d in FORBIDDEN_DIAGNOSES:
            if d in lower_text:
                raise ValueError(
                    f"Safety Boundary Violation: Forbidden diagnosis term '{d}' detected in observation."
                )
        # Check medications
        for m in FORBIDDEN_MEDICATIONS:
            if m in lower_text:
                raise ValueError(
                    f"Safety Boundary Violation: Forbidden medication term '{m}' detected in observation."
                )


class HealthIntelligenceEngine:
    """Generate observational intelligence statements from vault trends."""

    def __init__(self, store: VaultStore, trends: TrendEngine | None = None) -> None:
        self.store = store
        self.trends = trends or TrendEngine(store)

    def get_patient_observations(self, patient_id: str) -> list[dict[str, Any]]:
        """Retrieve historical observations for a specific patient."""
        data = self.store._read_index()
        hi = data.get("health_intelligence") or {}
        obs = hi.get("observations") or []
        return [o for o in obs if o.get("patient_id") == patient_id]

    def _get_metric_series(self, metric: str, patient_id: str) -> list[dict[str, Any]]:
        """Fetch all eligible measurement objects for a metric and patient, sorted chronologically."""
        canon = canonicalize_metric(metric)
        docs = self.trends._docs_by_id()
        items = []
        for m in self.store.list_measurements():
            if canonicalize_metric(m.get("metric")) == canon and self.trends._eligible(m, docs):
                doc = docs.get(str(m.get("document_id") or ""))
                if doc and doc.get("patient_id", "default-patient") == patient_id:
                    items.append(m)
        
        # Sort chronologically based on measured_at
        items.sort(
            key=lambda x: str(
                x.get("measured_at")
                or (docs.get(str(x.get("document_id") or ""), {}) or {}).get("measured_at")
                or ""
            )
        )
        return items

    def generate_observations(self, patient_id: str = "default-patient") -> list[dict[str, Any]]:
        """Generate and persist patient-specific observations using clinical trend metrics."""
        trend_map = self.trends.recompute(patient_id=patient_id)
        observations: list[dict[str, Any]] = []
        processed_metrics: set[str] = set()

        # Categories tracked for missing-data warnings
        has_glycemic = False
        has_renal = False
        has_cardiovascular = False

        # 1. DIABETES/GLYCEMIC ANALYSIS — like-for-like classes only
        from backend.health_vault.clinical_semantics import GLYCEMIC_TREND_CLASSES

        _GLUCOSE_LABEL = {
            "glucose_fasting": "fasting glucose",
            "glucose_random": "random glucose",
            "glucose_postprandial": "postprandial glucose",
            "glucose_cgm_interstitial": "CGM interstitial glucose",
            "glucose_capillary": "capillary glucose",
            "glucose_serum_plasma": "serum/plasma glucose",
            "glucose": "glucose (context unspecified)",
        }
        for klass in GLYCEMIC_TREND_CLASSES:
            glucose_items = self._get_metric_series(klass, patient_id)
            if not glucose_items:
                continue
            has_glycemic = True
            processed_metrics.add(klass)
            values = [float(item["value"]) for item in glucose_items]
            evidence = [
                EvidenceReference(
                    source_type="measurement",
                    document_id=item.get("document_id"),
                    measurement_id=item.get("measurement_id") or item.get("id"),
                ) for item in glucose_items
            ]
            mean_val = sum(values) / len(values)
            variance = sum((x - mean_val) ** 2 for x in values) / len(values)
            std_dev = math.sqrt(variance)
            label = _GLUCOSE_LABEL.get(klass, klass.replace("_", " "))
            units = glucose_items[-1].get("units") or "mg/dL"
            fact = f"Mean {label} is {mean_val:.1f} {units} with standard deviation of {std_dev:.1f} {units}."
            direction = trend_map.get(klass, {}).get("direction", "stable")
            if std_dev > 20:
                interpretation = f"{label} shows high variability ({direction} trend)."
            else:
                interpretation = f"{label} is stable with normal variability ({direction} trend)."
            explanation = (
                f"Calculated {label} standard deviation over {len(values)} measurements. "
                "This series is not merged with other glucose specimen or timing classes."
            )
            obs = HealthObservation(
                patient_id=patient_id,
                observation_id=str(uuid4()),
                category="glycemic",
                metric=klass,
                fact=fact,
                interpretation=interpretation,
                measured_at=utc_now(),
                confidence=ConfidenceScore(
                    value=0.9 if len(values) >= 3 else 0.6,
                    method="statistical_analysis",
                    version="1.2.0",
                ),
                evidence=evidence,
                explanation=explanation,
            )
            _validate_safety_boundaries(obs)
            observations.append(obs.to_dict())

        # HbA1c Check
        hba1c_items = self._get_metric_series("hba1c", patient_id)
        if hba1c_items:
            has_glycemic = True
            processed_metrics.add("hba1c")
            values = [float(item["value"]) for item in hba1c_items]
            evidence = [
                EvidenceReference(
                    source_type="measurement",
                    document_id=item.get("document_id"),
                    measurement_id=item.get("measurement_id") or item.get("id"),
                ) for item in hba1c_items
            ]
            direction = trend_map.get("hba1c", {}).get("direction", "stable")
            fact = f"Latest HbA1c is {values[-1]:.1f}% (prior values: {', '.join(f'{v:.1f}%' for v in values[:-1])})."
            interpretation = f"HbA1c levels show a {direction} pattern."
            explanation = f"Evaluated chronological trend direction over {len(values)} points."
            
            obs = HealthObservation(
                patient_id=patient_id,
                observation_id=str(uuid4()),
                category="glycemic",
                metric="hba1c",
                fact=fact,
                interpretation=interpretation,
                measured_at=utc_now(),
                confidence=ConfidenceScore(
                    value=0.85 if len(values) >= 3 else 0.5,
                    method="rule_based",
                    version="1.2.0",
                ),
                evidence=evidence,
                explanation=explanation,
            )
            _validate_safety_boundaries(obs)
            observations.append(obs.to_dict())

        # 2. KIDNEY/RENAL ANALYSIS
        egfr_items = self._get_metric_series("egfr", patient_id)
        if egfr_items:
            has_renal = True
            processed_metrics.add("egfr")
            values = [float(item["value"]) for item in egfr_items]
            evidence = [
                EvidenceReference(
                    source_type="measurement",
                    document_id=item.get("document_id"),
                    measurement_id=item.get("measurement_id") or item.get("id"),
                ) for item in egfr_items
            ]
            direction = trend_map.get("egfr", {}).get("direction", "stable")
            change = values[-1] - values[0]
            fact = f"eGFR changed from {values[0]:.1f} to {values[-1]:.1f} mL/min/1.73m2."
            interpretation = f"Kidney filtration rate is stable ({direction} trend)."
            if direction == "worsening" or change < -5:
                interpretation = f"Kidney filtration rate shows worsening trend ({direction})."

            explanation = f"Calculated absolute change of {change:+.1f} mL/min/1.73m2 over {len(values)} readings."

            obs = HealthObservation(
                patient_id=patient_id,
                observation_id=str(uuid4()),
                category="renal",
                metric="egfr",
                fact=fact,
                interpretation=interpretation,
                measured_at=utc_now(),
                confidence=ConfidenceScore(
                    value=0.85 if len(values) >= 3 else 0.5,
                    method="statistical_analysis",
                    version="1.2.0",
                ),
                evidence=evidence,
                explanation=explanation,
            )
            _validate_safety_boundaries(obs)
            observations.append(obs.to_dict())

        # Creatinine & Proteinuria Check
        for metric, category in [
            ("creatinine_serum", "renal"),
            ("creatinine", "renal"),
            ("creatinine_urine", "renal"),
            ("uacr", "renal"),
            ("protein", "renal"),
            ("protein_urine", "renal"),
        ]:
            items = self._get_metric_series(metric, patient_id)
            if items:
                has_renal = True
                processed_metrics.add(metric)
                values = [float(item["value"]) for item in items]
                evidence = [EvidenceReference("measurement", item.get("document_id"), item.get("measurement_id") or item.get("id")) for item in items]
                direction = trend_map.get(metric, {}).get("direction", "stable")
                
                obs = HealthObservation(
                    patient_id=patient_id,
                    observation_id=str(uuid4()),
                    category=category,
                    metric=metric,
                    fact=f"Latest {metric} is {values[-1]} (prior values: {', '.join(map(str, values[:-1]))}).",
                    interpretation=f"Trends show a {direction} pattern.",
                    measured_at=utc_now(),
                    confidence=ConfidenceScore(0.85 if len(values) >= 3 else 0.5, "rule_based", "1.2.0"),
                    evidence=evidence,
                    explanation=f"Evaluated trend direction over {len(values)} points.",
                )
                _validate_safety_boundaries(obs)
                observations.append(obs.to_dict())

        # 3. CARDIOVASCULAR BP / PULSE
        systolic_items = self._get_metric_series("systolic", patient_id)
        diastolic_items = self._get_metric_series("diastolic", patient_id)
        if systolic_items and diastolic_items:
            has_cardiovascular = True
            processed_metrics.add("systolic")
            processed_metrics.add("diastolic")
            
            s_vals = [float(x["value"]) for x in systolic_items]
            d_vals = [float(x["value"]) for x in diastolic_items]
            
            evidence = []
            for item in (systolic_items + diastolic_items):
                ref = EvidenceReference("measurement", item.get("document_id"), item.get("measurement_id") or item.get("id"))
                if ref not in evidence:
                    evidence.append(ref)

            latest_s = s_vals[-1]
            latest_d = d_vals[-1]
            fact = f"Latest blood pressure is {latest_s:.0f}/{latest_d:.0f} mmHg."
            
            direction_s = trend_map.get("systolic", {}).get("direction", "stable")
            direction_d = trend_map.get("diastolic", {}).get("direction", "stable")
            
            if latest_s > 130 or latest_d > 80:
                interpretation = f"Blood pressure is elevated ({direction_s}/{direction_d} trend)."
            else:
                interpretation = f"Blood pressure is stable within normal limits ({direction_s}/{direction_d} trend)."
                
            obs = HealthObservation(
                patient_id=patient_id,
                observation_id=str(uuid4()),
                category="cardiovascular",
                metric="systolic",
                fact=fact,
                interpretation=interpretation,
                measured_at=utc_now(),
                confidence=ConfidenceScore(0.85 if len(s_vals) >= 3 else 0.5, "rule_based", "1.2.0"),
                evidence=evidence,
                explanation=f"Evaluated latest systolic/diastolic values against elevated blood pressure threshold (130/80 mmHg) over {len(s_vals)} readings.",
            )
            _validate_safety_boundaries(obs)
            observations.append(obs.to_dict())

        # Other cardiovascular metrics: pulse, lipid metrics
        for metric, category in [
            ("pulse", "cardiovascular"),
            ("resting_hr", "cardiovascular"),
            ("hrv", "cardiovascular"),
            ("ldl", "cardiovascular"),
            ("hdl", "cardiovascular"),
            ("cholesterol", "cardiovascular"),
            ("triglycerides", "cardiovascular")
        ]:
            items = self._get_metric_series(metric, patient_id)
            if items:
                has_cardiovascular = True
                processed_metrics.add(metric)
                values = [float(item["value"]) for item in items]
                evidence = [EvidenceReference("measurement", item.get("document_id"), item.get("measurement_id") or item.get("id")) for item in items]
                direction = trend_map.get(metric, {}).get("direction", "stable")
                
                obs = HealthObservation(
                    patient_id=patient_id,
                    observation_id=str(uuid4()),
                    category=category,
                    metric=metric,
                    fact=f"Latest {metric} is {values[-1]} (range: {min(values)}-{max(values)}).",
                    interpretation=f"Trends show a {direction} pattern.",
                    measured_at=utc_now(),
                    confidence=ConfidenceScore(0.85 if len(values) >= 3 else 0.5, "rule_based", "1.2.0"),
                    evidence=evidence,
                    explanation=f"Tracked clinical trend path for {metric} across {len(values)} readings.",
                )
                _validate_safety_boundaries(obs)
                observations.append(obs.to_dict())

        # 4. LIFESTYLE (WEIGHT / SLEEP / ACTIVITY)
        for metric, category in [
            ("weight", "general"),
            ("sleep_score", "sleep"),
            ("sleep_duration", "sleep"),
            ("steps", "general")
        ]:
            items = self._get_metric_series(metric, patient_id)
            if items:
                processed_metrics.add(metric)
                values = [float(item["value"]) for item in items]
                evidence = [EvidenceReference("measurement", item.get("document_id"), item.get("measurement_id") or item.get("id")) for item in items]
                direction = trend_map.get(metric, {}).get("direction", "stable")
                
                obs = HealthObservation(
                    patient_id=patient_id,
                    observation_id=str(uuid4()),
                    category=category,
                    metric=metric,
                    fact=f"Latest {metric} is {values[-1]} (prior average: {sum(values[:-1])/max(1, len(values)-1):.1f}).",
                    interpretation=f"Trend direction is {direction}.",
                    measured_at=utc_now(),
                    confidence=ConfidenceScore(0.85 if len(values) >= 3 else 0.5, "rule_based", "1.2.0"),
                    evidence=evidence,
                    explanation=f"Evaluated lifestyle trend direction for {metric} across {len(values)} points.",
                )
                _validate_safety_boundaries(obs)
                observations.append(obs.to_dict())

        # 5. MISSING-DATA WARNINGS
        # Generate warnings if core metrics are absent for a category.
        if not has_glycemic:
            obs = HealthObservation(
                patient_id=patient_id,
                observation_id=str(uuid4()),
                category="glycemic",
                metric=None,
                fact="No glycemic measurements found in the vault.",
                interpretation="Missing data warning",
                measured_at=utc_now(),
                confidence=ConfidenceScore(0.0, "rule_based", "1.2.0"),
                evidence=[],
                explanation="No glucose or HbA1c metrics exist in the vault for glycemic analysis.",
            )
            _validate_safety_boundaries(obs)
            observations.append(obs.to_dict())

        if not has_renal:
            obs = HealthObservation(
                patient_id=patient_id,
                observation_id=str(uuid4()),
                category="renal",
                metric=None,
                fact="No renal measurements found in the vault.",
                interpretation="Missing data warning",
                measured_at=utc_now(),
                confidence=ConfidenceScore(0.0, "rule_based", "1.2.0"),
                evidence=[],
                explanation="No eGFR or creatinine metrics exist in the vault for renal analysis.",
            )
            _validate_safety_boundaries(obs)
            observations.append(obs.to_dict())

        if not has_cardiovascular:
            obs = HealthObservation(
                patient_id=patient_id,
                observation_id=str(uuid4()),
                category="cardiovascular",
                metric=None,
                fact="No cardiovascular measurements found in the vault.",
                interpretation="Missing data warning",
                measured_at=utc_now(),
                confidence=ConfidenceScore(0.0, "rule_based", "1.2.0"),
                evidence=[],
                explanation="No blood pressure or pulse metrics exist in the vault for cardiovascular analysis.",
            )
            _validate_safety_boundaries(obs)
            observations.append(obs.to_dict())

        # 6. REGRESSION FALLBACK
        for metric, t in trend_map.items():
            canon_metric = canonicalize_metric(metric)
            if canon_metric in processed_metrics or metric in processed_metrics:
                continue

            label = _LABELS.get(metric, metric.replace("_", " ").title())
            direction = t.get("direction")
            sample_count = t.get("sample_count") or 0

            evidence = []
            docs = self.trends._docs_by_id()
            for m in self.store.list_measurements():
                if canonicalize_metric(m.get("metric")) == canon_metric:
                    doc = docs.get(str(m.get("document_id") or ""))
                    if doc and doc.get("patient_id", "default-patient") == patient_id:
                        evidence.append(
                            EvidenceReference(
                                source_type="measurement",
                                document_id=m.get("document_id"),
                                measurement_id=m.get("measurement_id") or m.get("id"),
                            )
                        )
            
            if not evidence:
                continue

            text = self._phrase(label, metric, direction)
            
            obs = HealthObservation(
                patient_id=patient_id,
                observation_id=str(uuid4()),
                category="general",
                metric=metric,
                fact=text,
                interpretation=direction or "stable",
                measured_at=utc_now(),
                confidence=ConfidenceScore(
                    value=0.85 if sample_count >= 3 else 0.5,
                    method="rule_based",
                    version="1.2.0",
                ),
                evidence=evidence,
                explanation=f"Evaluated trend direction for {metric} across {sample_count} points.",
            )
            _validate_safety_boundaries(obs)
            observations.append(obs.to_dict())

        # Persist snapshot for Doctor Visit / UI
        data = self.store._read_index()
        hi = data.get("health_intelligence") or {}
        obs_list = hi.get("observations") or []

        # Filter out old observations for this patient and append new ones
        obs_list = [o for o in obs_list if o.get("patient_id") != patient_id]
        obs_list.extend(observations)

        data["health_intelligence"] = {
            "observations": obs_list,
            "disclaimer": "Observational intelligence only — not a medical diagnosis.",
        }
        self.store._audit(data, "health_intelligence_updated", {"patient_id": patient_id, "count": len(observations)})
        self.store._write_index(data)
        return observations

    @staticmethod
    def _phrase(label: str, metric: str, direction: str | None) -> str:
        d = direction or "stable"
        if metric == "heart_rhythm" and d in {"stable", "rising", "falling"}:
            return "Heart rhythm unchanged (observational)"
        if d == "improving":
            if metric in LOWER_BETTER or metric in HIGHER_BETTER:
                return f"{label} improving (observational)"
            return f"{label} improving (observational)"
        if d == "worsening":
            if metric in {"egfr"}:
                return "Kidney declining (observational)"
            if metric in {"glucose", "hba1c"}:
                return f"{label} variability/level worsening (observational)"
            if metric in {"sleep_score"}:
                return "Sleep deteriorating (observational)"
            if metric in {"systolic", "diastolic"}:
                return "Blood pressure worsening (observational)"
            return f"{label} worsening (observational)"
        if d == "rising" and metric in {"glucose"}:
            return "Glucose variability increasing (observational)"
        if d == "stable":
            if metric in {"egfr"}:
                return "Kidney function stable (observational)"
            if metric in {"systolic", "diastolic"}:
                return "Blood pressure stable (observational)"
            return f"{label} stable (observational)"
        return f"{label} {d} (observational)"
