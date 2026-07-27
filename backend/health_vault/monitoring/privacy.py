"""Privacy-safe logging helpers for HC-302 monitoring."""

from __future__ import annotations

from typing import Any

# Keys that must never appear with raw clinical values in ordinary logs.
_REDACT_KEYS = {
    "value",
    "numeric_value",
    "text_value",
    "original_value",
    "glucose",
    "heart_rate",
    "systolic",
    "diastolic",
    "spo2",
    "oxygen_saturation",
    "weight",
    "source_record_id",
    "serial",
    "device_serial",
}


def redact_for_log(payload: Any) -> Any:
    """
    Return a log-safe copy of a payload.

    Removes/redacts private clinical values and source record identifiers.
    Safe for ordinary application logs — never silent-pass raw readings.
    """
    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        for key, val in payload.items():
            lk = str(key).lower()
            if lk in _REDACT_KEYS or lk.endswith("_value") or "secret" in lk or "token" in lk:
                out[key] = "[redacted]"
            elif lk in {"measured_at", "received_at", "metric", "units", "acquisition_mode", "freshness_status", "source", "connector_id", "status"}:
                out[key] = val
            else:
                out[key] = redact_for_log(val)
        return out
    if isinstance(payload, list):
        return [redact_for_log(v) for v in payload]
    return payload


def safe_sync_summary(
    *,
    connector_id: str,
    status: str,
    fetched: int = 0,
    stored: int = 0,
    skipped: int = 0,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    """Compact sync summary without observation values."""
    return {
        "connector_id": connector_id,
        "status": status,
        "fetched": int(fetched),
        "stored": int(stored),
        "skipped": int(skipped),
        "error_count": len(errors or []),
        "errors": list(errors or [])[:8],
    }
