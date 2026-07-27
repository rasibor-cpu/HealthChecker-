"""HC-302 Continuous Monitoring Bridge — sync connectors → ingest → evaluate."""

from __future__ import annotations

from typing import Any

from backend.health_vault.event_bus import EventBus, get_event_bus
from backend.health_vault.guardian.health_guardian import HealthGuardian
from backend.health_vault.models import utc_now
from backend.health_vault.monitoring.connectors.base import (
    list_device_connectors,
    resolve_device_connector,
)
from backend.health_vault.monitoring.ingestion import (
    MONITORING_SYNC_COMPLETED,
    MONITORING_SYNC_FAILED,
    IngestionCoordinator,
)
from backend.health_vault.monitoring.monitoring_engine import MonitoringEngine
from backend.health_vault.monitoring.privacy import redact_for_log, safe_sync_summary
from backend.health_vault.monitoring.scheduler import MonitoringScheduler
from backend.health_vault.vault_store import VaultStore

DISCLAIMER = (
    "HC-302 Continuous Monitoring is observational decision support only. "
    "It does not diagnose or prescribe. Live device access requires platform permissions "
    "and authorized connectors. Simulated data is test-only and never used as a silent production fallback."
)


class ContinuousMonitoringBridge:
    """Orchestrates connector sync, vault persistence, monitoring eval, and status."""

    def __init__(
        self,
        store: VaultStore | None = None,
        bus: EventBus | None = None,
        patient_id: str = "default-patient",
    ) -> None:
        self.store = store or VaultStore()
        self.bus = bus or get_event_bus()
        self.ingestion = IngestionCoordinator(store=self.store, bus=self.bus)
        self.engine = MonitoringEngine(store=self.store, ingestion=self.ingestion)
        self.scheduler = MonitoringScheduler(store=self.store, patient_id=patient_id)
        self.guardian = HealthGuardian(store=self.store, bus=self.bus)

    def available_connectors(self, *, include_simulated: bool = False) -> list[dict[str, Any]]:
        return list_device_connectors(include_simulated=include_simulated)

    def sync_connector(
        self,
        connector_id: str,
        *,
        patient_id: str = "default-patient",
        context: dict[str, Any] | None = None,
        allow_simulated: bool = False,
        run_guardian: bool = True,
        now: str | None = None,
    ) -> dict[str, Any]:
        now_ts = now or utc_now()
        ctx = dict(context or {})
        prior_cursor = self.ingestion.get_cursor(connector_id, patient_id=patient_id)
        try:
            connector = resolve_device_connector(connector_id)
        except ValueError as exc:
            return {"ok": False, "errors": [str(exc)], "disclaimer": DISCLAIMER}

        if not connector.production_allowed and not allow_simulated:
            summary = safe_sync_summary(
                connector_id=connector_id,
                status="rejected",
                errors=["simulated_connector_forbidden_in_production"],
            )
            self.ingestion.record_sync_health({**summary, "patient_id": patient_id, "success": False})
            self.bus.publish(MONITORING_SYNC_FAILED, redact_for_log(summary))
            return {"ok": False, **summary, "disclaimer": DISCLAIMER}

        readiness = connector.readiness(ctx)
        fetched = connector.fetch_new_observations(cursor=prior_cursor, context=ctx)
        status = str(fetched.get("status") or "")

        if status in {
            "UNAVAILABLE",
            "IMPORT_REQUIRED",
            "permission_required",
            "permission_denied",
            "error",
        }:
            summary = safe_sync_summary(
                connector_id=connector_id,
                status=status,
                errors=list(fetched.get("errors") or []),
            )
            summary["unavailable_reason"] = fetched.get("unavailable_reason")
            summary["readiness"] = readiness
            summary["patient_id"] = patient_id
            summary["success"] = False
            # Do not advance cursor on unavailable / permission / hard error
            self.ingestion.record_sync_health(summary)
            mon = self.engine.evaluate(patient_id=patient_id, now=now_ts, trigger=f"sync_{connector_id}")
            self.bus.publish(MONITORING_SYNC_FAILED, redact_for_log(summary))
            return {
                "ok": False,
                **summary,
                "monitoring": mon,
                "disclaimer": DISCLAIMER,
            }

        allow_sim = allow_simulated and connector_id == "simulated"
        ingest = self.ingestion.ingest_observations(
            list(fetched.get("observations") or []),
            connector_id=connector_id,
            patient_id=patient_id,
            allow_simulated=allow_sim,
            default_tz=ctx.get("default_tz"),
            now=now_ts,
        )

        fetch_errors = list(fetched.get("errors") or [])
        fetch_incomplete = status == "partial" or bool(fetch_errors)
        durable = bool(ingest.get("durable_success")) and not fetch_incomplete
        next_cursor = fetched.get("next_cursor") or prior_cursor
        # Advance cursor ONLY after durable success for the full batch with no connector-row errors
        cursor_advanced = False
        if durable:
            self.ingestion.save_cursor(connector_id, next_cursor, patient_id=patient_id)
            cursor_advanced = True
        else:
            next_cursor = prior_cursor

        ok = durable and str(ingest.get("status")) in {"ok", "partial"}
        # All-duplicates / full success with no fetch-row errors may advance
        if (
            str(fetched.get("status")) == "ok"
            and not fetch_errors
            and not ingest.get("errors")
            and int(ingest.get("fetched") or 0)
            == (int(ingest.get("stored") or 0) + int(ingest.get("skipped") or 0))
        ):
            ok = True
            if not cursor_advanced:
                self.ingestion.save_cursor(
                    connector_id, fetched.get("next_cursor") or prior_cursor, patient_id=patient_id
                )
                cursor_advanced = True
                next_cursor = fetched.get("next_cursor") or prior_cursor

        sync_health = {
            **safe_sync_summary(
                connector_id=connector_id,
                status=str(ingest.get("status") or ("ok" if ok else "error")),
                fetched=int(ingest.get("fetched") or 0),
                stored=int(ingest.get("stored") or 0),
                skipped=int(ingest.get("skipped") or 0),
                errors=list(ingest.get("errors") or []),
            ),
            "patient_id": patient_id,
            "readiness": readiness,
            "cursor": next_cursor if cursor_advanced else prior_cursor,
            "cursor_advanced": cursor_advanced,
            "success": ok,
        }
        self.ingestion.record_sync_health(sync_health)

        mon = None
        guardian_result = None
        # Simulated never drives clinical monitoring / guardian
        if not allow_sim:
            mon = self.engine.evaluate(patient_id=patient_id, now=now_ts, trigger=f"sync_{connector_id}")
            clinical_stored = int(ingest.get("stored") or 0) > 0 and not allow_sim
            if run_guardian and clinical_stored:
                # Shared AlertEngine rule_ids prevent duplicate absolute alerts
                guardian_result = self.guardian.evaluate(
                    patient_id=patient_id,
                    trigger=f"hc302_{connector_id}_sync",
                )

        event = MONITORING_SYNC_COMPLETED if ok else MONITORING_SYNC_FAILED
        self.bus.publish(event, redact_for_log(sync_health))
        return {
            "ok": ok,
            **sync_health,
            "ingest": {
                "stored": ingest.get("stored"),
                "skipped": ingest.get("skipped"),
                "fetched": ingest.get("fetched"),
                "errors": ingest.get("errors"),
                "durable_success": ingest.get("durable_success"),
            },
            "monitoring": mon,
            "guardian": {
                "overall_state": (
                    ((guardian_result or {}).get("status") or {}).get("overall_state")
                    if guardian_result
                    else None
                ),
                "ran": guardian_result is not None,
            },
            "disclaimer": DISCLAIMER,
        }

    def sync_all(
        self,
        *,
        patient_id: str = "default-patient",
        context: dict[str, Any] | None = None,
        allow_simulated: bool = False,
    ) -> dict[str, Any]:
        results = []
        for row in self.available_connectors(include_simulated=allow_simulated):
            cid = str(row.get("connector_id"))
            results.append(
                self.sync_connector(
                    cid,
                    patient_id=patient_id,
                    context=context,
                    allow_simulated=allow_simulated,
                )
            )
        ok_any = any(r.get("ok") for r in results)
        status = self.get_status(patient_id=patient_id)
        return {
            "ok": ok_any,
            "results": results,
            "status": status,
            "disclaimer": DISCLAIMER,
        }

    def run_scheduled_sync(
        self,
        *,
        patient_id: str = "default-patient",
        context: dict[str, Any] | None = None,
        force: bool = False,
        now: str | None = None,
    ) -> dict[str, Any]:
        # Bind scheduler to patient
        self.scheduler = MonitoringScheduler(store=self.store, patient_id=patient_id)

        def _sync() -> dict[str, Any]:
            outcomes = []
            for cid in ("health_connect", "libre"):
                outcomes.append(
                    self.sync_connector(
                        cid,
                        patient_id=patient_id,
                        context=context,
                        allow_simulated=False,
                        now=now,
                    )
                )
            any_data_success = any(bool(o.get("ok")) and bool(o.get("success")) for o in outcomes)
            hard_fail = any(str(o.get("status")) == "error" for o in outcomes)
            # UNAVAILABLE / IMPORT_REQUIRED is expected degraded — not a false success
            only_degraded = (not any_data_success) and (not hard_fail)
            return {
                "ok": any_data_success,
                "degraded": only_degraded,
                "outcomes": outcomes,
                "error": (
                    "hard_sync_failure"
                    if hard_fail
                    else ("no_connector_data" if only_degraded else None)
                ),
            }

        return self.scheduler.run_due(_sync, now=now, force=force)

    def get_status(self, patient_id: str = "default-patient") -> dict[str, Any]:
        # Always rebuild freshness on read (do not serve stale snapshot as current)
        built = self.engine.build_status(patient_id=patient_id, trigger="status_read")
        self.scheduler = MonitoringScheduler(store=self.store, patient_id=patient_id)
        built["scheduler"] = self.scheduler.status()
        built["disclaimer"] = DISCLAIMER
        self.store.save_monitoring_status(built)
        return built

    def evaluate(self, patient_id: str = "default-patient", trigger: str = "manual") -> dict[str, Any]:
        return self.engine.evaluate(patient_id=patient_id, trigger=trigger)
