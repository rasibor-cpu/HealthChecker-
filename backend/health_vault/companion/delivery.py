"""HC-303A companion observation delivery into HC-302 IngestionCoordinator."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.health_vault.companion.pairing import CompanionPairingService
from backend.health_vault.companion.security import (
    ALLOWED_ACQUISITION_MODES,
    MAX_DEVICE_META_CHARS,
    MAX_OBSERVATIONS_PER_BATCH,
    MAX_PAYLOAD_BYTES,
    MAX_STRING_FIELD_CHARS,
    SENT_AT_SKEW_SECONDS,
    SUPPORTED_COMPANION_METRICS,
    UNSUPPORTED_METRICS,
    estimate_payload_bytes,
    payload_fingerprint,
    redact_companion_log,
    truncate_field,
)
from backend.health_vault.event_bus import EventBus, get_event_bus
from backend.health_vault.models import utc_now
from backend.health_vault.monitoring.bridge import ContinuousMonitoringBridge
from backend.health_vault.monitoring.ingestion import IngestionCoordinator
from backend.health_vault.monitoring.observation import normalize_metric_type
from backend.health_vault.vault_store import VaultStore

COMPANION_BATCH_ACCEPTED = "CompanionBatchAccepted"
COMPANION_BATCH_REJECTED = "CompanionBatchRejected"


def _parse_ts(value: str) -> datetime:
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(text).astimezone(timezone.utc)


class CompanionDeliveryService:
    """
    Authenticated batch delivery from the Android companion.

    Validates schema, rejects simulated/unsupported metrics, prevents replay,
    and persists via HC-302 IngestionCoordinator only.
    """

    def __init__(
        self,
        store: VaultStore | None = None,
        bus: EventBus | None = None,
        pairing: CompanionPairingService | None = None,
    ) -> None:
        self.store = store or VaultStore()
        self.bus = bus or get_event_bus()
        self.pairing = pairing or CompanionPairingService(store=self.store, bus=self.bus)
        self.ingestion = IngestionCoordinator(store=self.store, bus=self.bus)
        self.bridge = ContinuousMonitoringBridge(store=self.store, bus=self.bus)

    def deliver(
        self,
        body: dict[str, Any],
        *,
        authorization: str | None,
        now: str | None = None,
        require_tls_hint: bool = True,
        tls_enabled: bool | None = None,
        local_dev: bool = False,
        content_length: int | None = None,
    ) -> dict[str, Any]:
        now_ts = now or utc_now()
        device = self.pairing.authenticate(authorization)
        if not device:
            self.bus.publish(COMPANION_BATCH_REJECTED, {"reason": "authentication_failure"})
            return {"ok": False, "status": "unauthorized", "errors": ["authentication_failure"]}

        if device.get("revoked"):
            return {"ok": False, "status": "revoked", "errors": ["device_revoked"]}

        if require_tls_hint and not local_dev and tls_enabled is False:
            return {
                "ok": False,
                "status": "tls_required",
                "errors": ["tls_required_outside_local_dev"],
            }

        if content_length is not None and content_length > MAX_PAYLOAD_BYTES:
            return {
                "ok": False,
                "status": "payload_too_large",
                "errors": [f"payload_exceeds_{MAX_PAYLOAD_BYTES}_bytes"],
            }

        body = dict(body or {})
        try:
            size = estimate_payload_bytes(body)
        except Exception:
            return {"ok": False, "status": "malformed", "errors": ["payload_unreadable"]}
        if size > MAX_PAYLOAD_BYTES:
            return {
                "ok": False,
                "status": "payload_too_large",
                "errors": [f"payload_exceeds_{MAX_PAYLOAD_BYTES}_bytes"],
            }

        batch_id = str(body.get("batch_id") or "").strip()
        nonce = str(body.get("nonce") or "").strip()
        sent_at = str(body.get("sent_at") or "").strip()
        if not batch_id or not nonce or not sent_at:
            return {
                "ok": False,
                "status": "malformed",
                "errors": ["batch_id_nonce_sent_at_required"],
            }
        if len(batch_id) > MAX_STRING_FIELD_CHARS or len(nonce) > MAX_STRING_FIELD_CHARS:
            return {"ok": False, "status": "malformed", "errors": ["field_too_large"]}

        # Reject client-injected patient identity — always bind to authenticated device.
        if "patient_id" in body and body.get("patient_id") not in (None, "", device.get("patient_id")):
            return {
                "ok": False,
                "status": "forbidden",
                "errors": ["patient_id_injection_rejected"],
            }

        try:
            sent_dt = _parse_ts(sent_at)
            now_dt = _parse_ts(now_ts)
            skew = abs((now_dt - sent_dt).total_seconds())
            if skew > SENT_AT_SKEW_SECONDS:
                return {
                    "ok": False,
                    "status": "clock_skew",
                    "errors": [
                        f"sent_at_outside_{SENT_AT_SKEW_SECONDS}s_window",
                        "check_device_clock_and_retry",
                    ],
                }
        except Exception:
            return {
                "ok": False,
                "status": "malformed",
                "errors": ["sent_at_unparseable"],
            }

        observations = body.get("observations")
        if not isinstance(observations, list):
            return {"ok": False, "status": "malformed", "errors": ["observations_must_be_list"]}
        if len(observations) > MAX_OBSERVATIONS_PER_BATCH:
            return {
                "ok": False,
                "status": "payload_too_large",
                "errors": [f"observation_count_exceeds_{MAX_OBSERVATIONS_PER_BATCH}"],
            }

        payload_fp = payload_fingerprint(observations, nonce=nonce)
        reservation = self.store.reserve_companion_batch(
            batch_id=batch_id,
            nonce=nonce,
            device_id=str(device.get("device_id")),
            payload_fp=payload_fp,
            now=now_ts,
        )
        if reservation["status"] == "duplicate":
            prior = reservation["ack"]
            return {
                "ok": True,
                "status": "duplicate_ack",
                "batch_id": batch_id,
                "idempotent": True,
                "accepted": prior.get("accepted") or [],
                "rejected": prior.get("rejected") or [],
                "cursor": prior.get("cursor"),
                "cursor_advanced": prior.get("cursor_advanced"),
                "stored": prior.get("stored") or 0,
                "skipped": prior.get("skipped") or 0,
            }
        if reservation["status"] == "conflict":
            return {
                "ok": False,
                "status": "replay_conflict",
                "errors": [reservation.get("reason") or "batch_conflict"],
            }
        if reservation["status"] == "in_flight":
            return {
                "ok": False,
                "status": "in_flight",
                "errors": ["batch_in_progress_retry_later"],
            }

        # Patient identity from authenticated device only
        patient_id = str(device.get("patient_id") or "default-patient")
        validated: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []

        for idx, raw in enumerate(observations):
            if not isinstance(raw, dict):
                rejected.append({"index": idx, "errors": ["observation_not_object"]})
                continue
            row = dict(raw)
            try:
                for field in ("observation_id", "source_record_id", "metric_type", "unit", "measured_at"):
                    if field in row and row[field] is not None:
                        truncate_field(row[field], MAX_STRING_FIELD_CHARS)
                if row.get("text_value") is not None:
                    truncate_field(row.get("text_value"), MAX_STRING_FIELD_CHARS)
            except ValueError as exc:
                rejected.append({"index": idx, "errors": [str(exc)]})
                continue

            # Reject client patient_id on observation rows
            if "patient_id" in row and row.get("patient_id") not in (None, "", patient_id):
                rejected.append({"index": idx, "errors": ["patient_id_injection_rejected"]})
                continue

            mode = str(row.get("acquisition_mode") or "DELAYED").upper()
            if mode == "SIMULATED_TEST_ONLY" or row.get("allow_simulated"):
                rejected.append({"index": idx, "errors": ["simulated_forbidden"]})
                continue
            if mode not in ALLOWED_ACQUISITION_MODES:
                rejected.append({"index": idx, "errors": [f"invalid_acquisition_mode:{mode}"]})
                continue
            metric = normalize_metric_type(row.get("metric_type") or row.get("metric"))
            if metric in UNSUPPORTED_METRICS:
                rejected.append(
                    {
                        "index": idx,
                        "errors": [
                            "ecg_unsupported_in_hc303a",
                            "ECG is not represented as continuous Health Connect data in this phase.",
                        ],
                    }
                )
                continue
            if metric not in SUPPORTED_COMPANION_METRICS:
                rejected.append({"index": idx, "errors": [f"unsupported_metric:{metric}"]})
                continue
            if not row.get("source_record_id") and not row.get("observation_id"):
                rejected.append({"index": idx, "errors": ["source_record_id_or_observation_id_required"]})
                continue
            if not row.get("measured_at"):
                rejected.append({"index": idx, "errors": ["measured_at_required"]})
                continue

            # BP must remain DELAYED explicit measurement — never LIVE
            if metric in {"systolic_bp", "diastolic_bp"} and mode == "LIVE":
                mode = "DELAYED"
                row["device"] = dict(row.get("device") or {})
                row["device"]["measurement_kind"] = "explicit_supported_measurement"
                row["device"]["live_coerced_to_delayed"] = "true"

            row["metric_type"] = metric
            row["acquisition_mode"] = mode
            row["patient_id"] = patient_id
            row["connector_id"] = "health_connect"
            row.setdefault("source", "health_connect_companion")
            row.setdefault("provenance", "health_connect_sync")
            device_meta = dict(row.get("device") or {})
            # Bound device metadata strings
            safe_meta: dict[str, Any] = {}
            for k, v in list(device_meta.items())[:20]:
                try:
                    safe_meta[str(k)[:64]] = truncate_field(v, MAX_DEVICE_META_CHARS)
                except ValueError:
                    continue
            safe_meta["companion_device_id"] = device.get("device_id")
            if metric in {"systolic_bp", "diastolic_bp"}:
                safe_meta.setdefault("measurement_kind", "explicit_supported_measurement")
            row["device"] = safe_meta
            validated.append(row)

        if not validated and rejected:
            ack = {
                "ok": False,
                "status": "rejected",
                "batch_id": batch_id,
                "nonce": nonce,
                "device_id": device.get("device_id"),
                "payload_fp": payload_fp,
                "accepted": [],
                "rejected": rejected,
                "stored": 0,
                "skipped": 0,
                "errors": ["no_valid_observations"],
            }
            self.store.save_companion_batch_ack(ack)
            self.bus.publish(COMPANION_BATCH_REJECTED, redact_companion_log({"batch_id": batch_id}))
            return ack

        prior_cursor = self.ingestion.get_cursor("health_connect", patient_id=patient_id)
        proposed_cursor = body.get("next_cursor") or body.get("cursor") or prior_cursor

        try:
            ingest = self.ingestion.ingest_observations(
                validated,
                connector_id="health_connect",
                patient_id=patient_id,
                allow_simulated=False,
                default_tz=body.get("default_tz"),
                now=now_ts,
            )
        except Exception:
            # Do not ack success; leave reservation replaceable by failed ack
            fail = {
                "ok": False,
                "status": "ingest_exception",
                "batch_id": batch_id,
                "nonce": nonce,
                "device_id": device.get("device_id"),
                "payload_fp": payload_fp,
                "accepted": [],
                "rejected": rejected,
                "stored": 0,
                "skipped": 0,
                "errors": ["ingest_failed"],
            }
            self.store.save_companion_batch_ack(fail)
            self.bus.publish(COMPANION_BATCH_REJECTED, redact_companion_log({"batch_id": batch_id}))
            return fail

        durable = bool(ingest.get("durable_success"))
        cursor_advanced = False
        if durable and not rejected:
            self.ingestion.save_cursor("health_connect", proposed_cursor, patient_id=patient_id)
            cursor_advanced = True

        # Mark observation keys seen only after durable acceptance
        if durable:
            for row in validated:
                obs_key = str(row.get("observation_id") or row.get("source_record_id"))
                self.store.mark_companion_observation_seen(device.get("device_id"), obs_key, batch_id)

        device_upd = dict(device)
        device_upd["last_seen_at"] = now_ts
        self.store.upsert_companion_device(device_upd)

        sync_health = {
            "connector_id": "health_connect",
            "patient_id": patient_id,
            "status": "ok" if durable and not rejected else ("partial" if durable else "error"),
            "success": durable and not rejected,
            "fetched": len(validated),
            "stored": ingest.get("stored"),
            "skipped": ingest.get("skipped"),
            "errors": list(ingest.get("errors") or []),
            "companion_device_id": device.get("device_id"),
            "batch_id": batch_id,
            "cursor_advanced": cursor_advanced,
        }
        self.ingestion.record_sync_health(sync_health)

        mon = None
        if int(ingest.get("stored") or 0) > 0:
            mon = self.bridge.evaluate(patient_id=patient_id, trigger="hc303a_companion_delivery")

        self.store.save_companion_status(
            {
                "schema_version": "hc.companion_status.v1",
                "device_id": device.get("device_id"),
                "last_attempt_at": now_ts,
                "last_success_at": now_ts if durable else None,
                "last_batch_id": batch_id,
                "health_connect": body.get("health_connect_status") or {},
                "permissions": body.get("permissions") or {},
                "workmanager": body.get("workmanager") or {},
                "queued_observations": body.get("queued_observations"),
                "delivery_error": None if durable else (ingest.get("errors") or ["ingest_failed"]),
            }
        )
        if durable:
            device_upd["last_success_at"] = now_ts
            self.store.upsert_companion_device(device_upd)

        ack = {
            "ok": durable,
            "status": "accepted" if durable and not rejected else ("partial" if durable else "rejected"),
            "batch_id": batch_id,
            "nonce": nonce,
            "device_id": device.get("device_id"),
            "payload_fp": payload_fp,
            "idempotent": False,
            "accepted": [
                str(r.get("observation_id") or r.get("source_record_id")) for r in validated
            ],
            "rejected": rejected,
            "stored": ingest.get("stored"),
            "skipped": ingest.get("skipped"),
            "errors": ingest.get("errors") or [],
            "cursor": proposed_cursor if cursor_advanced else prior_cursor,
            "cursor_advanced": cursor_advanced,
            "monitoring_ran": mon is not None,
            "disclaimer": (
                "Observational companion delivery only. Not a diagnosis. "
                "Blood pressure is not continuously measured. ECG is unsupported in HC-303A."
            ),
        }
        self.store.save_companion_batch_ack(ack)
        event = COMPANION_BATCH_ACCEPTED if durable else COMPANION_BATCH_REJECTED
        self.bus.publish(
            event,
            redact_companion_log(
                {
                    "batch_id": batch_id,
                    "device_id": device.get("device_id"),
                    "stored": ingest.get("stored"),
                    "skipped": ingest.get("skipped"),
                    "cursor_advanced": cursor_advanced,
                }
            ),
        )
        return ack

    def status(self, *, authorization: str | None = None) -> dict[str, Any]:
        device = self.pairing.authenticate(authorization) if authorization else None
        companion_status = (
            self.store.get_companion_status(device_id=str(device.get("device_id")))
            if device
            else {}
        )
        # Unauthenticated status is privacy-safe summary only — no device list / sync detail
        if not device:
            devices = self.pairing.list_devices(include_revoked=False)
            return {
                "schema_version": "hc.companion_host_status.v1",
                "phase": "HC-303A",
                "paired_device_count": len(devices),
                "authenticated_device": None,
                "companion_status": {},
                "health_connect_host_note": (
                    "Host process cannot read Health Connect directly. "
                    "Android companion must obtain permissions and deliver observations."
                ),
                "background_limitations": {
                    "exact_timing_guaranteed": False,
                    "minimum_periodic_interval_minutes": 15,
                    "continuous_execution_guaranteed": False,
                },
                "disclaimer": (
                    "HC-303A is an Android companion foundation. "
                    "Production-live monitoring requires install, permission grant, and device validation."
                ),
            }

        return {
            "schema_version": "hc.companion_host_status.v1",
            "phase": "HC-303A",
            "paired_device_count": 1,
            "devices": [
                {
                    "device_id": device.get("device_id"),
                    "display_name": device.get("display_name"),
                    "platform": device.get("platform"),
                    "paired_at": device.get("paired_at"),
                    "last_seen_at": device.get("last_seen_at"),
                    "revoked": bool(device.get("revoked")),
                }
            ],
            "authenticated_device": {
                "device_id": device.get("device_id"),
                "display_name": device.get("display_name"),
                "last_seen_at": device.get("last_seen_at"),
                "revoked": bool(device.get("revoked")),
            },
            "companion_status": companion_status,
            "health_connect_host_note": (
                "Host process cannot read Health Connect directly. "
                "Android companion must obtain permissions and deliver observations."
            ),
            "background_limitations": {
                "exact_timing_guaranteed": False,
                "minimum_periodic_interval_minutes": 15,
                "continuous_execution_guaranteed": False,
            },
            "disclaimer": (
                "HC-303A is an Android companion foundation. "
                "Production-live monitoring requires install, permission grant, and device validation."
            ),
        }
