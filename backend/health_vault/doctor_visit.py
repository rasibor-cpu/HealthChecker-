"""Doctor Visit Mode — printable professional summary."""

from __future__ import annotations

from typing import Any

from backend.health_vault.models import UserDashboardPreferences, utc_now
from backend.health_vault.timeline import build_timeline
from backend.health_vault.unit_conversion import CANONICAL_UNITS, apply_display_units
from backend.health_vault.vault_store import VaultStore


class DoctorVisitMode:
    def __init__(self, store: VaultStore) -> None:
        self.store = store

    def _prefs(self, patient_id: str) -> UserDashboardPreferences:
        profile = self.store.get_profile(patient_id=patient_id) or {}
        return UserDashboardPreferences.from_dict(profile.get("dashboard_preferences") or {})

    def _trend_line(self, metric: str, patient_id: str, *, label: str | None = None) -> str:
        t = (self.store.get_trends(patient_id=patient_id) or {}).get(metric) or {}
        if not t:
            return "n/a"
        latest = t.get("latest")
        direction = t.get("label") or "n/a"
        prefs = self._prefs(patient_id)
        display = apply_display_units(
            {
                "metric": metric,
                "observation_class": metric,
                "value": latest,
                "units": CANONICAL_UNITS.get(metric),
            },
            region=prefs.reporting_region,
            unit_overrides=prefs.unit_overrides,
        )
        shown = display.get("display_value")
        units = display.get("display_units") or ""
        if shown is None:
            shown = latest
        prefix = f"{label} " if label else ""
        if shown is not None:
            unit_bit = f" {units}" if units else ""
            return f"{prefix}{direction} (latest {shown}{unit_bit})".strip()
        return f"{prefix}{direction}".strip()

    def _diabetes_trend(self, patient_id: str) -> str:
        trends = self.store.get_trends(patient_id=patient_id) or {}
        glucose_keys = (
            "glucose_random",
            "glucose",
            "glucose_fasting",
            "glucose_cgm_interstitial",
            "glucose_postprandial",
            "glucose_capillary",
            "glucose_serum_plasma",
        )
        if not any(trends.get(key) for key in glucose_keys):
            return f"n/a · HbA1c {self._trend_line('hba1c', patient_id)}"
        parts: list[str] = []
        if trends.get("glucose_random"):
            parts.append(self._trend_line("glucose_random", patient_id, label="random"))
        elif trends.get("glucose"):
            parts.append(self._trend_line("glucose", patient_id, label="unspecified"))
        if trends.get("glucose_fasting"):
            parts.append(self._trend_line("glucose_fasting", patient_id, label="fasting"))
        if trends.get("glucose_cgm_interstitial"):
            parts.append(self._trend_line("glucose_cgm_interstitial", patient_id, label="CGM"))
        parts.append(f"HbA1c {self._trend_line('hba1c', patient_id)}")
        return " · ".join(parts) if parts else "n/a"

    def generate(self, patient_id: str = "default-patient") -> dict[str, Any]:
        profile = self.store.get_profile(patient_id=patient_id)
        if not profile and patient_id == "default-patient":
            profile = self.store.get_profile()
        docs = [
            document
            for document in self.store.list_documents()
            if document.get("patient_id", "default-patient") == patient_id
        ]
        timeline = build_timeline(self.store, patient_id=patient_id)
        ecg = [
            d
            for d in docs
            if d.get("document_type") == "samsung_health_ecg" or "ecg" in (d.get("tags") or [])
        ][-3:]
        kidney = self._trend_line("egfr", patient_id)
        return {
            "title": "HealthChecker+ Doctor Visit Report",
            "generated_at": utc_now(),
            "patient_id": patient_id,
            "fhir_bundle_hint": [
                "Patient",
                "Medication",
                "Observation",
                "DiagnosticReport",
                "DocumentReference",
                "Encounter",
            ],
            "current_diagnoses": profile.get("diagnoses") or [],
            "current_medications": profile.get("medications") or [],
            "recent_ecg": list(reversed(ecg)),
            "kidney_trend": kidney,
            "blood_pressure_trend": f"{self._trend_line('systolic_bp', patient_id)} / {self._trend_line('diastolic_bp', patient_id)}",
            "sleep_trend": self._trend_line("sleep_score", patient_id),
            "diabetes_trend": self._diabetes_trend(patient_id),
            "imported_reports": docs,
            "health_timeline": timeline[:25],
        }
