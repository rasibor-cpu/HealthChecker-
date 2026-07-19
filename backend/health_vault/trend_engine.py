"""Automatic trend detection: Improving / Stable / Worsening."""

from __future__ import annotations

from typing import Any

from backend.health_vault.models import utc_now
from backend.health_vault.vault_store import VaultStore

HIGHER_BETTER = {"egfr", "sleep_score", "energy_score", "cgm_time_in_range"}
LOWER_BETTER = {
    "glucose",
    "hba1c",
    "systolic",
    "diastolic",
    "creatinine",
    "uacr",
    "resting_hr",
    "bmi",
}


class TrendEngine:
    def __init__(self, store: VaultStore) -> None:
        self.store = store

    def series(self, metric: str) -> list[float]:
        items = self.store.list_measurements(metric=metric)
        values = []
        for m in sorted(items, key=lambda x: str(x.get("measured_at") or "")):
            try:
                values.append(float(m["value"]))
            except (TypeError, ValueError, KeyError):
                continue
        return values

    @staticmethod
    def classify(metric: str, values: list[float]) -> dict[str, Any]:
        if len(values) < 3:
            return {"direction": "stable", "label": "Stable", "reason": "insufficient_points"}
        a = values[-3:]
        rising = a[2] > a[1] > a[0]
        falling = a[2] < a[1] < a[0]
        direction = "stable"
        if metric in HIGHER_BETTER:
            if rising:
                direction = "improving"
            elif falling:
                direction = "worsening"
        elif metric in LOWER_BETTER:
            if falling:
                direction = "improving"
            elif rising:
                direction = "worsening"
        else:
            if rising:
                direction = "rising"
            elif falling:
                direction = "falling"
        labels = {
            "improving": "Improving",
            "worsening": "Worsening",
            "rising": "Rising",
            "falling": "Falling",
            "stable": "Stable",
        }
        return {"direction": direction, "label": labels[direction], "reason": "auto", "window": a}

    def recompute(self) -> dict[str, Any]:
        metrics = {m.get("metric") for m in self.store.list_measurements() if m.get("metric")}
        trends: dict[str, Any] = {}
        for metric in metrics:
            series = self.series(metric)
            result = self.classify(metric, series)
            trends[metric] = {
                "metric": metric,
                "direction": result["direction"],
                "label": result["label"],
                "reason": result["reason"],
                "sample_count": len(series),
                "latest": series[-1] if series else None,
                "updated_at": utc_now(),
                "fhir_resource": "Observation",
            }
        self.store.save_trends(trends)
        return trends
