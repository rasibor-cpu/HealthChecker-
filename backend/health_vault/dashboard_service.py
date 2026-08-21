"""Dashboard Backend Service Layer — Gated data synthesis & personalization."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from backend.health_vault.health_intelligence import HealthIntelligenceEngine
from backend.health_vault.monitoring.monitoring_engine import MonitoringEngine
from backend.health_vault.models import (
    DashboardSummary,
    DashboardWidget,
    UserDashboardPreferences,
)
from backend.health_vault.timeline import _measurements_by_document, build_timeline
from backend.health_vault.vault_store import VaultStore


def _patient_has_health_connect_observations(
    store: VaultStore,
    patient_id: str,
    *,
    observations: list[dict[str, Any]] | None = None,
) -> bool:
    rows = store.list_observations() if observations is None else observations
    for row in rows:
        if str(row.get("patient_id") or "default-patient") != patient_id:
            continue
        source = str(row.get("source") or "")
        if "health_connect" in source or row.get("connector_id") == "health_connect":
            return True
    return False


def _health_connect_sync_summary(
    store: VaultStore,
    patient_id: str,
    *,
    observations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """First-class Health Connect / Android companion sync status for the consumer dashboard."""
    paired_count = 0
    last_seen_at = None
    try:
        from backend.health_vault.companion.pairing import CompanionPairingService

        devices = CompanionPairingService(store).list_devices(
            include_revoked=False, patient_id=patient_id
        )
        paired_count = len(devices)
        for device in devices:
            seen = device.get("last_seen_at")
            if seen and (last_seen_at is None or str(seen) > str(last_seen_at)):
                last_seen_at = seen
    except Exception:
        paired_count = 0

    rows = store.list_observations() if observations is None else observations
    last_observation_at = None
    observation_count = 0
    for row in rows:
        if str(row.get("patient_id") or "default-patient") != patient_id:
            continue
        source = str(row.get("source") or "")
        if "health_connect" not in source and row.get("connector_id") != "health_connect":
            continue
        observation_count += 1
        measured = row.get("measured_at") or row.get("ingested_at")
        if measured and (last_observation_at is None or str(measured) > str(last_observation_at)):
            last_observation_at = measured

    companion = store.get_companion_status() or {}
    companion_device = str(companion.get("device_id") or "")
    # Only surface companion_status when it belongs to a device paired to this patient
    companion_for_patient = {}
    if companion and paired_count:
        try:
            from backend.health_vault.companion.pairing import CompanionPairingService

            owned_ids = {
                str(d.get("device_id"))
                for d in CompanionPairingService(store).list_devices(
                    include_revoked=True, patient_id=patient_id
                )
            }
            if companion_device and companion_device in owned_ids:
                companion_for_patient = {
                    "device_id": companion_device,
                    "updated_at": companion.get("updated_at") or companion.get("last_sync_at"),
                    "health_connect_status": companion.get("health_connect")
                    or companion.get("health_connect_status")
                    or {},
                }
                sync_ts = companion_for_patient.get("updated_at")
                if sync_ts and (last_seen_at is None or str(sync_ts) > str(last_seen_at)):
                    last_seen_at = sync_ts
        except Exception:
            companion_for_patient = {}

    if observation_count > 0 and paired_count > 0:
        sync_state = "synced"
        label = "Health Connect sync active"
    elif observation_count > 0:
        # Observations in the vault mean Android/HC delivery already occurred even if
        # the current pairing roster is empty (revoked/re-paired/stale device registry).
        sync_state = "observations_present"
        label = "Health Connect observations present"
        if paired_count == 0:
            label = (
                "Health Connect observations present "
                "(no active paired Android device in registry)"
            )
    elif paired_count > 0:
        sync_state = "paired_awaiting_data"
        label = "Android companion paired — awaiting Health Connect data"
    else:
        sync_state = "not_configured"
        label = "Health Connect / Android sync not configured"

    # Prefer companion heartbeat when it confirms a healthier state than raw counts alone.
    companion_hc = (companion_for_patient.get("health_connect_status") or {})
    companion_state = str(
        companion_hc.get("status")
        or companion_hc.get("state")
        or companion_for_patient.get("status")
        or ""
    ).lower()
    if companion_state in {"synced", "ok", "healthy", "active"} and observation_count > 0:
        sync_state = "synced"
        label = "Health Connect sync active"

    return {
        "sync_state": sync_state,
        "label": label,
        "paired_device_count": paired_count,
        "observation_count": observation_count,
        "last_observation_at": last_observation_at,
        "last_device_seen_at": last_seen_at,
        "companion": companion_for_patient,
        "host_note": (
            "Host cannot read Health Connect directly. "
            "Android companion delivers observational data after permission grant."
        ),
        "reason": label,
    }


def _trend_exclusion_notes(
    *,
    observations: list[dict[str, Any]],
    patient_id: str,
    trends: dict[str, Any],
) -> list[dict[str, str]]:
    """Explicit exclusions for HC metrics that intentionally do not enter Trends."""
    from backend.health_vault.metric_normalization import (
        MONITORING_TREND_METRICS,
        canonicalize_metric,
    )

    present: set[str] = set()
    for row in observations:
        if str(row.get("patient_id") or "default-patient") != patient_id:
            continue
        source = str(row.get("source") or "")
        if "health_connect" not in source and row.get("connector_id") != "health_connect":
            continue
        metric = canonicalize_metric(str(row.get("metric_type") or row.get("metric") or ""))
        if metric and metric != "unknown":
            present.add(metric)

    notes: list[dict[str, str]] = []
    for metric in sorted(present):
        if metric in trends:
            continue
        if metric in MONITORING_TREND_METRICS:
            notes.append(
                {
                    "metric": metric,
                    "reason": "insufficient_or_ineligible_samples",
                    "message": (
                        f"{metric.replace('_', ' ')} has Health Connect observations but "
                        "does not yet meet trend sample/eligibility requirements."
                    ),
                }
            )
        else:
            notes.append(
                {
                    "metric": metric,
                    "reason": "intentionally_excluded_from_trends",
                    "message": (
                        f"{metric.replace('_', ' ')} is retained as observational Health Connect "
                        "data and is intentionally excluded from classical consumer Trends."
                    ),
                }
            )
    return notes


def _monitoring_observation_cards(
    store: VaultStore, patient_id: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Surface HC-302 Health Connect latest readings for the consumer dashboard."""
    status = MonitoringEngine(store).build_status(patient_id=patient_id)
    latest = dict(status.get("latest_reading_by_metric") or {})
    cards: list[dict[str, Any]] = []
    for metric, row in sorted(latest.items()):
        value = row.get("value")
        if value is None:
            continue
        unit = row.get("unit") or row.get("units") or ""
        measured_at = row.get("measured_at") or ""
        freshness = row.get("freshness_status") or "unknown"
        cards.append(
            {
                "patient_id": patient_id,
                "category": "continuous_monitoring",
                "metric": metric,
                "fact": f"Latest {metric.replace('_', ' ')}: {value}{(' ' + unit) if unit else ''}",
                "interpretation": f"Health Connect ({freshness})",
                "explanation": (
                    "Observational Health Connect data from the encrypted vault. "
                    "Not a diagnosis or treatment recommendation."
                ),
                "measured_at": measured_at,
                "source": row.get("source") or "health_connect_companion",
            }
        )
    return cards, latest


