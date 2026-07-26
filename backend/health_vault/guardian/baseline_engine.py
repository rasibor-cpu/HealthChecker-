"""HC-301 Personalized Baseline Engine — confirmed measurements only."""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any

from backend.health_vault.models import utc_now

_DEFAULT_PATH = Path(__file__).resolve().parents[1] / "config" / "baseline_config.json"


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        num = float(value)
        if math.isnan(num) or math.isinf(num):
            return None
        return num
    except (TypeError, ValueError):
        return None


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


def _parse_ts(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


class BaselineEngine:
    """Deterministic personalized baselines from append-only vault measurements."""

    def __init__(self, store: Any, config: dict[str, Any] | None = None, path: Path | None = None) -> None:
        if config is not None:
            self.config = config
        else:
            p = path or _DEFAULT_PATH
            self.config = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {
                "minimum_sample_count": 5,
                "rolling_window_days": 90,
                "min_confidence": 0.5,
                "percentile_low": 10,
                "percentile_high": 90,
                "supported_metrics": ["glucose", "systolic", "diastolic", "resting_hr"],
            }
        self.store = store

    def rebuild(
        self,
        *,
        patient_id: str = "default-patient",
        as_of: str | None = None,
    ) -> dict[str, Any]:
        as_of_ts = as_of or utc_now()
        window_days = int(self.config.get("rolling_window_days") or 90)
        min_n = int(self.config.get("minimum_sample_count") or 5)
        min_conf = float(self.config.get("min_confidence") or 0.5)
        lo_pct = float(self.config.get("percentile_low") or 10)
        hi_pct = float(self.config.get("percentile_high") or 90)
        supported = list(self.config.get("supported_metrics") or [])
        cutoff = None
        as_epoch = _parse_ts(as_of_ts)
        if as_epoch is not None:
            cutoff = as_epoch - (window_days * 86400)

        by_metric: dict[str, list[dict[str, Any]]] = {}
        docs_by_id = {d.get("id"): d for d in self.store.list_documents()}
        for m in self.store.list_measurements():
            # Patient isolation via measurement or parent document
            m_pid = m.get("patient_id")
            if not m_pid:
                doc = docs_by_id.get(m.get("document_id")) or {}
                m_pid = doc.get("patient_id") or "default-patient"
            if str(m_pid) != patient_id:
                continue
            metric = str(m.get("metric") or "")
            if supported and metric not in supported:
                continue
            # Reject invalid / low confidence
            conf = m.get("confidence")
            if conf is not None:
                try:
                    if float(conf) < min_conf:
                        continue
                except (TypeError, ValueError):
                    pass
            flag = str(m.get("abnormal_flag") or "")
            if flag == "Unknown" and m.get("unit_compatible") is False:
                continue
            val = _to_float(m.get("value"))
            if val is None:
                continue
            units = str(m.get("units") or "") or None
            measured = m.get("measured_at") or m.get("imported_at")
            epoch = _parse_ts(measured)
            if cutoff is not None and epoch is not None and epoch < cutoff:
                continue
            if as_epoch is not None and epoch is not None and epoch > as_epoch:
                continue
            context = str(m.get("context") or m.get("meal_context") or "unspecified")
            by_metric.setdefault(metric, []).append(
                {"value": val, "units": units, "measured_at": measured, "context": context}
            )

        baselines: dict[str, Any] = {}
        for metric, rows in by_metric.items():
            # Separate incompatible units
            by_units: dict[str, list[dict[str, Any]]] = {}
            for r in rows:
                key = r.get("units") or ""
                by_units.setdefault(key, []).append(r)
            # Prefer the most common unit set
            unit_key = max(by_units.keys(), key=lambda k: len(by_units[k]))
            series = by_units[unit_key]
            values = [r["value"] for r in series]
            sample_count = len(values)
            ready = sample_count >= min_n
            sorted_vals = sorted(values)
            mean = statistics.fmean(values) if values else None
            median = statistics.median(values) if values else None
            stdev = statistics.pstdev(values) if len(values) >= 2 else 0.0
            contextual: dict[str, Any] = {}
            for ctx in self.config.get("contexts") or []:
                ctx_vals = [r["value"] for r in series if r.get("context") == ctx]
                if len(ctx_vals) >= min_n:
                    contextual[ctx] = {
                        "sample_count": len(ctx_vals),
                        "median": statistics.median(ctx_vals),
                        "mean": statistics.fmean(ctx_vals),
                        "lower_percentile": _percentile(sorted(ctx_vals), lo_pct),
                        "upper_percentile": _percentile(sorted(ctx_vals), hi_pct),
                    }
            confidence = 0.0
            if ready:
                confidence = min(1.0, 0.5 + (sample_count - min_n) * 0.05)
            entry = {
                "metric": metric,
                "patient_id": patient_id,
                "sample_count": sample_count,
                "observation_window_days": window_days,
                "units": unit_key or None,
                "median": median,
                "mean": mean,
                "minimum": min(values) if values else None,
                "maximum": max(values) if values else None,
                "standard_deviation": stdev,
                "lower_percentile_band": _percentile(sorted_vals, lo_pct) if values else None,
                "upper_percentile_band": _percentile(sorted_vals, hi_pct) if values else None,
                "last_updated": as_of_ts,
                "baseline_confidence": round(confidence, 3),
                "ready": ready,
                "contextual": contextual,
                "insufficient_data": not ready,
            }
            baselines[metric] = entry

        payload = {
            "patient_id": patient_id,
            "as_of": as_of_ts,
            "baselines": baselines,
            "config_version": self.config.get("schema_version"),
            "disclaimer": "Personalized baselines are observational and not diagnostic.",
        }
        self.store.save_baselines(payload)
        return payload

    def get_summaries(self, patient_id: str = "default-patient") -> dict[str, Any]:
        data = self.store.get_baselines()
        if not data or data.get("patient_id") != patient_id:
            return self.rebuild(patient_id=patient_id)
        return data

    def deviation(
        self,
        metric: str,
        value: Any,
        *,
        units: str | None = None,
        patient_id: str = "default-patient",
    ) -> dict[str, Any]:
        """Return whether value is outside personal band; never treats missing as normal."""
        num = _to_float(value)
        if num is None:
            return {
                "metric": metric,
                "available": False,
                "outside_band": False,
                "reason": "no_data",
                "message": "No data is never interpreted as a normal measurement.",
            }
        summaries = self.get_summaries(patient_id=patient_id)
        base = (summaries.get("baselines") or {}).get(metric)
        if not base or not base.get("ready"):
            return {
                "metric": metric,
                "available": False,
                "outside_band": False,
                "reason": "insufficient_baseline",
                "fallback": "population_or_configured_rules",
            }
        if units and base.get("units") and units != base.get("units"):
            return {
                "metric": metric,
                "available": False,
                "outside_band": False,
                "reason": "unit_mismatch",
            }
        lo = base.get("lower_percentile_band")
        hi = base.get("upper_percentile_band")
        outside = (lo is not None and num < float(lo)) or (hi is not None and num > float(hi))
        return {
            "metric": metric,
            "available": True,
            "value": num,
            "outside_band": outside,
            "lower": lo,
            "upper": hi,
            "baseline_confidence": base.get("baseline_confidence"),
            "reason": "outside_band" if outside else "within_band",
        }
