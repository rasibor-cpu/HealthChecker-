"""Executive Health Intelligence — observational only, never diagnoses."""

from __future__ import annotations

from typing import Any

from backend.health_vault.trend_engine import HIGHER_BETTER, LOWER_BETTER, TrendEngine
from backend.health_vault.vault_store import VaultStore

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

    def generate_observations(self) -> list[dict[str, Any]]:
        trend_map = self.trends.recompute()
        observations: list[dict[str, Any]] = []
        for metric, t in trend_map.items():
            label = _LABELS.get(metric, metric.replace("_", " ").title())
            direction = t.get("direction")
            sample_count = t.get("sample_count") or 0
            if sample_count < 3:
                observations.append(
                    {
                        "metric": metric,
                        "observation": f"{label} — insufficient history for trend observation",
                        "direction": "stable",
                        "kind": "observational",
                        "diagnostic": False,
                    }
                )
                continue
            text = self._phrase(label, metric, direction)
            observations.append(
                {
                    "metric": metric,
                    "observation": text,
                    "direction": direction,
                    "kind": "observational",
                    "diagnostic": False,
                    "latest": t.get("latest"),
                    "sample_count": sample_count,
                }
            )
        # Persist snapshot for Doctor Visit / UI
        data = self.store._read_index()
        data["health_intelligence"] = {
            "observations": observations,
            "disclaimer": "Observational intelligence only — not a medical diagnosis.",
        }
        self.store._audit(data, "health_intelligence_updated", {"count": len(observations)})
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
