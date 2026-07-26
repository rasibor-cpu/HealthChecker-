"""HC-301 expanded clinical / guardian rule evaluation (extends ClinicalRulesEngine)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.health_vault.clinical_rules import ClinicalRulesEngine
from backend.health_vault.guardian.alert_engine import SAFETY_DISCLAIMER
from backend.health_vault.guardian.baseline_engine import BaselineEngine
from backend.health_vault.models import utc_now

_DEFAULT_PATH = Path(__file__).resolve().parents[1] / "config" / "guardian_rules.json"


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def _cmp(op: str, left: float, right: float) -> bool:
    if op in ("gte", ">="):
        return left >= right
    if op in ("lte", "<="):
        return left <= right
    if op in ("gt", ">"):
        return left > right
    if op in ("lt", "<"):
        return left < right
    if op in ("eq", "=="):
        return left == right
    return False


class ExpandedClinicalRulesEngine:
    """
    Extends ClinicalRulesEngine with multi-condition guardian rules.

    Authoritative implementation is Python. Browser mirror is best-effort subset.
    Missing data never evaluates as Normal.
    """

    def __init__(
        self,
        store: Any,
        *,
        clinical: ClinicalRulesEngine | None = None,
        baseline: BaselineEngine | None = None,
        rules_config: dict[str, Any] | None = None,
        path: Path | None = None,
    ) -> None:
        self.store = store
        self.clinical = clinical or ClinicalRulesEngine()
        self.baseline = baseline or BaselineEngine(store)
        if rules_config is not None:
            self.config = rules_config
        else:
            p = path or _DEFAULT_PATH
            if not p.exists():
                raise FileNotFoundError(f"Guardian rules config missing: {p}")
            self.config = json.loads(p.read_text(encoding="utf-8"))
        self._validate_config(self.config)

    @staticmethod
    def _validate_config(config: dict[str, Any]) -> None:
        if not isinstance(config, dict):
            raise ValueError("guardian_rules config must be an object")
        rules = config.get("rules")
        if rules is None:
            raise ValueError("guardian_rules missing rules[]")
        if not isinstance(rules, list):
            raise ValueError("guardian_rules.rules must be a list")
        known = {
            "absolute_threshold",
            "multi_metric",
            "rate_of_change",
            "rolling_average",
            "consecutive_abnormal",
            "baseline_deviation",
            "missing_data",
            "sensor_expiring",
            "sensor_expired",
            "inventory_reserve",
            "coverage_shortfall",
            "pipeline_failure",
        }
        for i, rule in enumerate(rules):
            if not isinstance(rule, dict):
                raise ValueError(f"rule[{i}] must be an object")
            if not rule.get("rule_id"):
                raise ValueError(f"rule[{i}] missing rule_id")
            rtype = rule.get("type")
            if rtype not in known:
                raise ValueError(f"rule[{rule.get('rule_id')}] unknown type: {rtype}")
            if rtype == "absolute_threshold" and (
                rule.get("metric") is None or rule.get("threshold") is None or rule.get("operator") is None
            ):
                raise ValueError(f"rule[{rule.get('rule_id')}] missing absolute_threshold parameters")

    @property
    def rule_pack_version(self) -> str:
        return str(self.config.get("rule_pack_version") or "1.0.0")

    def classify_measurement(self, measurement: Any) -> str:
        """Preserve HC-201 absolute flag classification."""
        return self.clinical.classify(measurement)

    def evaluate(
        self,
        *,
        patient_id: str = "default-patient",
        context: dict[str, Any] | None = None,
        now: str | None = None,
    ) -> list[dict[str, Any]]:
        ctx = dict(context or {})
        now_ts = now or utc_now()
        docs = {d.get("id"): d for d in self.store.list_documents()}
        measurements = []
        for m in self.store.list_measurements():
            pid = m.get("patient_id")
            if not pid:
                pid = (docs.get(m.get("document_id")) or {}).get("patient_id") or "default-patient"
            if str(pid) != patient_id:
                continue
            measurements.append(m)
        trends = {}
        try:
            trends = dict(self.store.get_trends() or {})
        except Exception:
            trends = {}
        continuity = ctx.get("continuity") or {}
        pipeline_failure = bool(ctx.get("pipeline_failure"))
        results: list[dict[str, Any]] = []
        for rule in self.config.get("rules") or []:
            ev = self._eval_rule(
                rule,
                measurements=measurements,
                trends=trends,
                continuity=continuity,
                pipeline_failure=pipeline_failure,
                patient_id=patient_id,
                now_ts=now_ts,
                latest_by_metric=ctx.get("latest_by_metric") or {},
            )
            if ev is not None:
                results.append(ev)
        return results

    def _eval_rule(
        self,
        rule: dict[str, Any],
        *,
        measurements: list[dict[str, Any]],
        trends: dict[str, Any],
        continuity: dict[str, Any],
        pipeline_failure: bool,
        patient_id: str,
        now_ts: str,
        latest_by_metric: dict[str, Any],
    ) -> dict[str, Any] | None:
        rtype = str(rule.get("type") or "")
        base = {
            "rule_id": rule.get("rule_id"),
            "rule_version": rule.get("version") or self.rule_pack_version,
            "title": rule.get("title"),
            "category": rule.get("category"),
            "severity": rule.get("severity") or "watch",
            "recommended_next_step": rule.get("recommended_next_step") or "",
            "safety_disclaimer": self.config.get("safety_disclaimer") or SAFETY_DISCLAIMER,
            "triggered": False,
        }

        if rtype == "pipeline_failure":
            if not pipeline_failure:
                return None
            return {
                **base,
                "triggered": True,
                "message": "Guardian evaluation or import pipeline reported a failure.",
                "metrics": [],
                "evidence": {"pipeline_failure": True},
            }

        if rtype == "sensor_expiring":
            if continuity.get("state") != "SENSOR_EXPIRING" and "SENSOR_EXPIRING" not in (continuity.get("states") or []):
                return None
            return {
                **base,
                "triggered": True,
                "message": "Active CGM sensor is approaching expected expiry.",
                "metrics": ["cgm_sensor"],
                "evidence": {"hours_remaining": continuity.get("hours_remaining")},
                "deduplication_key": f"{patient_id}|cgm_sensor_expiring",
            }

        if rtype == "sensor_expired":
            if continuity.get("state") != "SENSOR_EXPIRED" and "SENSOR_EXPIRED" not in (continuity.get("states") or []):
                return None
            return {
                **base,
                "triggered": True,
                "message": "Active CGM sensor is past expected expiry.",
                "metrics": ["cgm_sensor"],
                "evidence": {"active_sensor": continuity.get("active_sensor")},
                "deduplication_key": f"{patient_id}|cgm_sensor_expired",
            }

        if rtype == "inventory_reserve":
            if "REORDER_REQUIRED" not in (continuity.get("states") or []) and continuity.get("state") != "REORDER_REQUIRED":
                inv = continuity.get("inventory") or {}
                if inv.get("confidence") == "unknown":
                    return {
                        **base,
                        "triggered": True,
                        "severity": "watch",
                        "title": "CGM inventory unknown",
                        "message": "Sensor inventory has not been confirmed.",
                        "metrics": ["cgm_inventory"],
                        "evidence": inv,
                        "deduplication_key": f"{patient_id}|cgm_inventory_unknown",
                    }
                return None
            return {
                **base,
                "triggered": True,
                "message": "Unused CGM sensors are below the configured protected reserve.",
                "metrics": ["cgm_inventory"],
                "evidence": continuity.get("inventory") or {},
                "deduplication_key": f"{patient_id}|cgm_reserve",
            }

        if rtype == "coverage_shortfall":
            states = continuity.get("states") or []
            if continuity.get("state") not in ("CRITICAL_SHORTAGE", "REORDER_REQUIRED") and "CRITICAL_SHORTAGE" not in states:
                return None
            if continuity.get("state") != "CRITICAL_SHORTAGE" and "CRITICAL_SHORTAGE" not in states:
                # Only fire coverage rule on critical shortage; reserve rule covers reorder
                if "CRITICAL_SHORTAGE" not in states:
                    return None
            return {
                **base,
                "triggered": True,
                "message": "Projected CGM coverage is insufficient for configured buffers.",
                "metrics": ["cgm_inventory"],
                "evidence": continuity.get("inventory") or {},
                "deduplication_key": f"{patient_id}|cgm_coverage",
            }

        if rtype == "missing_data":
            gaps = continuity.get("open_data_gaps") or []
            if not gaps:
                return None
            gap = gaps[0]
            return {
                **base,
                "triggered": True,
                "message": "Glucose data gap detected. No data is never treated as normal.",
                "metrics": [rule.get("metric") or "glucose"],
                "evidence": gap,
                "severity": "critical"
                if gap.get("escalation_status") == "critical"
                else ("urgent" if gap.get("escalation_status") == "urgent" else base["severity"]),
                "deduplication_key": f"{patient_id}|glucose_data_gap",
            }

        if rtype == "absolute_threshold":
            metric = str(rule.get("metric") or "")
            latest = latest_by_metric.get(metric) or self._latest(measurements, metric)
            if not latest:
                # No data → do not treat as normal; emit nothing for absolute unless explicit missing rule
                return None
            val = _to_float(latest.get("value"))
            if val is None:
                return None
            units = latest.get("units")
            allowed = rule.get("units") or []
            if units and allowed and units not in allowed:
                return None
            thr = float(rule.get("threshold"))
            op = str(rule.get("operator") or "gte")
            if not _cmp(op, val, thr):
                return None
            return {
                **base,
                "triggered": True,
                "metric": metric,
                "metrics": [metric],
                "message": f"{rule.get('title')}: observed {val} {units or ''} (threshold {op} {thr}).",
                "evidence": {"value": val, "units": units, "measured_at": latest.get("measured_at"), "threshold": thr},
                "deduplication_key": f"{patient_id}|{rule.get('rule_id')}|{metric}",
            }

        if rtype == "multi_metric":
            clauses = rule.get("all_of") or []
            mode = "all"
            if rule.get("any_of"):
                clauses = rule.get("any_of") or []
                mode = "any"
            hits = []
            for c in clauses:
                metric = str(c.get("metric") or "")
                latest = latest_by_metric.get(metric) or self._latest(measurements, metric)
                if not latest:
                    hits.append(False)
                    continue
                val = _to_float(latest.get("value"))
                if val is None:
                    hits.append(False)
                    continue
                hits.append(_cmp(str(c.get("operator") or "gte"), val, float(c.get("threshold"))))
            ok = all(hits) if mode == "all" else any(hits)
            if not ok or not hits:
                return None
            metrics = [str(c.get("metric")) for c in clauses]
            return {
                **base,
                "triggered": True,
                "metrics": metrics,
                "message": str(rule.get("title")),
                "evidence": {"mode": mode, "metrics": metrics},
                "deduplication_key": f"{patient_id}|{rule.get('rule_id')}",
            }

        if rtype == "rate_of_change":
            metric = str(rule.get("metric") or "")
            window = int(rule.get("window_minutes") or 60)
            series = self._series(measurements, metric, now_ts=now_ts, window_minutes=window)
            min_points = int(rule.get("min_points") or 2)
            if len(series) < min_points:
                return None
            delta = series[-1]["value"] - series[0]["value"]
            triggered = False
            if rule.get("delta_lte") is not None and delta <= float(rule["delta_lte"]):
                triggered = True
            if rule.get("delta_gte") is not None and delta >= float(rule["delta_gte"]):
                triggered = True
            if not triggered:
                return None
            return {
                **base,
                "triggered": True,
                "metric": metric,
                "metrics": [metric],
                "message": f"{rule.get('title')}: delta {delta:.1f} over ~{window} minutes.",
                "evidence": {"delta": delta, "window_minutes": window, "points": len(series)},
                "deduplication_key": f"{patient_id}|{rule.get('rule_id')}|{metric}",
            }

        if rtype == "rolling_average":
            metric = str(rule.get("metric") or "")
            days = int(rule.get("window_days") or 3)
            series = self._series(measurements, metric, now_ts=now_ts, window_minutes=days * 24 * 60)
            min_points = int(rule.get("min_points") or 3)
            if len(series) < min_points:
                return None
            vals = [p["value"] for p in series]
            avg = sum(vals) / len(vals)
            first_avg = sum(vals[: max(1, len(vals) // 2)]) / max(1, len(vals) // 2)
            last_avg = sum(vals[len(vals) // 2 :]) / max(1, len(vals) - len(vals) // 2)
            delta = last_avg - first_avg
            if rule.get("compare") == "rising" and rule.get("delta_gte") is not None:
                if delta < float(rule["delta_gte"]):
                    return None
            # Also consult trend engine label when present
            trend = trends.get(metric) or {}
            if trend.get("direction") == "worsening" or delta >= float(rule.get("delta_gte") or 0):
                return {
                    **base,
                    "triggered": True,
                    "metric": metric,
                    "metrics": [metric],
                    "message": f"{rule.get('title')}: rolling average shift {delta:.1f} (mean {avg:.1f}).",
                    "evidence": {"delta": delta, "mean": avg, "trend": trend.get("direction")},
                    "deduplication_key": f"{patient_id}|{rule.get('rule_id')}|{metric}",
                }
            return None

        if rtype == "consecutive_abnormal":
            metric = str(rule.get("metric") or "")
            flags = set(rule.get("abnormal_flags") or ["Abnormal", "Critical"])
            need = int(rule.get("min_consecutive") or 3)
            series = self._series(measurements, metric, now_ts=now_ts, window_minutes=14 * 24 * 60)
            streak = 0
            for p in reversed(series):
                flag = p.get("abnormal_flag") or self.clinical.classify(p)
                if flag in flags:
                    streak += 1
                else:
                    break
            if streak < need:
                return None
            return {
                **base,
                "triggered": True,
                "metric": metric,
                "metrics": [metric],
                "message": f"{streak} consecutive abnormal {metric} readings.",
                "evidence": {"streak": streak},
                "deduplication_key": f"{patient_id}|{rule.get('rule_id')}|{metric}",
            }

        if rtype == "baseline_deviation":
            metric = str(rule.get("metric") or "")
            latest = latest_by_metric.get(metric) or self._latest(measurements, metric)
            if not latest:
                return None
            # Absolute critical clinical flags always win — do not soften into baseline watch
            abs_flag = self.clinical.classify(latest)
            if abs_flag == "Critical":
                return None
            dev = self.baseline.deviation(
                metric,
                latest.get("value"),
                units=latest.get("units"),
                patient_id=patient_id,
            )
            if not dev.get("available") or not dev.get("outside_band"):
                return None
            return {
                **base,
                "triggered": True,
                "metric": metric,
                "metrics": [metric],
                "message": f"{metric} outside personal baseline band.",
                "evidence": dev,
                "deduplication_key": f"{patient_id}|{rule.get('rule_id')}|{metric}",
            }

        return None

    def _latest(self, measurements: list[dict[str, Any]], metric: str) -> dict[str, Any] | None:
        best = None
        best_ts = None
        for m in measurements:
            if m.get("metric") != metric:
                continue
            ts = _parse_ts(m.get("measured_at"))
            if best is None or (ts and (best_ts is None or ts > best_ts)):
                best = m
                best_ts = ts
        return best

    def _series(
        self,
        measurements: list[dict[str, Any]],
        metric: str,
        *,
        now_ts: str,
        window_minutes: int,
    ) -> list[dict[str, Any]]:
        now = _parse_ts(now_ts) or datetime.now(timezone.utc)
        out: list[dict[str, Any]] = []
        for m in measurements:
            if m.get("metric") != metric:
                continue
            val = _to_float(m.get("value"))
            if val is None:
                continue
            ts = _parse_ts(m.get("measured_at"))
            if ts is None:
                continue
            age_min = (now - ts).total_seconds() / 60.0
            if age_min < 0 or age_min > window_minutes:
                continue
            row = dict(m)
            row["value"] = val
            row["_ts"] = ts
            out.append(row)
        out.sort(key=lambda r: r["_ts"])
        return out
