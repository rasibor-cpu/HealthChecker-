"""HC-302 canonical observation model."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from backend.health_vault.metric_normalization import canonicalize_metric, normalize_measurement
from backend.health_vault.models import utc_now

ACQUISITION_MODES = (
    "LIVE",
    "DELAYED",
    "MANUAL",
    "IMPORTED",
    "SIMULATED_TEST_ONLY",
    "STALE",
    "UNAVAILABLE",
)

FRESHNESS_STATUSES = (
    "fresh",
    "aging",
    "stale",
    "missing",
    "unknown",
    "unavailable",
)

CONNECTOR_METRIC_ALIASES = {
    "hr": "heart_rate",
    "pulse": "heart_rate",
    "resting_heart_rate": "resting_hr",
    "spo2": "oxygen_saturation",
    "blood_oxygen": "oxygen_saturation",
    "spo₂": "oxygen_saturation",
    "systolic": "systolic_bp",
    "diastolic": "diastolic_bp",
    "bp_systolic": "systolic_bp",
    "bp_diastolic": "diastolic_bp",
    "steps": "steps",
    "step_count": "steps",
    "activity": "activity_minutes",
    "exercise": "exercise_minutes",
    "sleep": "sleep_duration",
    "ecg": "ecg_result",
    "glucose_mg_dl": "glucose",
    "cgm_glucose": "glucose",
}


def parse_timestamp(
    value: Any,
    default_tz: str | None = None,
    *,
    strip_microseconds: bool = True,
) -> str:
    """
    Normalize a timestamp to timezone-aware UTC ISO-8601 ending in Z.

    Naive timestamps are interpreted in default_tz when provided, else UTC.
    measured_at display/storage may strip microseconds; fingerprinting can keep them.
    """
    if value is None or value == "":
        raise ValueError("measured_at_required")
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"invalid_timestamp:{value}") from exc
    if dt.tzinfo is None:
        if default_tz:
            dt = dt.replace(tzinfo=ZoneInfo(default_tz))
        else:
            dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    if strip_microseconds:
        dt = dt.replace(microsecond=0)
    return dt.isoformat().replace("+00:00", "Z")


def observation_fingerprint(
    *,
    patient_id: str,
    source: str,
    source_record_id: str | None,
    metric: str,
    measured_at: str,
    value: Any,
    units: str | None,
) -> str:
    """Stable idempotency key — includes patient_id so patients cannot collide."""
    material = {
        "patient_id": str(patient_id or "default-patient"),
        "source": str(source or ""),
        "source_record_id": str(source_record_id or ""),
        "metric": str(metric or ""),
        "measured_at": str(measured_at or ""),
        "value": value,
        "units": str(units or ""),
    }
    raw = json.dumps(material, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass
class CanonicalObservation:
    """Canonical continuous-monitoring observation (FHIR Observation–oriented)."""

    observation_id: str = field(default_factory=lambda: str(uuid4()))
    metric_type: str = "unknown"
    value: Any = None
    text_value: str | None = None
    unit: str | None = None
    measured_at: str | None = None
    received_at: str = field(default_factory=utc_now)
    source: str = "unknown"
    source_record_id: str | None = None
    acquisition_mode: str = "IMPORTED"
    freshness_status: str = "unknown"
    confidence: float | None = None
    quality: dict[str, Any] = field(default_factory=dict)
    provenance: str | None = None
    device: dict[str, Any] = field(default_factory=dict)
    patient_id: str = "default-patient"
    connector_id: str | None = None
    trend_direction: str | None = None
    fingerprint: str | None = None
    notes: list[str] = field(default_factory=list)
    schema_version: str = "hc.observation.v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.metric_type or self.metric_type == "unknown":
            errors.append("metric_type_required")
        if self.acquisition_mode not in ACQUISITION_MODES:
            errors.append(f"invalid_acquisition_mode:{self.acquisition_mode}")
        if self.freshness_status not in FRESHNESS_STATUSES:
            errors.append(f"invalid_freshness_status:{self.freshness_status}")
        if self.acquisition_mode == "SIMULATED_TEST_ONLY" and self.provenance != "simulated_test_only":
            errors.append("simulated_requires_simulated_provenance")
        if self.value is None and not self.text_value:
            errors.append("value_or_text_required")
        if not self.measured_at:
            errors.append("measured_at_required")
        else:
            try:
                parse_timestamp(self.measured_at)
            except ValueError:
                errors.append("measured_at_not_timezone_aware_iso")
        if not self.received_at:
            errors.append("received_at_required")
        if self.confidence is not None and not (0.0 <= float(self.confidence) <= 1.0):
            errors.append("confidence_out_of_range")
        if self.quality.get("unit_compatible") is False:
            errors.append("unit_incompatible")
        return errors


def normalize_metric_type(metric: str | None) -> str:
    key = str(metric or "unknown").strip().lower()
    if key in CONNECTOR_METRIC_ALIASES:
        key = CONNECTOR_METRIC_ALIASES[key]
    return canonicalize_metric(key)


def build_observation(raw: dict[str, Any], *, default_tz: str | None = None) -> CanonicalObservation:
    """Validate and normalize a raw connector observation into the canonical model."""
    raw = dict(raw or {})
    metric = normalize_metric_type(raw.get("metric_type") or raw.get("metric"))
    # Preserve microsecond precision for fingerprint when source_record_id absent
    measured_at_precise = parse_timestamp(
        raw.get("measured_at") or raw.get("timestamp"),
        default_tz=default_tz,
        strip_microseconds=False,
    )
    measured_at = parse_timestamp(measured_at_precise, strip_microseconds=True)
    # received_at is ingest time — never confuse with measured_at
    if raw.get("received_at"):
        received_at = parse_timestamp(raw.get("received_at"))
    else:
        received_at = utc_now()
    mode = str(raw.get("acquisition_mode") or "IMPORTED").upper()
    if mode not in ACQUISITION_MODES:
        raise ValueError(f"invalid_acquisition_mode:{mode}")
    if mode == "SIMULATED_TEST_ONLY" and not raw.get("allow_simulated"):
        raise ValueError("simulated_test_data_forbidden_in_production_path")

    value = raw.get("value")
    text_value = raw.get("text_value") or raw.get("text")
    unit = raw.get("unit") or raw.get("units")
    quality = dict(raw.get("quality") or {})

    if value is not None and metric not in {"ecg_result", "heart_rhythm", "activity_type"}:
        normalized = normalize_measurement(
            {"metric": metric, "value": value, "units": unit, "measured_at": measured_at}
        )
        metric = str(normalized.get("metric") or metric)
        value = normalized.get("value")
        unit = normalized.get("units") or unit
        quality["unit_compatible"] = normalized.get("unit_compatible", True)
        quality["normalization_notes"] = normalized.get("normalization_notes") or []
        quality["normalization_version"] = normalized.get("normalization_version")
        if quality.get("unit_compatible") is False:
            raise ValueError(f"unit_incompatible:{metric}:{raw.get('unit') or raw.get('units')}")

    source = str(raw.get("source") or raw.get("connector_id") or "unknown")
    source_record_id = raw.get("source_record_id")
    patient_id = str(raw.get("patient_id") or "default-patient")
    # Prefer precise timestamp in fingerprint when no device record id (avoid same-second collapse)
    fp_measured = measured_at_precise if not source_record_id else measured_at
    fingerprint = raw.get("fingerprint") or observation_fingerprint(
        patient_id=patient_id,
        source=source,
        source_record_id=str(source_record_id) if source_record_id is not None else None,
        metric=metric,
        measured_at=fp_measured,
        value=value if value is not None else text_value,
        units=unit,
    )

    obs = CanonicalObservation(
        observation_id=str(raw.get("observation_id") or uuid4()),
        metric_type=metric,
        value=value,
        text_value=str(text_value) if text_value is not None else None,
        unit=unit,
        measured_at=measured_at,
        received_at=received_at,
        source=source,
        source_record_id=str(source_record_id) if source_record_id is not None else None,
        acquisition_mode=mode,
        freshness_status=str(raw.get("freshness_status") or "unknown"),
        confidence=float(raw["confidence"]) if raw.get("confidence") is not None else None,
        quality=quality,
        provenance=raw.get("provenance"),
        device=dict(raw.get("device") or {}),
        patient_id=patient_id,
        connector_id=raw.get("connector_id") or source,
        trend_direction=raw.get("trend_direction"),
        fingerprint=fingerprint,
        notes=list(raw.get("notes") or []),
    )
    if mode == "SIMULATED_TEST_ONLY":
        obs.provenance = "simulated_test_only"
    errors = obs.validate()
    if errors:
        raise ValueError(";".join(errors))
    return obs
