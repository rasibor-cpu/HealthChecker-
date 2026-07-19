"""Automatic trend detection: Improving / Stable / Worsening (HC-201H trend-ready)."""

from __future__ import annotations

from typing import Any

from backend.health_vault.date_extraction import timeline_sort_key
from backend.health_vault.metric_normalization import TREND_METRICS, canonicalize_metric
from backend.health_vault.models import utc_now
from backend.health_vault.vault_store import VaultStore

HIGHER_BETTER = {
    "egfr",
    "sleep_score",
    "energy_score",
    "cgm_time_in_range",
    "hrv_rmssd",
    "sleep_duration",
}
LOWER_BETTER = {
    "glucose",
    "hba1c",
    "systolic",
    "diastolic",
    "systolic_bp",
    "diastolic_bp",
    "creatinine",
    "uacr",
    "resting_hr",
    "bmi",
}

# Default: exclude measurements needing review below this overall doc confidence proxy
DEFAULT_MIN_DATE_CONFIDENCE = 0.2


class TrendEngine:
    def __init__(
        self,
        store: VaultStore,
        *,
        min_date_confidence: float = DEFAULT_MIN_DATE_CONFIDENCE,
    ) -> None:
        self.store = store
        self.min_date_confidence = min_date_confidence

    def _docs_by_id(self) -> dict[str, dict[str, Any]]:
        return {d["id"]: d for d in self.store.list_documents() if d.get("id")}

    def _eligible(self, m: dict[str, Any], docs: dict[str, dict[str, Any]]) -> bool:
        if m.get("unit_compatible") is False:
            return False
        if m.get("value") is None:
            return False
        metric = canonicalize_metric(m.get("metric"))
        if metric not in TREND_METRICS and m.get("metric") not in TREND_METRICS:
            # Allow legacy names still in TREND via canonicalize
            if metric not in TREND_METRICS:
                return False
        doc = docs.get(str(m.get("document_id") or ""))
        if doc:
            if doc.get("duplicate_of"):
                return False
            if doc.get("status") == "failed":
                return False
            date_conf = doc.get("date_confidence")
            if date_conf is not None and float(date_conf) < self.min_date_confidence:
                return False
            # Only exclude review items when explicitly low classification confidence
            if (
                doc.get("requires_review")
                and doc.get("classification_confidence") is not None
                and float(doc.get("classification_confidence") or 0) < 0.45
            ):
                return False
            if not (
                doc.get("measured_at") or doc.get("report_date") or m.get("measured_at")
            ):
                return False
        try:
            float(m["value"])
        except (TypeError, ValueError, KeyError):
            return False
        return True

    def series(self, metric: str) -> list[float]:
        canonical = canonicalize_metric(metric)
        docs = self._docs_by_id()
        items = [
            m
            for m in self.store.list_measurements()
            if canonicalize_metric(m.get("metric")) == canonical and self._eligible(m, docs)
        ]
        items.sort(
            key=lambda x: str(
                x.get("measured_at")
                or (docs.get(str(x.get("document_id") or ""), {}) or {}).get("measured_at")
                or ""
            )
        )
        values = []
        for m in items:
            try:
                values.append(float(m["value"]))
            except (TypeError, ValueError, KeyError):
                continue
        return values

    @staticmethod
    def classify(metric: str, values: list[float]) -> dict[str, Any]:
        metric = canonicalize_metric(metric)
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
        docs = self._docs_by_id()
        metrics = {
            canonicalize_metric(m.get("metric"))
            for m in self.store.list_measurements()
            if m.get("metric") and self._eligible(m, docs)
        }
        metrics = {m for m in metrics if m in TREND_METRICS}
        trends: dict[str, Any] = {}
        for metric in metrics:
            series = self.series(metric)
            result = self.classify(metric, series)
            # Category from first eligible measurement's document
            category = None
            for m in self.store.list_measurements():
                if canonicalize_metric(m.get("metric")) == metric and self._eligible(m, docs):
                    category = (docs.get(str(m.get("document_id") or "")) or {}).get(
                        "primary_category"
                    )
                    break
            trends[metric] = {
                "metric": metric,
                "direction": result["direction"],
                "label": result["label"],
                "reason": result["reason"],
                "sample_count": len(series),
                "latest": series[-1] if series else None,
                "category": category,
                "updated_at": utc_now(),
                "fhir_resource": "Observation",
            }
        self.store.save_trends(trends)
        return trends
