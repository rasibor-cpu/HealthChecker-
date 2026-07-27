"""HC-301 Always-On Health Guardian orchestrator."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from backend.health_vault.event_bus import EventBus, get_event_bus
from backend.health_vault.guardian.alert_engine import AlertEngine, SEVERITY_RANK
from backend.health_vault.guardian.baseline_engine import BaselineEngine
from backend.health_vault.guardian.cgm_continuity import CGMContinuityGuardian
from backend.health_vault.guardian.rule_engine import ExpandedClinicalRulesEngine
from backend.health_vault.models import utc_now
from backend.health_vault.vault_store import VaultStore

OVERALL_STATES = (
    "NORMAL",
    "WATCH",
    "WARNING",
    "URGENT",
    "CRITICAL",
    "MONITORING_DEGRADED",
    "UNKNOWN",
)

KNOWN_LIMITATIONS = [
    "HC-303A adds an Android companion foundation for Health Connect delivery; live activation requires phone install and permission validation.",
    "HC-302 continuous-monitoring foundation remains the host ingestion boundary.",
    "Samsung Health / Galaxy Watch data is available only when written into Health Connect and authorized by the user.",
    "Health Connect live reads are not available in the Python vault process without the Android companion.",
    "Galaxy Watch does not measure glucose.",
    "Samsung Watch blood pressure is user-initiated, not continuous.",
    "ECG is not supported as continuous Health Connect data in HC-303A.",
    "A PWA cannot guarantee unrestricted background execution.",
    "WorkManager periodic sync is best-effort (15+ minute minimum) and not continuous.",
    "Manufacturer CGM and device alarms must remain enabled.",
    "HealthChecker+ does not replace medical care or emergency services.",
    "No caregiver/SMS/email/emergency notifications are sent off-device in HC-303A.",
    "Production sync never silently falls back to simulated readings.",
]


class HealthGuardian:
    """
    Thin orchestrator:

    event → baselines → rules → alerts → timeline → EventBus → status → audit
    """

    def __init__(
        self,
        store: VaultStore | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self.store = store or VaultStore()
        self.bus = bus or get_event_bus()
        self.baselines = BaselineEngine(self.store)
        self.cgm = CGMContinuityGuardian(self.store, bus=self.bus)
        self.rules = ExpandedClinicalRulesEngine(self.store, baseline=self.baselines)
        self.alerts = AlertEngine(
            self.store,
            cooldowns_minutes=(self.rules.config.get("cooldowns_minutes") or None),
            bus=self.bus,
        )

    def evaluate(
        self,
        *,
        patient_id: str = "default-patient",
        source_event_ids: list[str] | None = None,
        pipeline_failure: bool = False,
        now: str | None = None,
        trigger: str = "manual",
        lightweight: bool = False,
    ) -> dict[str, Any]:
        now_ts = now or utc_now()
        audit_id = str(uuid4())
        try:
            # 1-2 baselines — always rebuild so post-import state reflects new measurements
            baseline_summary = self.baselines.rebuild(patient_id=patient_id, as_of=now_ts)
            deferred_steps: list[str] = []
            # continuity + gaps (full path preferred; lightweight defers heavy continuity refresh)
            if not lightweight:
                self.cgm.detect_glucose_gap(patient_id=patient_id, now=now_ts)
                continuity = self.cgm.evaluate_continuity(patient_id=patient_id, now=now_ts)
            else:
                deferred_steps.extend(["cgm_gap_detection", "full_continuity_refresh"])
                continuity = self.store.get_cgm_continuity() or {}
                if not continuity:
                    continuity = {
                        "state": "UNKNOWN",
                        "states": ["INVENTORY_UNKNOWN"],
                        "reasons": [
                            "Lightweight post-import evaluation; full continuity deferred.",
                            "Inventory and live continuity not refreshed in this pass.",
                        ],
                        "inventory": self.cgm.get_inventory(patient_id),
                        "open_data_gaps": [],
                        "live_libre_api": False,
                    }
                else:
                    reasons = list(continuity.get("reasons") or [])
                    reasons.append("Lightweight evaluation: continuity snapshot may be stale until full evaluate.")
                    continuity = dict(continuity)
                    continuity["reasons"] = reasons
                    continuity["lightweight_stale"] = True
            latest = self._latest_by_metric(patient_id=patient_id)
            # 3 rules
            evaluations = self.rules.evaluate(
                patient_id=patient_id,
                context={
                    "continuity": continuity,
                    "pipeline_failure": pipeline_failure,
                    "latest_by_metric": latest,
                },
                now=now_ts,
            )
            # 4 alerts
            alert_results = []
            for ev in evaluations:
                saved = self.alerts.ingest_evaluation(
                    ev,
                    patient_id=patient_id,
                    source_event_ids=source_event_ids,
                    now=now_ts,
                )
                if saved:
                    alert_results.append(saved)
                    self._timeline_alert_event(saved)
            # 5-7 status
            status = self.build_status(
                patient_id=patient_id,
                now=now_ts,
                baseline_summary=baseline_summary,
                continuity=continuity,
                trigger=trigger,
                fully_evaluated=not deferred_steps,
                deferred_steps=deferred_steps,
                evaluation_mode="lightweight" if lightweight else "full",
            )
            self.store.save_guardian_status(status)
            # 8 audit
            self.store.append_guardian_audit(
                {
                    "audit_id": audit_id,
                    "at": now_ts,
                    "trigger": trigger,
                    "patient_id": patient_id,
                    "alert_count": len(alert_results),
                    "overall_state": status.get("overall_state"),
                    "pipeline_failure": pipeline_failure,
                    "lightweight": lightweight,
                    "fully_evaluated": not deferred_steps,
                    "deferred_steps": deferred_steps,
                }
            )
            self.bus.publish(
                "GuardianEvaluated",
                {
                    "audit_id": audit_id,
                    "overall_state": status.get("overall_state"),
                    "alert_count": len(alert_results),
                    "fully_evaluated": not deferred_steps,
                },
            )
            return {
                "ok": True,
                "status": status,
                "alerts": alert_results,
                "evaluations": evaluations,
                "continuity": continuity,
                "baselines": baseline_summary,
                "audit_id": audit_id,
                "fully_evaluated": not deferred_steps,
                "deferred_steps": deferred_steps,
                "evaluation_mode": "lightweight" if lightweight else "full",
                "disclaimer": KNOWN_LIMITATIONS[0],
            }
        except Exception as exc:
            degraded = {
                "ok": False,
                "overall_state": "MONITORING_DEGRADED",
                "errors": [f"{type(exc).__name__}:{exc}"],
                "evaluated_at": now_ts,
                "fully_evaluated": False,
                "known_limitations": KNOWN_LIMITATIONS,
            }
            self.store.save_guardian_status(degraded)
            self.bus.publish("GuardianEvaluationFailed", {"error": type(exc).__name__})
            try:
                self.alerts.ingest_evaluation(
                    {
                        "triggered": True,
                        "rule_id": "monitoring_pipeline_failure",
                        "rule_version": "1.0.0",
                        "title": "Monitoring pipeline failure",
                        "message": f"Guardian evaluation failed: {type(exc).__name__}",
                        "severity": "urgent",
                        "category": "system",
                        "metrics": [],
                        "evidence": {"error": type(exc).__name__},
                        "recommended_next_step": "Retry evaluation. Background PWA checks are not guaranteed.",
                    },
                    patient_id=patient_id,
                    now=now_ts,
                )
            except Exception as ingest_exc:
                degraded["alert_ingest_error"] = type(ingest_exc).__name__
            return {"ok": False, "status": degraded, "errors": degraded["errors"], "fully_evaluated": False}

    def evaluate_after_import(self, import_result: dict[str, Any]) -> dict[str, Any]:
        """Hook from ImportPipeline after successful confirmed import."""
        if not import_result or not import_result.get("ok"):
            return self.evaluate(trigger="import_failed", pipeline_failure=True, lightweight=True)
        if import_result.get("duplicate"):
            return {"ok": True, "skipped": True, "reason": "duplicate"}
        doc = import_result.get("document") or {}
        source_ids = []
        if isinstance(doc, dict) and doc.get("id"):
            source_ids.append(str(doc["id"]))
        return self.evaluate(
            patient_id=str(doc.get("patient_id") or "default-patient"),
            source_event_ids=source_ids,
            trigger="import_completed",
            lightweight=True,
        )

    def build_status(
        self,
        *,
        patient_id: str = "default-patient",
        now: str | None = None,
        baseline_summary: dict[str, Any] | None = None,
        continuity: dict[str, Any] | None = None,
        trigger: str = "status",
        background_capability: dict[str, Any] | None = None,
        fully_evaluated: bool = True,
        deferred_steps: list[str] | None = None,
        evaluation_mode: str = "full",
    ) -> dict[str, Any]:
        now_ts = now or utc_now()
        baseline_summary = baseline_summary or self.store.get_baselines() or {}
        continuity = continuity or self.store.get_cgm_continuity() or self.cgm.evaluate_continuity(patient_id)
        counts = self.alerts.active_counts(patient_id=patient_id)
        latest = self._latest_values(patient_id=patient_id)
        reasons: list[str] = []
        deferred_steps = list(deferred_steps or [])

        measurement_count = sum(
            1
            for m in self.store.list_measurements()
            if self._measurement_belongs(m, patient_id)
        )
        empty_vault = measurement_count == 0 and not counts.get("total")

        # Empty / unknown vault is UNKNOWN — never pretend NORMAL
        overall = "UNKNOWN" if empty_vault else "NORMAL"
        if empty_vault:
            reasons.append("No confirmed measurements in vault for this patient.")

        if counts.get("critical"):
            overall = "CRITICAL"
            reasons.insert(0, f"{counts['critical']} critical alert(s) active.")
        elif counts.get("urgent"):
            overall = "URGENT"
            reasons.insert(0, f"{counts['urgent']} urgent alert(s) active.")
        elif counts.get("warning"):
            if overall not in ("CRITICAL", "URGENT"):
                overall = "WARNING"
            reasons.insert(0, f"{counts['warning']} warning alert(s) active.")
        elif counts.get("watch") or counts.get("informational"):
            if overall in ("NORMAL", "UNKNOWN"):
                overall = "WATCH"
            reasons.insert(0, "Watch/informational alerts present.")

        cont_state = continuity.get("state")
        if cont_state in ("CRITICAL_SHORTAGE", "SENSOR_EXPIRED", "SIGNAL_LOSS", "DATA_PIPELINE_FAILURE"):
            if overall not in ("CRITICAL",):
                overall = "CRITICAL" if cont_state == "CRITICAL_SHORTAGE" else "URGENT"
            reasons.extend(list(continuity.get("reasons") or [])[:3])
        elif cont_state in ("SENSOR_EXPIRING", "REORDER_REQUIRED", "INVENTORY_UNKNOWN"):
            # Empty vault stays UNKNOWN (inventory unknown is expected, not a WATCH upgrade)
            if empty_vault and cont_state == "INVENTORY_UNKNOWN":
                reasons.extend(list(continuity.get("reasons") or [])[:2])
            elif overall in ("NORMAL", "UNKNOWN"):
                overall = "WATCH" if cont_state == "INVENTORY_UNKNOWN" else "WARNING"
                reasons.extend(list(continuity.get("reasons") or [])[:2])
            elif overall == "WATCH" and cont_state != "INVENTORY_UNKNOWN":
                overall = "WARNING"
                reasons.extend(list(continuity.get("reasons") or [])[:2])
            else:
                reasons.extend(list(continuity.get("reasons") or [])[:2])

        glucose_feed = self._glucose_feed_state(continuity, latest)
        if glucose_feed in ("no_data", "gap") and overall == "NORMAL":
            overall = "WATCH"
            reasons.append("No recent glucose data — no data is never treated as normal.")

        # Combined real-world failure: expiring sensor + low reserve + no glucose
        inv = continuity.get("inventory") or {}
        unused = inv.get("unused_sensor_count")
        min_res = inv.get("minimum_protected_reserve")
        reserve_breach = (
            inv.get("confidence") != "unknown"
            and unused is not None
            and min_res is not None
            and int(unused) < int(min_res)
        )
        if (
            cont_state in ("SENSOR_EXPIRING", "SENSOR_EXPIRED")
            and reserve_breach
            and glucose_feed in ("no_data", "gap")
        ):
            if overall not in ("CRITICAL",):
                overall = "URGENT"
            reasons.append(
                "Active sensor expiry risk with protected-reserve breach and no fresh glucose data."
            )

        if deferred_steps and overall == "NORMAL":
            overall = "WATCH"
            reasons.append("Evaluation incomplete; deferred steps remain: " + ", ".join(deferred_steps))

        if not fully_evaluated and overall == "NORMAL":
            overall = "WATCH"
            reasons.append("Status is not from a fully completed Guardian evaluation.")

        if not reasons:
            reasons.append("No active Guardian warnings under current rules.")

        baselines = baseline_summary.get("baselines") or {}
        ready_metrics = [k for k, v in baselines.items() if v.get("ready")]
        bg = background_capability or {
            "supported": False,
            "permission_required": True,
            "limited": True,
            "unavailable": False,
            "note": "Service worker foundation present; OS may suspend PWAs. Continuous execution is not guaranteed.",
        }

        active = continuity.get("active_sensor")
        return {
            "schema_version": "hc.guardian_status.v1",
            "patient_id": patient_id,
            "overall_state": overall,
            "reasons": reasons,
            "monitoring_active": True,
            "fully_evaluated": fully_evaluated,
            "evaluation_mode": evaluation_mode,
            "deferred_steps": deferred_steps,
            "last_evaluation_time": now_ts,
            "last_successful_data_ingestion": self._last_ingestion(),
            "latest_measurement_time_by_source": self._latest_by_source(patient_id=patient_id),
            "active_alert_count_by_severity": counts,
            "glucose_feed_state": glucose_feed,
            "cgm_continuity_state": cont_state,
            "active_sensor_time_remaining_hours": continuity.get("hours_remaining"),
            "spare_sensor_inventory": inv.get("unused_sensor_count"),
            "projected_coverage_days": inv.get("projected_coverage_days"),
            "next_reorder_deadline": inv.get("reorder_deadline"),
            "active_sensor": active,
            "latest_bp": latest.get("bp"),
            "latest_pulse": latest.get("pulse"),
            "latest_glucose": latest.get("glucose"),
            "latest_oxygen_saturation": latest.get("oxygen_saturation"),
            "latest_sleep_summary": latest.get("sleep"),
            "baseline_readiness": {
                "ready_metrics": ready_metrics,
                "ready_count": len(ready_metrics),
                "total_tracked": len(baselines),
            },
            "source_connection_availability": {
                "health_connect": False,  # HC-302 foundation present; live bridge not configured
                "health_connect_foundation": True,
                "live_libre_api": False,
                "libre_import": True,
                "samsung_live_api": False,
                "upload_parsers": True,
                "manual_entry": True,
                "continuous_monitoring_bridge": True,
            },
            "background_capability": bg,
            "known_limitations": KNOWN_LIMITATIONS,
            "trigger": trigger,
            "disclaimer": (
                "Observational safety companion only. Not a medical device claim. "
                "Does not replace manufacturer alarms or emergency care."
            ),
        }

    def get_status(self, patient_id: str = "default-patient") -> dict[str, Any]:
        status = self.store.get_guardian_status()
        if status and status.get("patient_id") == patient_id:
            return status
        return self.build_status(patient_id=patient_id)

    def _measurement_belongs(self, measurement: dict[str, Any], patient_id: str) -> bool:
        """Patient isolation: measurements inherit patient via document when needed."""
        pid = measurement.get("patient_id")
        if pid:
            return str(pid) == patient_id
        doc_id = measurement.get("document_id")
        if not doc_id:
            # Legacy rows without patient/document linkage belong to default patient only
            return patient_id == "default-patient"
        for d in self.store.list_documents():
            if d.get("id") == doc_id:
                return str(d.get("patient_id") or "default-patient") == patient_id
        return patient_id == "default-patient"

    def _latest_by_metric(self, patient_id: str = "default-patient") -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for m in self.store.list_measurements():
            if not self._measurement_belongs(m, patient_id):
                continue
            metric = str(m.get("metric") or "")
            if not metric:
                continue
            prev = out.get(metric)
            if prev is None or str(m.get("measured_at") or "") > str(prev.get("measured_at") or ""):
                out[metric] = m
        return out

    def _latest_values(self, patient_id: str = "default-patient") -> dict[str, Any]:
        by = self._latest_by_metric(patient_id=patient_id)
        glucose = by.get("glucose")
        sys = by.get("systolic") or by.get("systolic_bp")
        dia = by.get("diastolic") or by.get("diastolic_bp")
        pulse = by.get("resting_hr") or by.get("heart_rate") or by.get("average_hr") or by.get("pulse")
        spo2 = by.get("oxygen_saturation")
        sleep = by.get("sleep_score")
        bp = None
        if sys and dia:
            bp = {
                "systolic": sys.get("value"),
                "diastolic": dia.get("value"),
                "pulse": pulse.get("value") if pulse else None,
                "units": "mmHg",
                "measured_at": max(
                    str(sys.get("measured_at") or ""),
                    str(dia.get("measured_at") or ""),
                ),
            }
        return {
            "glucose": {
                "value": glucose.get("value"),
                "units": glucose.get("units"),
                "measured_at": glucose.get("measured_at"),
            }
            if glucose
            else None,
            "bp": bp,
            "pulse": {
                "value": pulse.get("value"),
                "units": pulse.get("units") or "bpm",
                "measured_at": pulse.get("measured_at"),
            }
            if pulse
            else None,
            "oxygen_saturation": {
                "value": spo2.get("value"),
                "units": spo2.get("units") or "%",
                "measured_at": spo2.get("measured_at"),
            }
            if spo2
            else None,
            "sleep": {
                "value": sleep.get("value"),
                "units": sleep.get("units"),
                "measured_at": sleep.get("measured_at"),
            }
            if sleep
            else None,
        }

    def _glucose_feed_state(self, continuity: dict[str, Any], latest: dict[str, Any]) -> str:
        if continuity.get("state") in ("SIGNAL_LOSS",):
            return "gap"
        gaps = continuity.get("open_data_gaps") or []
        if gaps:
            return "gap"
        if not latest.get("glucose"):
            return "no_data"
        return "available_upload_or_manual"

    def _last_ingestion(self) -> str | None:
        logs = self.store.import_log()
        if not logs:
            return None
        return logs[-1].get("timestamp")

    def _latest_by_source(self, patient_id: str = "default-patient") -> dict[str, str | None]:
        out: dict[str, str | None] = {}
        for d in self.store.list_documents():
            if str(d.get("patient_id") or "default-patient") != patient_id:
                continue
            src = str(d.get("source_system") or d.get("document_type") or "unknown")
            ts = d.get("measured_at") or d.get("imported_at")
            if ts and (out.get(src) is None or str(ts) > str(out.get(src))):
                out[src] = ts
        return out

    def _timeline_alert_event(self, alert: dict[str, Any]) -> None:
        self.store.append_timeline_event(
            {
                "event_id": str(uuid4()),
                "kind": "alert",
                "category": alert.get("category") or "alert",
                "measured_at": alert.get("last_detected_at") or alert.get("created_at") or utc_now(),
                "imported_at": utc_now(),
                "provenance": "health_guardian",
                "severity": alert.get("severity"),
                "summary": alert.get("title"),
                "payload": {
                    "alert_id": alert.get("alert_id"),
                    "status": alert.get("status"),
                    "rule_id": alert.get("rule_id"),
                    "patient_id": alert.get("patient_id"),
                },
                "dedupe_key": f"alert|{alert.get('alert_id')}|{alert.get('occurrence_count')}|{alert.get('status')}",
            }
        )