def _monitoring_trend_snapshot(
    store: VaultStore,
    patient_id: str,
    *,
    observations: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], int]:
    """Lightweight trend view from persisted monitoring observations.

    Health Connect observational rows participate in consumer trends with an
    explicit observational provenance — they are never labeled as clinical/lab.
    """
    from backend.health_vault.metric_normalization import (
        MONITORING_TREND_METRICS,
        canonicalize_metric,
    )

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rows = store.list_observations() if observations is None else observations
    for row in rows:
        if str(row.get("patient_id") or "default-patient") != patient_id:
            continue
        source = str(row.get("source") or "")
        if "health_connect" not in source and row.get("connector_id") != "health_connect":
            continue
        metric = canonicalize_metric(
            str(row.get("metric_type") or row.get("metric") or "").strip()
        )
        if not metric or metric == "unknown" or row.get("value") is None:
            continue
        if metric not in MONITORING_TREND_METRICS:
            continue
        try:
            float(row.get("value"))
        except (TypeError, ValueError):
            continue
        if not (row.get("measured_at") or row.get("ingested_at")):
            continue
        buckets[metric].append(row)
    trends: dict[str, Any] = {}
    for metric, metric_rows in buckets.items():
        metric_rows.sort(key=lambda item: str(item.get("measured_at") or item.get("ingested_at") or ""))
        latest = metric_rows[-1]
        try:
            values = [float(r["value"]) for r in metric_rows]
        except (TypeError, ValueError, KeyError):
            continue
        direction = "stable"
        label = "Available"
        reason = "monitoring_snapshot"
        if len(values) >= 3:
            from backend.health_vault.trend_engine import TrendEngine

            classified = TrendEngine.classify(metric, values)
            direction = classified["direction"]
            label = classified["label"]
            reason = "monitoring_auto"
        trends[metric] = {
            "metric": metric,
            "direction": direction,
            "label": label,
            "reason": reason,
            "sample_count": len(metric_rows),
            "latest": latest.get("value"),
            "updated_at": latest.get("measured_at") or latest.get("ingested_at"),
            "category": "continuous_monitoring",
            "provenance": "health_connect_observational",
            "data_plane": "monitoring",
        }
    return trends, sum(len(rows) for rows in buckets.values())


