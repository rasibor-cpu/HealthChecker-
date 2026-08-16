"""Dashboard Backend Service Layer — Gated data synthesis & personalization."""

from __future__ import annotations

from typing import Any

from backend.health_vault.health_intelligence import HealthIntelligenceEngine
from backend.health_vault.models import (
    DashboardSummary,
    DashboardWidget,
    UserDashboardPreferences,
)
from backend.health_vault.timeline import build_timeline
from backend.health_vault.vault_store import VaultStore


class DashboardService:
    """Orchestrates dashboard landing pages, preferences, and widgets."""

    def __init__(self, store: VaultStore) -> None:
        self.store = store
        self.intel_engine = HealthIntelligenceEngine(store)

    def get_preferences(self, patient_id: str) -> UserDashboardPreferences:
        """Load user personalization choices from the encrypted vault profile."""
        profile = self.store.get_profile()
        prefs_data = profile.get("dashboard_preferences") or {}
        return UserDashboardPreferences.from_dict(prefs_data)

    def save_preferences(
        self, patient_id: str, preferences: UserDashboardPreferences
    ) -> UserDashboardPreferences:
        """Persist user personalization choices directly inside the patient's vault profile."""
        profile = self.store.get_profile()
        profile["dashboard_preferences"] = preferences.to_dict()
        self.store.update_profile({"dashboard_preferences": preferences.to_dict()})
        return preferences

    def get_summary(self, patient_id: str) -> DashboardSummary:
        """Synthesize clinical data and construct ordered widgets for landing page view."""
        prefs = self.get_preferences(patient_id)
        from backend.health_vault.records_service import RecordsService
        record_summaries = [
            record.to_summary_dict()
            for record in RecordsService(self.store).list_records(patient_id)[:5]
        ]
        
        # 1. Fetch patient intelligence outputs
        observations = self.intel_engine.get_patient_observations(patient_id)
        trends = self.store.get_trends(patient_id=patient_id)
        timeline = build_timeline(
            self.store,
            patient_id=patient_id,
            include_guardian_events=True,
            newest_first=True
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
                    "measurements_count": len(self.store.list_measurements()),
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
                    "priority_metric": prefs.priority_metric
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
                    "records_count": len(RecordsService(self.store).list_records(patient_id)),
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

        return DashboardSummary(
            patient_id=patient_id,
            overall_status=status,
            active_warnings_count=active_warnings,
            widgets=ordered_widgets
        )
