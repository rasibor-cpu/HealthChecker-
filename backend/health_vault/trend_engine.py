"""Automatic trend detection: Improving / Stable / Worsening (HC-201H trend-ready)."""

from __future__ import annotations

from typing import Any

from backend.health_vault.date_extraction import timeline_sort_key
from backend.health_vault.metric_normalization import (
    MONITORING_TREND_METRICS,
    TREND_METRICS,
    canonicalize_metric,
)
from backend.health_vault.models import utc_now
from backend.health_vault.vault_store import VaultStore

HIGHER_BETTER = {
    "egfr",
    "sleep_score",
    "energy_score",
    "cgm_time_in_range",
    "hrv_rmssd",
    "sleep_duration",
    "oxygen_saturation",
    "steps",
    "activity_minutes",
    "exercise_minutes",
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


def _is_health_connect_context(m: dict[str, Any], doc: dict[str, Any] | None) -> bool:
    """True when the measurement/document carries Health Connect monitoring provenance."""
    source_bits = " ".join(
        str(x or "")
        for x in (
            m.get("source"),
            m.get("source_system"),
            m.get("provenance"),
            m.get("connector_id"),
            (doc or {}).get("source"),
            (doc or {}).get("source_system"),
            (doc or {}).get("provenance"),
            (doc or {}).get("connector_id"),
            (doc or {}).get("document_type"),
        )
    ).lower()
    raw_tags = (doc or {}).get("tags") or []
    tags = {str(t).lower() for t in raw_tags} if isinstance(raw_tags, (list, tuple, set)) else set()
    if "health_connect" in source_bits or "hc302" in source_bits:
        return True
    if "continuous_monitoring" in source_bits or "continuous_monitoring" in tags:
        return True
    if (doc or {}).get("document_type") == "continuous_monitoring_observation":
        return True
    if m.get("connector_id") == "health_connect" or (doc or {}).get("connector_id") == "health_connect":
        return True
    return False


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

    def _eligible(
        self,
        m: dict[str, Any],
        docs: dict[str, dict[str, Any]],
        *,
        allow_monitoring: bool = True,
    ) -> bool:
        if m.get("unit_compatible") is False:
            return False
        if m.get("value") is None:
            return False
        metric = canonicalize_metric(m.get("metric"))
        doc = docs.get(str(m.get("document_id") or ""))
        monitoring_ctx = allow_monitoring and _is_health_connect_context(m, doc)

        if monitoring_ctx:
            if metric not in MONITORING_TREND_METRICS and m.get("metric") not in MONITORING_TREND_METRICS:
                return False
        else:
            if metric not in TREND_METRICS and m.get("metric") not in TREND_METRICS:
                # Allow legacy names still in TREND via canonicalize
                if metric not in TREND_METRICS:
                    return False

        if doc:
            if doc.get("duplicate_of"):
                return False
            if doc.get("status") == "failed":
                return False
            if not monitoring_ctx:
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
        elif monitoring_ctx and not m.get("measured_at"):
            return False
        try:
            float(m["value"])
        except (TypeError, ValueError, KeyError):
            return False
        return True

    def _series_provenance(
        self, metric: str, patient_id: str, docs: dict[str, dict[str, Any]]
    ) -> str:
        """Return dominant provenance for an eligible series (clinical vs observational)."""
        monitoring = 0
        clinical = 0
        for m in self.store.list_measurements():
            if canonicalize_metric(m.get("metric")) != canonicalize_metric(metric):
                continue
            if not self._eligible(m, docs):
                continue
            doc = docs.get(str(m.get("document_id") or ""))
            if (doc or {}).get("patient_id", "default-patient") != patient_id:
                continue
            if _is_health_connect_context(m, doc):
                monitoring += 1
            else:
                clinical += 1
        if clinical and monitoring:
            return "mixed_clinical_and_observational"
        if monitoring:
            return "health_connect_observational"
        return "clinical"

    def series(
        self,
        metric: str,
        patient_id: str = "default-patient",
        *,
        plane: str = "auto",
    ) -> list[float]:
        """Build a numeric series.

        plane:
          - clinical: imported clinical/lab documents only
          - monitoring: Health Connect / continuous monitoring only
          - auto: prefer clinical when present; otherwise monitoring (never merge both)
        """
        canonical = canonicalize_metric(metric)
        docs = self._docs_by_id()
        candidates: list[dict[str, Any]] = []
        for m in self.store.list_measurements():
            if canonicalize_metric(m.get("metric")) != canonical:
                continue
            if not self._eligible(m, docs):
                continue
            doc = docs.get(str(m.get("document_id") or "")) or {}
            if doc.get("patient_id", "default-patient") != patient_id:
                continue
            candidates.append(m)

        clinical = [
            m
            for m in candidates
            if not _is_health_connect_context(m, docs.get(str(m.get("document_id") or "")))
        ]
        monitoring = [
            m
            for m in candidates
            if _is_health_connect_context(m, docs.get(str(m.get("document_id") or "")))
        ]

        if plane == "clinical":
            items = clinical
        elif plane == "monitoring":
            items = monitoring
        else:
            # Never merge incompatible clinical + wearable series into one direction.
            items = clinical if clinical else monitoring

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

    def recompute(self, patient_id: str = "default-patient") -> dict[str, Any]:
        docs = self._docs_by_id()
        allowed = TREND_METRICS | MONITORING_TREND_METRICS
        active_metrics: set[str] = set()
        for m in self.store.list_measurements():
            if not m.get("metric") or not self._eligible(m, docs):
                continue
            metric = canonicalize_metric(m.get("metric"))
            if metric not in allowed:
                continue
            doc = docs.get(str(m.get("document_id") or ""))
            if doc and doc.get("patient_id", "default-patient") == patient_id:
                active_metrics.add(metric)

        trends: dict[str, Any] = {}
        for metric in active_metrics:
            clinical_series = self.series(metric, patient_id=patient_id, plane="clinical")
            monitoring_series = self.series(metric, patient_id=patient_id, plane="monitoring")
            if clinical_series:
                plane = "clinical"
                series = clinical_series
                provenance = "clinical"
            elif monitoring_series:
                plane = "monitoring"
                series = monitoring_series
                provenance = "health_connect_observational"
            else:
                continue
            result = self.classify(metric, series)
            category = None
            for m in self.store.list_measurements():
                if canonicalize_metric(m.get("metric")) != metric or not self._eligible(m, docs):
                    continue
                doc = docs.get(str(m.get("document_id") or ""))
                if not doc or doc.get("patient_id", "default-patient") != patient_id:
                    continue
                is_hc = _is_health_connect_context(m, doc)
                if plane == "clinical" and is_hc:
                    continue
                if plane == "monitoring" and not is_hc:
                    continue
                category = doc.get("primary_category") or (
                    "continuous_monitoring" if is_hc else None
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
                "provenance": provenance,
                "data_plane": plane,
                "updated_at": utc_now(),
                "fhir_resource": "Observation",
            }
        self.store.save_trends(trends, patient_id=patient_id)
        return trends