def _merge_trend_planes(
    clinical_trends: dict[str, Any],
    monitoring_trends: dict[str, Any],
) -> dict[str, Any]:
    """Merge clinical cached trends with HC observational trends without silently collapsing planes.

    - Monitoring-only metrics keep Health Connect observational provenance.
    - Clinical-only metrics keep clinical/lab provenance.
    - When both planes contribute the same metric key, label the row as combined explicitly
      (do not pretend the series is purely clinical/lab).
    """
    from backend.health_vault.metric_normalization import MONITORING_TREND_METRICS

    merged: dict[str, Any] = {}
    clinical = clinical_trends or {}
    monitoring = monitoring_trends or {}
    for metric in sorted(set(clinical) | set(monitoring)):
        clin = dict(clinical[metric]) if metric in clinical else None
        mon = dict(monitoring[metric]) if metric in monitoring else None
        if clin is not None:
            clin_prov = str(clin.get("provenance") or clin.get("data_plane") or "").strip().lower()
            if not clin_prov or clin_prov in {"monitoring", "health_connect_observational"}:
                # Legacy/unprovenanced cache rows for wearable metrics must not override HC.
                if mon is not None and (
                    metric in MONITORING_TREND_METRICS
                    or clin_prov in {"monitoring", "health_connect_observational"}
                ):
                    clin = None
                else:
                    clin["provenance"] = clin.get("provenance") or "clinical"
                    clin["data_plane"] = clin.get("data_plane") or "clinical"
            else:
                clin.setdefault("provenance", "clinical")
                clin.setdefault("data_plane", clin.get("data_plane") or "clinical")
        if clin is not None and mon is not None:
            entry = dict(clin)
            entry["provenance"] = "combined_clinical_and_health_connect"
            entry["data_plane"] = "combined"
            entry["plane_components"] = {
                "clinical": {
                    "latest": clin.get("latest"),
                    "sample_count": clin.get("sample_count"),
                },
                "health_connect_observational": {
                    "latest": mon.get("latest"),
                    "sample_count": mon.get("sample_count"),
                },
            }
            entry["label"] = clin.get("label") or mon.get("label") or entry.get("label")
            merged[metric] = entry
        elif clin is not None:
            merged[metric] = clin
        elif mon is not None:
            merged[metric] = mon
    return merged


