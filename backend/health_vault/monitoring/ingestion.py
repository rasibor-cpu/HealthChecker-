"""HC-302 ingestion coordinator — normalize, dedupe, persist, maintain cursors."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.health_vault.event_bus import EventBus, get_event_bus
from backend.health_vault.models import MedicalDocument, create_measurement, utc_now
from backend.health_vault.monitoring.observation import (
    CanonicalObservation,
    build_observation,
    parse_timestamp,
)
from backend.health_vault.monitoring.privacy import redact_for_log, safe_sync_summary
from backend.health_vault.vault_store import VaultStore

MONITORING_INGESTED = "MonitoringObservationIngested"
MONITORING_SYNC_COMPLETED = "MonitoringSyncCompleted"
MONITORING_SYNC_FAILED = "MonitoringSyncFailed"

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "monitoring_config.json"


def load_monitoring_config(path: Path | None = None) -> dict[str, Any]:
    p = path or _CONFIG_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"schema_version": "hc.monitoring_config.v1", "freshness_windows_minutes": {}}
        return data
    except Exception:
        return {"schema_version": "hc.monitoring_config.v1", "freshness_windows_minutes": {}}


class IngestionCoordinator:
    """
    Normalize observations, idempotently persist them, and maintain connector cursors.

    Clinical values are never written into ordinary audit log detail fields.
    SIMULATED_TEST_ONLY observations are stored in the observation index only —
    they never create clinical documents/measurements and cannot feed Guardian.
    """

    def __init__(
        self,
        store: VaultStore | None = None,
        bus: EventBus | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.store = store or VaultStore()
        self.bus = bus or get_event_bus()
        self.config = config or load_monitoring_config()

    def ingest_observations(
        self,
        observations: list[dict[str, Any] | CanonicalObservation],
        *,
        connector_id: str,
        patient_id: str = "default-patient",
        allow_simulated: bool = False,
        default_tz: str | None = None,
        evaluate_freshness: bool = True,
        now: str | None = None,
    ) -> dict[str, Any]:
        now_ts = now or utc_now()
        stored: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        errors: list[str] = []

        for raw in observations or []:
            try:
                if isinstance(raw, CanonicalObservation):
                    obs = raw
                else:
                    payload = dict(raw)
                    payload.setdefault("patient_id", patient_id)
                    if allow_simulated and str(payload.get("acquisition_mode") or "").upper() == "SIMULATED_TEST_ONLY":
                        payload["allow_simulated"] = True
                    obs = build_observation(payload, default_tz=default_tz)

                if obs.acquisition_mode == "SIMULATED_TEST_ONLY" and not allow_simulated:
                    errors.append("simulated_rejected_in_production_path")
                    continue
                obs.patient_id = patient_id

                if evaluate_freshness:
                    if not obs.measured_at:
                        obs.freshness_status = "missing"
                    else:
                        obs.freshness_status = self.compute_freshness(
                            metric=obs.metric_type,
                            measured_at=obs.measured_at,
                            now=now_ts,
                            acquisition_mode=obs.acquisition_mode,
                        )

                result = self._persist_one(obs, connector_id=connector_id)
                if result.get("skipped"):
                    skipped.append(result)
                else:
                    stored.append(result)
                    self.bus.publish(
                        MONITORING_INGESTED,
                        redact_for_log(
                            {
                                "observation_id": obs.observation_id,
                                "metric_type": obs.metric_type,
                                "acquisition_mode": obs.acquisition_mode,
                                "freshness_status": obs.freshness_status,
                                "connector_id": connector_id,
                                "measured_at": obs.measured_at,
                                "patient_id": patient_id,
                            }
                        ),
                    )
            except ValueError as exc:
                errors.append(str(exc))
            except Exception as exc:  # pragma: no cover
                errors.append(f"ingest_error:{type(exc).__name__}")

        summary = safe_sync_summary(
            connector_id=connector_id,
            status="ok" if not errors else ("partial" if stored or skipped else "error"),
            fetched=len(observations or []),
            stored=len(stored),
            skipped=len(skipped),
            errors=errors,
        )
        return {
            **summary,
            "stored_observations": stored,
            "skipped_observations": skipped,
            "at": now_ts,
            "durable_success": not errors and (len(stored) + len(skipped)) == len(observations or []),
        }

    def _persist_one(self, obs: CanonicalObservation, *, connector_id: str) -> dict[str, Any]:
        existing = self.store.get_observation_by_fingerprint(
            obs.fingerprint or "",
            patient_id=obs.patient_id,
        )
        if existing:
            return {
                "skipped": True,
                "reason": "duplicate_fingerprint",
                "observation_id": existing.get("observation_id"),
                "fingerprint": obs.fingerprint,
            }

        row = obs.to_dict()
        row["connector_id"] = connector_id
        row["ingested_at"] = utc_now()

        # Simulated: observation index only — never clinical vault measurements
        if obs.acquisition_mode == "SIMULATED_TEST_ONLY":
            row["clinical_persist"] = False
            saved = self.store.upsert_observation(row)
            return {"skipped": False, "observation": saved, "clinical_persist": False}

        content = json.dumps(
            {
                "observation_id": obs.observation_id,
                "fingerprint": obs.fingerprint,
                "metric_type": obs.metric_type,
                "measured_at": obs.measured_at,
                "received_at": obs.received_at,
                "acquisition_mode": obs.acquisition_mode,
                "source": obs.source,
                "source_record_id": obs.source_record_id,
                "connector_id": connector_id,
                "patient_id": obs.patient_id,
                "provenance": obs.provenance,
            },
            sort_keys=True,
        ).encode("utf-8")

        doc = MedicalDocument(
            id=str(uuid4()),
            patient_id=obs.patient_id,
            document_type="continuous_monitoring_observation",
            source_system=str(obs.source or connector_id),
            acquisition_method=f"continuous_monitor:{obs.acquisition_mode.lower()}",
            original_filename=f"{connector_id}_{obs.metric_type}_{obs.observation_id}.json",
            measured_at=obs.measured_at,
            provenance=obs.provenance or "continuous_monitoring",
            mime_type="application/json",
            tags=["hc302", "continuous_monitoring", obs.acquisition_mode.lower()],
            parser_version="hc302.ingestion.v1",
            parser_confidence=obs.confidence,
            sha256=VaultStore.sha256_bytes(content),
        )
        measurement = create_measurement(
            metric=obs.metric_type,
            value=obs.value if obs.value is not None else obs.text_value,
            units=obs.unit,
            measured_at=obs.measured_at,
            confidence=obs.confidence,
            document_id=doc.id,
        )
        stored_doc = self.store.store(
            document=doc,
            measurements=[measurement],
            content=content,
            interpretation=None,
            parser={"version": "hc302.ingestion.v1", "connector_id": connector_id},
            import_meta={
                "source": "hc302_continuous_monitoring",
                "acquisition_mode": obs.acquisition_mode,
                "fingerprint": obs.fingerprint,
            },
        )

        doc_row = stored_doc.get("document") or {}
        if doc_row.get("duplicate_of") or (stored_doc.get("import_record") or {}).get("duplicate_content"):
            # Still record observation linkage for idempotent cursor progress
            row["document_id"] = doc_row.get("duplicate_of") or doc_row.get("id")
            row["measurement_id"] = measurement.measurement_id
            saved = self.store.upsert_observation(row)
            return {
                "skipped": True,
                "reason": "duplicate_document_content",
                "fingerprint": obs.fingerprint,
                "observation": saved,
            }

        row["document_id"] = doc_row.get("id") or doc.id
        row["measurement_id"] = measurement.measurement_id
        row["clinical_persist"] = True
        saved = self.store.upsert_observation(row)
        return {"skipped": False, "observation": saved, "clinical_persist": True}

    def compute_freshness(
        self,
        *,
        metric: str,
        measured_at: str | None,
        now: str | None = None,
        acquisition_mode: str | None = None,
    ) -> str:
        if acquisition_mode == "UNAVAILABLE":
            return "unavailable"
        if not measured_at:
            return "missing"
        now_ts = parse_timestamp(now or utc_now())
        measured = parse_timestamp(measured_at)
        from datetime import datetime

        def _dt(s: str) -> datetime:
            text = s[:-1] + "+00:00" if s.endswith("Z") else s
            return datetime.fromisoformat(text)

        age_min = (_dt(now_ts) - _dt(measured)).total_seconds() / 60.0
        windows = self.config.get("freshness_windows_minutes") or {}
        fresh_window = float(windows.get(metric) or windows.get("default") or 360)
        stale_mult = float(self.config.get("stale_escalation_multiplier") or 3)
        if age_min <= fresh_window:
            return "fresh"
        if age_min <= fresh_window * stale_mult:
            return "aging"
        return "stale"

    def save_cursor(self, connector_id: str, cursor: dict[str, Any], patient_id: str = "default-patient") -> dict[str, Any]:
        return self.store.save_connector_cursor(
            connector_id,
            {
                "connector_id": connector_id,
                "patient_id": patient_id,
                "cursor": dict(cursor or {}),
                "updated_at": utc_now(),
            },
        )

    def get_cursor(self, connector_id: str, patient_id: str = "default-patient") -> dict[str, Any]:
        row = self.store.get_connector_cursor(connector_id, patient_id=patient_id)
        return dict((row or {}).get("cursor") or {})

    def record_sync_health(self, payload: dict[str, Any]) -> dict[str, Any]:
        safe = redact_for_log(dict(payload or {}))
        safe.pop("stored_observations", None)
        safe.pop("observations", None)
        safe["updated_at"] = utc_now()
        return self.store.save_connector_sync_health(safe)
