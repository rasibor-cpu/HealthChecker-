"""Executive Health Intelligence — observational only, never diagnoses."""

from __future__ import annotations

import logging
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
    "egfr": "Kidney function",
    "creatinine": "Creatinine",
    "glucose": "Glucose",
    "hba1c": "HbA1c / diabetes marker",
    "systolic": "Blood pressure (systolic)",
    "diastolic": "Blood pressure (diastolic)",
    "sleep_score": "Sleep",
    "energy_score": "Energy score",
    "resting_hr": "Resting heart rate",
    "hrv": "HRV",
    "heart_rhythm": "Heart rhythm",
}


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

    def generate_observations(self, patient_id: str = "default-patient") -> list[dict[str, Any]]:
        """Generate and persist patient-specific observations using structured models."""
        trend_map = self.trends.recompute(patient_id=patient_id)
        observations: list[dict[str, Any]] = []

        docs = self.trends._docs_by_id()

        for metric, t in trend_map.items():
            label = _LABELS.get(metric, metric.replace("_", " ").title())
            direction = t.get("direction")
            sample_count = t.get("sample_count") or 0

            # Map category
            canon_metric = canonicalize_metric(metric)
            if canon_metric in {"egfr", "creatinine"}:
                category = "renal"
            elif canon_metric in {"glucose", "hba1c"}:
                category = "glycemic"
            elif canon_metric in {"systolic", "diastolic"}:
                category = "cardiovascular"
            elif canon_metric in {"sleep_score", "sleep_duration"}:
                category = "sleep"
            else:
                category = "general"

            # Gather evidence trace
            evidence = []
            for m in self.store.list_measurements():
                if canonicalize_metric(m.get("metric")) == canon_metric:
                    doc = docs.get(str(m.get("document_id") or ""))
                    if doc and doc.get("patient_id", "default-patient") == patient_id:
                        evidence.append(
                            EvidenceReference(
                                source_type="measurement",
                                document_id=m.get("document_id"),
                                measurement_id=m.get("measurement_id") or m.get("id"),
                                sha256=doc.get("sha256"),
                            )
                        )

            # Traceability constraint: observations MUST trace back to source evidence
            if not evidence:
                continue

            text = self._phrase(label, metric, direction)
            
            # Construct using the canonical HealthObservation data model
            score = ConfidenceScore(
                value=0.85 if sample_count >= 3 else 0.5,
                method="rule_based",
                version="1.0.0",
            )
            obs = HealthObservation(
                patient_id=patient_id,
                observation_id=str(uuid4()),
                category=category,
                metric=metric,
                fact=text,
                interpretation=direction or "stable",
                measured_at=utc_now(),
                confidence=score,
                evidence=evidence,
            )
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
