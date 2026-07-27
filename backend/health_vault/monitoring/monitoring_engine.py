"""HC-302 monitoring engine — freshness, thresholds, short-term trends."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from backend.health_vault.guardian.alert_engine import AlertEngine, SEVERITIES
from backend.health_vault.models import utc_now
from backend.health_vault.monitoring.ingestion import IngestionCoordinator, load_monitoring_config
from backend.health_vault.monitoring.observation import parse_timestamp
from backend.health_vault.vault_store import VaultStore

_THRESHOLDS_PATH = Path(__file__).resolve().parents[1] / "config" / "monitoring_thresholds.json"

_VALID_OPS = {"gt", "gte", "lt", "lte"}


def validate_monitoring_thresholds(raw: dict[str, Any]) -> dict[str, Any]:
    """Schema-validate threshold config; drop malformed rules instead of crashing."""
    if not isinstance(raw, dict):
        raise ValueError("monitoring_thresholds_must_be_object")
    rules_out: list[dict[str, Any]] = []
    errors: list[str] = []
    for i, rule in enumerate(raw.get("rules") or []):
        if not isinstance(rule, dict):
            errors.append(f"rules[{i}]_not_object")
            continue
        rid = rule.get("rule_id")
        metric = rule.get("metric")
        op = str(rule.get("op") or "")
        sev = str(rule.get("severity") or "")
        if not rid or not metric:
            errors.append(f"rules[{i}]_missing_rule_id_or_metric")
            continue
        if op not in _VALID_OPS:
            errors.append(f"rules[{i}]_invalid_op:{op}")
            continue
        if sev not in SEVERITIES:
            errors.append(f"rules[{i}]_invalid_severity:{sev}")
            continue
        try:
            float(rule.get("value"))
        except (TypeError, ValueError):
            errors.append(f"rules[{i}]_invalid_value")
            continue
        rules_out.append(dict(rule))

    trends_out: list[dict[str, Any]] = []
    for i, rule in enumerate(raw.get("trend_rules") or []):
        if not isinstance(rule, dict):
            errors.append(f"trend_rules[{i}]_not_object")
            continue
        rid = rule.get("rule_id")
        metric = rule.get("metric")
        sev = str(rule.get("severity") or "")
        if not rid or not metric:
            errors.append(f"trend_rules[{i}]_missing_rule_id_or_metric")
            continue
        if sev not in SEVERITIES:
            errors.append(f"trend_rules[{i}]_invalid_severity:{sev}")
            continue
        try:
            int(rule.get("window_minutes") or 60)
            int(rule.get("min_points") or 3)
            float(rule.get("delta_gt") or 0)
        except (TypeError, ValueError):
            errors.append(f"trend_rules[{i}]_invalid_numeric_fields")
            continue
        trends_out.append(dict(rule))

    return {
        "schema_version": raw.get("schema_version") or "hc.monitoring_thresholds.v1",
        "disclaimer": raw.get("disclaimer"),
        "rules": rules_out,
        "trend_rules": trends_out,
        "validation_errors": errors,
        "ok": len(errors) == 0,
    }


def load_monitoring_thresholds(path: Path | None = None) -> dict[str, Any]:
    p = path or _THRESHOLDS_PATH
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"monitoring_thresholds_unreadable:{type(exc).__name__}") from exc
    return validate_monitoring_thresholds(raw)


def _dt(ts: str) -> datetime:
    text = parse_timestamp(ts)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


class MonitoringEngine:
    """
    Evaluate new readings and short-term trends using configurable thresholds.

    Alert lifecycle is delegated to HC-301 AlertEngine (dedupe / ack / resolve).
    Absolute vitals that overlap Guardian reuse the same rule_ids to avoid duplicates.
    Stale readings never fire absolute/trend clinical thresholds as if current.
    """

    def __init__(
        self,
        store: VaultStore | None = None,
        alerts: AlertEngine | None = None,
        ingestion: IngestionCoordinator | None = None,
        thresholds: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.store = store or VaultStore()
        self.alerts = alerts or AlertEngine(self.store)
        self.ingestion = ingestion or IngestionCoordinator(store=self.store)
        self.thresholds = thresholds if thresholds is not None else load_monitoring_thresholds()
        self.config = config or load_monitoring_config()

    def evaluate(
        self,
        *,
        patient_id: str = "default-patient",
        now: str | None = None,
        trigger: str = "monitoring_eval",
    ) -> dict[str, Any]:
        now_ts = now or utc_now()
        observations = [
            o
            for o in self.store.list_observations()
            if str(o.get("patient_id") or "default-patient") == patient_id
            and o.get("acquisition_mode") != "SIMULATED_TEST_ONLY"
            and o.get("clinical_persist") is not False
        ]
        evaluations: list[dict[str, Any]] = []
        freshness_flags: list[dict[str, Any]] = []

        latest_by_metric = self._latest_by_metric(observations, patient_id=patient_id)
        for metric, row in latest_by_metric.items():
            mode = row.get("acquisition_mode")
            measured_at = row.get("measured_at")
            if not measured_at:
                freshness = "missing"
            else:
                freshness = self.ingestion.compute_freshness(
                    metric=metric,
                    measured_at=str(measured_at),
                    now=now_ts,
                    acquisition_mode=mode,
                )
            if row.get("observation_id"):
                updated = dict(row)
                updated["freshness_status"] = freshness
                self.store.upsert_observation(updated)

            if freshness in {"stale", "missing"}:
                sev = "warning" if freshness == "stale" else "watch"
                ev = {
                    "triggered": True,
                    "rule_id": f"mon_stale_{metric}",
                    "rule_version": "hc302.1",
                    "title": f"Stale or missing {metric} data",
                    "message": (
                        f"No fresh {metric} observation within the configured freshness window. "
                        "Observational monitoring only — check device permissions/sync and manufacturer alarms. "
                        "A stale reading is not treated as a current clinical value."
                    ),
                    "severity": sev,
                    "category": "freshness",
                    "metric": metric,
                    "metrics": [metric],
                    "evidence": {
                        "freshness_status": freshness,
                        "measured_at": measured_at,
                        "acquisition_mode": mode,
                        "provenance": row.get("provenance"),
                    },
                    "patient_id": patient_id,
                }
                freshness_flags.append(ev)
                evaluations.append(ev)
                # Do not evaluate absolute thresholds on stale/missing as if current
                continue

            if freshness not in {"fresh", "aging"}:
                continue

            # Skip absolute eval when unit incompatible
            if row.get("quality", {}).get("unit_compatible") is False:
                continue

            for rule in self.thresholds.get("rules") or []:
                if str(rule.get("metric")) != metric:
                    continue
                ev = self._eval_threshold(rule, row, patient_id=patient_id, freshness=freshness)
                if ev:
                    evaluations.append(ev)

        for trend in self.thresholds.get("trend_rules") or []:
            ev = self._eval_trend(trend, observations, patient_id=patient_id, now=now_ts)
            if ev:
                evaluations.append(ev)

        alert_results = []
        for ev in evaluations:
            if not ev.get("triggered"):
                continue
            alert = self.alerts.ingest_evaluation(
                ev,
                patient_id=patient_id,
                now=now_ts,
            )
            if alert:
                alert_results.append(alert)

        status = self.build_status(patient_id=patient_id, now=now_ts, trigger=trigger)
        self.store.save_monitoring_status(status)
        return {
            "ok": True,
            "patient_id": patient_id,
            "trigger": trigger,
            "evaluation_count": len(evaluations),
            "alerts_touched": len(alert_results),
            "freshness_flags": len(freshness_flags),
            "threshold_validation_errors": list(self.thresholds.get("validation_errors") or []),
            "status": status,
            "at": now_ts,
        }

    def _eval_threshold(
        self,
        rule: dict[str, Any],
        row: dict[str, Any],
        *,
        patient_id: str,
        freshness: str,
    ) -> dict[str, Any] | None:
        try:
            value = float(row.get("value"))
            threshold = float(rule.get("value"))
        except (TypeError, ValueError):
            return None
        op = str(rule.get("op") or "gt")
        triggered = False
        if op == "gt":
            triggered = value > threshold
        elif op == "gte":
            triggered = value >= threshold
        elif op == "lt":
            triggered = value < threshold
        elif op == "lte":
            triggered = value <= threshold
        if not triggered:
            return None
        return {
            "triggered": True,
            "rule_id": rule.get("rule_id"),
            "rule_version": "hc302.1",
            "title": rule.get("title"),
            "message": rule.get("message"),
            "severity": rule.get("severity") or "watch",
            "category": rule.get("category") or "monitoring",
            "metric": rule.get("metric"),
            "metrics": [rule.get("metric")],
            "evidence": {
                "value": value,
                "threshold": threshold,
                "op": op,
                "measured_at": row.get("measured_at"),
                "received_at": row.get("received_at"),
                "acquisition_mode": row.get("acquisition_mode"),
                "provenance": row.get("provenance"),
                "freshness_status": freshness,
                "source": row.get("source"),
                "emergency_routing": bool(rule.get("emergency_routing")),
            },
            "patient_id": patient_id,
        }

    def _eval_trend(
        self,
        rule: dict[str, Any],
        observations: list[dict[str, Any]],
        *,
        patient_id: str,
        now: str,
    ) -> dict[str, Any] | None:
        metric = str(rule.get("metric") or "")
        window = int(rule.get("window_minutes") or 60)
        min_points = max(3, int(rule.get("min_points") or 3))
        min_span = int(rule.get("min_span_minutes") or 5)
        delta_gt = float(rule.get("delta_gt") or 0)
        now_dt = _dt(now)
        points: list[tuple[datetime, float, str | None]] = []
        for o in observations:
            if str(o.get("metric_type") or o.get("metric")) != metric:
                continue
            if o.get("acquisition_mode") == "SIMULATED_TEST_ONLY":
                continue
            try:
                points.append(
                    (
                        _dt(str(o.get("measured_at"))),
                        float(o.get("value")),
                        o.get("observation_id"),
                    )
                )
            except (TypeError, ValueError):
                continue
        points = [p for p in points if p[0] >= now_dt - timedelta(minutes=window)]
        points.sort(key=lambda x: x[0])
        if len(points) < min_points:
            return None
        span_min = (points[-1][0] - points[0][0]).total_seconds() / 60.0
        if span_min < min_span:
            return None
        delta = points[-1][1] - points[0][1]
        if delta <= delta_gt:
            return None
        return {
            "triggered": True,
            "rule_id": rule.get("rule_id"),
            "rule_version": "hc302.1",
            "title": rule.get("title"),
            "message": rule.get("message"),
            "severity": rule.get("severity") or "watch",
            "category": rule.get("category") or "trend",
            "metric": metric,
            "metrics": [metric],
            "evidence": {
                "delta": delta,
                "delta_gt": delta_gt,
                "window_minutes": window,
                "points": len(points),
                "span_minutes": span_min,
                "from": points[0][1],
                "to": points[-1][1],
                "from_measured_at": points[0][0].isoformat().replace("+00:00", "Z"),
                "to_measured_at": points[-1][0].isoformat().replace("+00:00", "Z"),
                "observation_ids": [p[2] for p in points if p[2]],
            },
            "patient_id": patient_id,
        }

    def _doc_is_simulated(self, doc: dict[str, Any] | None) -> bool:
        if not doc:
            return False
        tags = [str(t).lower() for t in (doc.get("tags") or [])]
        am = str(doc.get("acquisition_method") or "").lower()
        prov = str(doc.get("provenance") or "").lower()
        return (
            "simulated_test_only" in tags
            or "simulated" in am
            or prov == "simulated_test_only"
        )

    def _latest_by_metric(
        self,
        observations: list[dict[str, Any]],
        *,
        patient_id: str,
    ) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}

        def _consider(row: dict[str, Any]) -> None:
            metric = str(row.get("metric_type") or row.get("metric") or "")
            if not metric:
                return
            if row.get("acquisition_mode") == "SIMULATED_TEST_ONLY":
                return
            prev = latest.get(metric)
            if not prev or str(row.get("measured_at") or "") > str(prev.get("measured_at") or ""):
                latest[metric] = row

        for o in observations:
            _consider(o)

        docs = {d.get("id"): d for d in self.store.list_documents()}
        for m in self.store.list_measurements():
            doc_id = m.get("document_id")
            doc = docs.get(doc_id) if doc_id else None
            if doc and str(doc.get("patient_id") or "default-patient") != patient_id:
                continue
            if self._doc_is_simulated(doc):
                continue
            mode = "IMPORTED"
            if doc:
                am = str(doc.get("acquisition_method") or "")
                if "live" in am.lower():
                    mode = "LIVE"
                elif "manual" in am.lower():
                    mode = "MANUAL"
                elif am.startswith("continuous_monitor:"):
                    mode = am.split(":", 1)[-1].upper() or "IMPORTED"
                if mode == "SIMULATED_TEST_ONLY":
                    continue
            row = {
                "metric_type": str(m.get("metric") or ""),
                "value": m.get("value"),
                "unit": m.get("units"),
                "measured_at": m.get("measured_at"),
                "acquisition_mode": mode,
                "measurement_id": m.get("measurement_id"),
                "provenance": (doc or {}).get("provenance"),
                "source": (doc or {}).get("source_system"),
                "quality": {
                    "unit_compatible": m.get("unit_compatible", True),
                },
            }
            _consider(row)
        return latest

    def build_status(
        self,
        *,
        patient_id: str = "default-patient",
        now: str | None = None,
        trigger: str = "status",
    ) -> dict[str, Any]:
        from backend.health_vault.monitoring.connectors.base import list_device_connectors

        now_ts = now or utc_now()
        observations = [
            o
            for o in self.store.list_observations()
            if str(o.get("patient_id") or "default-patient") == patient_id
            and o.get("acquisition_mode") != "SIMULATED_TEST_ONLY"
        ]
        latest = self._latest_by_metric(observations, patient_id=patient_id)
        latest_view = {}
        for metric, row in latest.items():
            measured_at = row.get("measured_at")
            freshness = (
                "missing"
                if not measured_at
                else self.ingestion.compute_freshness(
                    metric=metric,
                    measured_at=str(measured_at),
                    now=now_ts,
                    acquisition_mode=row.get("acquisition_mode"),
                )
            )
            mode = row.get("acquisition_mode")
            latest_view[metric] = {
                "metric": metric,
                "value": row.get("value"),
                "unit": row.get("unit") or row.get("units"),
                "measured_at": measured_at,
                "received_at": row.get("received_at"),
                "acquisition_mode": mode,
                "freshness_status": freshness,
                "source": row.get("source"),
                "provenance": row.get("provenance"),
                "trend_direction": row.get("trend_direction"),
                "is_current": freshness in {"fresh", "aging"},
                "live_vs_imported": (
                    "live"
                    if mode == "LIVE"
                    else (
                        "manual"
                        if mode == "MANUAL"
                        else (
                            "simulated_test_only"
                            if mode == "SIMULATED_TEST_ONLY"
                            else "imported_or_delayed"
                        )
                    )
                ),
            }

        connectors = list_device_connectors(include_simulated=False)
        sync_health = [
            r
            for r in self.store.list_connector_sync_health()
            if str(r.get("patient_id") or "default-patient") == patient_id
        ]
        active_alerts = self.alerts.list_alerts(patient_id=patient_id, active_only=True)

        action_required = []
        for c in connectors:
            state = str((c.get("readiness") or {}).get("state") or "")
            if state in {"permission_required", "permission_denied", "import_required", "unavailable"}:
                action_required.append(
                    {
                        "connector_id": c.get("connector_id"),
                        "state": state,
                        "action": (c.get("readiness") or {}).get("action_required"),
                    }
                )

        last_attempt = None
        last_success = None
        for row in sync_health:
            ts = row.get("updated_at") or row.get("at")
            if ts and (last_attempt is None or str(ts) > str(last_attempt)):
                last_attempt = ts
            status = str(row.get("status") or "")
            if status in {"ok", "partial"} and ts:
                if last_success is None or str(ts) > str(last_success):
                    last_success = ts

        return {
            "schema_version": "hc.monitoring_status.v1",
            "phase": "HC-302",
            "patient_id": patient_id,
            "trigger": trigger,
            "generated_at": now_ts,
            "connectors": connectors,
            "connector_sync_health": sync_health,
            "last_attempt_at": last_attempt,
            "last_successful_sync": last_success,
            "latest_reading_by_metric": latest_view,
            "active_alerts": active_alerts,
            "active_alert_count": len(active_alerts),
            "action_required": action_required,
            "background": {
                "continuous_guaranteed": False,
                "scheduler_foundation": True,
                "browser_pwa_limitation": True,
                "android_workmanager_required_for_reliable_background": True,
                "note": "Periodic sync is best-effort. Browser/PWA and OS may suspend background work. Native Android companion + WorkManager remain future requirements.",
            },
            "privacy": {
                "local_first": True,
                "log_private_values": False,
            },
            "disclaimer": (
                "Observational continuous-monitoring foundation only. Not a diagnosis. "
                "Does not replace manufacturer alarms, clinician care, or emergency services. "
                "If severe symptoms occur, seek emergency medical assistance."
            ),
        }