class DashboardService:
    """Orchestrates dashboard landing pages, preferences, and widgets."""

    def __init__(self, store: VaultStore) -> None:
        self.store = store
        self.intel_engine = HealthIntelligenceEngine(store)

    def get_preferences(self, patient_id: str) -> UserDashboardPreferences:
        """Load user personalization choices from the encrypted vault profile."""
        profile = self.store.get_profile(patient_id=patient_id)
        prefs_data = profile.get("dashboard_preferences") or {}
        return UserDashboardPreferences.from_dict(prefs_data)

    def save_preferences(
        self, patient_id: str, preferences: UserDashboardPreferences
    ) -> UserDashboardPreferences:
        """Persist user personalization choices directly inside the patient's vault profile."""
        profile = self.store.get_profile(patient_id=patient_id)
        profile["dashboard_preferences"] = preferences.to_dict()
        self.store.update_profile({"dashboard_preferences": preferences.to_dict()}, patient_id=patient_id)
        return preferences

    def get_summary(self, patient_id: str) -> DashboardSummary:
        """Synthesize clinical data and construct ordered widgets for landing page view."""
        prefs = self.get_preferences(patient_id)
        from backend.health_vault.records_service import RecordsService

        records_service = RecordsService(self.store)
        all_measurements = self.store.list_measurements()
        measurement_counts = RecordsService._measurement_counts_by_document(all_measurements)
        measurements_by_document = _measurements_by_document(all_measurements)
        patient_observations = self.store.list_observations()
        patient_records = records_service.list_records(
            patient_id,
            measurement_counts=measurement_counts,
        )
        record_summaries = [record.to_summary_dict() for record in patient_records[:5]]
        records_count = len(patient_records)
        measurements_count = sum(record.metrics_count for record in patient_records)
        
        # 1. Fetch patient intelligence outputs and Health Connect monitoring evidence
        observations = list(self.intel_engine.get_patient_observations(patient_id))
        monitoring_cards: list[dict[str, Any]] = []
        monitoring_latest: dict[str, Any] = {}
        if _patient_has_health_connect_observations(
            self.store, patient_id, observations=patient_observations
        ):
            monitoring_cards, monitoring_latest = _monitoring_observation_cards(
                self.store, patient_id
            )
        if monitoring_cards:
            observations = monitoring_cards + observations
        trends = self.store.get_trends(patient_id=patient_id) or {}
        monitoring_trends: dict[str, Any] = {}
        health_connect_observation_count = 0
        if _patient_has_health_connect_observations(
            self.store, patient_id, observations=patient_observations
        ):
            monitoring_trends, _eligible_samples = _monitoring_trend_snapshot(
                self.store, patient_id, observations=patient_observations
            )
            health_connect_observation_count = sum(
                1
                for row in patient_observations
                if str(row.get("patient_id") or "default-patient") == patient_id
                and (
                    "health_connect" in str(row.get("source") or "")
                    or row.get("connector_id") == "health_connect"
                )
            )
        trends = _merge_trend_planes(trends, monitoring_trends)
        health_connect_sync = _health_connect_sync_summary(
            self.store, patient_id, observations=patient_observations
        )
        trend_exclusions = _trend_exclusion_notes(
            observations=patient_observations,
            patient_id=patient_id,
            trends=trends,
        )
        timeline = build_timeline(
            self.store,
            patient_id=patient_id,
            include_guardian_events=True,
            newest_first=True,
            measurements_by_document=measurements_by_document,
        )

        # 2. Derive overall status & count active warnings
        active_warnings = 0
        status = "normal"
        for obs in observations:
            interpretation = obs.get("interpretation", "").lower()
            # Warnings have interpretation as "missing data warning"
            if "warning" in interpretation or "missing" in interpretation:
                active_warnings += 1
            elif "worsening" in interpretation or "critical" in interpretation or "elevated" in interpretation:
                status = "warning"

        # 3. Create widgets dynamically
        widgets_dict = {
            "status_summary": DashboardWidget(
                widget_id="status_summary",
                title="Health Status Summary",
                widget_type="status",
                priority=1,
                payload={
                    "status": status,
                    "active_warnings": active_warnings,
                    "measurements_count": measurements_count,
                    "monitoring_latest": monitoring_latest,
                    "health_connect_observation_count": health_connect_observation_count,
                    "health_connect_sync": health_connect_sync,
                }
            ),
            "key_observations": DashboardWidget(
                widget_id="key_observations",
                title="Key Observations",
                widget_type="observations_list",
                priority=2,
                payload={"observations": observations}
            ),
            "trends_widget": DashboardWidget(
                widget_id="trends_widget",
                title="Health Metric Trends",
                widget_type="trends_chart",
                priority=3,
                payload={
                    "trends": trends,
                    "priority_metric": prefs.priority_metric,
                    "exclusions": trend_exclusions,
                }
            ),
            "timeline_widget": DashboardWidget(
                widget_id="timeline_widget",
                title="Health Timeline",
                widget_type="timeline_list",
                priority=4,
                payload={"events": timeline[:10]}  # Top 10 events
            ),
            "import_wizard": DashboardWidget(
                widget_id="import_wizard",
                title="Import Medical Records",
                widget_type="import_entry",
                priority=5,
                payload={
                    "allowed_formats": ["PDF", "JSON", "PNG"],
                    "records_count": records_count,
                    "recent_records": record_summaries,
                }
            )
        }

        # 4. Map & order widgets based on preferences
        ordered_widgets = []
        for i, widget_id in enumerate(prefs.widget_order):
            if widget_id in prefs.visible_widgets and widget_id in widgets_dict:
                widget = widgets_dict[widget_id]
                # If it's a priority metric, elevate priority value
                if widget_id == "trends_widget" and prefs.priority_metric:
                    widget.priority = -1
                else:
                    widget.priority = i
                ordered_widgets.append(widget)

        # Handle any visible widgets missing from order
        for w_id in prefs.visible_widgets:
            if w_id in widgets_dict and widgets_dict[w_id] not in ordered_widgets:
                widget = widgets_dict[w_id]
                widget.priority = len(prefs.widget_order)
                ordered_widgets.append(widget)

        # Sort based on priority value (smaller priority numbers go first)
        ordered_widgets.sort(key=lambda w: w.priority)

        profile = self.store.get_profile(patient_id=patient_id) or {}
        raw_display = str(profile.get("display_name") or "").strip()
        display_name = raw_display if raw_display and raw_display != patient_id else None

        return DashboardSummary(
            patient_id=patient_id,
            overall_status=status,
            active_warnings_count=active_warnings,
            widgets=ordered_widgets,
            display_name=display_name,
        )
