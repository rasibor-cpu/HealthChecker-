"""HC323 — freshness path diagnostics (refresh vs sync vs latest measurement).

Does not inspect the live Android Health Connect database. Reports the
boundaries this process can see: companion status, vault observations,
API-selected latest values.
"""

from __future__ import annotations

from typing import Any

from backend.health_vault.health_snapshot import compute_freshness
from backend.health_vault.metric_normalization import canonicalize_metric
from backend.health_vault.models import utc_now

FRESHNESS_METRICS = (
    "heart_rate",
    "steps",
    "oxygen_saturation",
    "sleep_duration",
    "systolic_bp",
    "diastolic_bp",
    "glucose_capillary",
    "glucose_cgm_interstitial",
)


def _is_health_connect(row: dict[str, Any]) -> bool:
    source = str(row.get("source") or row.get("connector_id") or "").lower()
    return "health_connect" in source or row.get("connector_id") == "health_connect"


def _row_patient(row: dict[str, Any]) -> str:
    return str(row.get("patient_id") or "default-patient")


def build_freshness_path(
    store: Any,
    patient_id: str,
    *,
    now: str | None = None,
    companion_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    as_of = now or utc_now()
    observations = [
        row
        for row in (store.list_observations() or [])
        if _row_patient(row) == patient_id and _is_health_connect(row)
    ]
    measurements = [
        row
        for row in (store.list_measurements() or [])
        if _row_patient(row) == patient_id
    ]

    companion = companion_status
    if companion is None:
        try:
            companion = store.get_companion_status() or {}
        except Exception:
            companion = {}
    companion = companion if isinstance(companion, dict) else {}
    hc_status = companion.get("health_connect") if isinstance(companion.get("health_connect"), dict) else {}
    companion_latest_by_metric = {}
    raw_latest = hc_status.get("latest_by_metric") or companion.get("latest_received_by_metric") or {}
    if isinstance(raw_latest, dict):
        companion_latest_by_metric = {
            canonicalize_metric(key) or str(key): str(value)
            for key, value in raw_latest.items()
            if value
        }

    by_metric: dict[str, dict[str, Any]] = {}
    vault_latest = None
    for metric in FRESHNESS_METRICS:
        vault_at = None
        vault_value = None
        for row in observations:
            name = canonicalize_metric(row.get("metric_type") or row.get("metric") or "")
            if name != metric:
                continue
            measured = str(row.get("measured_at") or "")
            if measured and (vault_at is None or measured > vault_at):
                vault_at = measured
                vault_value = row.get("value")
        if vault_at is None:
            for row in measurements:
                name = canonicalize_metric(row.get("metric") or row.get("metric_type") or "")
                if name != metric:
                    continue
                measured = str(row.get("measured_at") or "")
                if measured and (vault_at is None or measured > vault_at):
                    vault_at = measured
                    vault_value = row.get("value")
        if vault_at and (vault_latest is None or vault_at > vault_latest):
            vault_latest = vault_at
        freshness = compute_freshness(metric=metric, measured_at=vault_at, now=None)
        companion_at = companion_latest_by_metric.get(metric)
        by_metric[metric] = {
            "health_connect_latest_at": None,
            "companion_reported_latest_at": companion_at,
            "vault_latest_at": vault_at,
            "api_latest_at": vault_at,
            "ui_latest_at": vault_at,
            "vault_latest_value": vault_value,
            "currentness": freshness.get("currentness"),
            "freshness_status": freshness.get("freshness_status"),
            "freshness_label": freshness.get("label"),
        }

    last_sync = (
        companion.get("last_success_at")
        or companion.get("last_attempt_at")
        or hc_status.get("last_success_at")
        or None
    )
    break_point = _classify_break(by_metric, companion_latest_by_metric, last_sync)
    return {
        "as_of": as_of,
        "patient_id": patient_id,
        "last_ui_refresh_is_not_measurement_time": True,
        "last_health_connect_sync_at": last_sync,
        "last_sync_attempt_at": companion.get("last_attempt_at"),
        "latest_measurement_at": vault_latest,
        "companion_observation_count": len(observations),
        "health_connect_inspectable_from_this_host": False,
        "by_metric": by_metric,
        "break": break_point,
    }


def _classify_break(
    by_metric: dict[str, dict[str, Any]],
    companion_latest: dict[str, str],
    last_sync: str | None,
) -> dict[str, Any]:
    missing = [metric for metric, row in by_metric.items() if not row.get("vault_latest_at")]
    present = {
        metric: row.get("vault_latest_at")
        for metric, row in by_metric.items()
        if row.get("vault_latest_at")
    }
    if not present:
        return {
            "boundary": "vault_empty_or_no_matching_observations",
            "detail": "No Health Connect observations for the audited metrics are persisted in this vault.",
        }
    mismatched = []
    for metric, companion_at in companion_latest.items():
        vault_at = (by_metric.get(metric) or {}).get("vault_latest_at")
        if companion_at and vault_at and str(companion_at) > str(vault_at):
            mismatched.append(metric)
    if mismatched:
        return {
            "boundary": "companion_to_vault",
            "detail": "Companion reported a later timestamp than the vault for: " + ", ".join(mismatched),
        }
    if last_sync and present:
        latest = max(present.values())
        if str(last_sync) > str(latest):
            return {
                "boundary": "health_connect_or_source_app_not_writing_newer_samples",
                "detail": (
                    "A later sync attempt exists than the latest persisted measurement. "
                    "Newer source-app readings may not be in Health Connect or were not returned by the companion query."
                ),
                "latest_measurement_at": latest,
                "last_sync_at": last_sync,
            }
    return {
        "boundary": "none_detected_in_inspectable_layers",
        "detail": (
            "Vault and API latest timestamps match. Live Health Connect contents are not "
            "inspectable from this host; compare device source-app times against vault_latest_at."
        ),
        "unreadable_boundaries": ["health_connect_on_device", "source_app_private_stores"],
        "missing_metrics": missing,
    }
