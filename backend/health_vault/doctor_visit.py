"""Doctor Visit Mode — printable professional summary."""

from __future__ import annotations

from typing import Any

from backend.health_vault.models import utc_now
from backend.health_vault.timeline import build_timeline
from backend.health_vault.vault_store import VaultStore


class DoctorVisitMode:
    def __init__(self, store: VaultStore) -> None:
        self.store = store

    def _trend_line(self, metric: str) -> str:
        t = (self.store.get_trends() or {}).get(metric) or {}
        if not t:
            return "n/a"
        latest = t.get("latest")
        label = t.get("label") or "n/a"
        return f"{label} (latest {latest})" if latest is not None else label

    def generate(self, patient_id: str = "default-patient") -> dict[str, Any]:
        profile = self.store.get_profile()
        docs = self.store.list_documents()
        timeline = build_timeline(self.store)
        ecg = [
            d
            for d in docs
            if d.get("document_type") == "samsung_health_ecg" or "ecg" in (d.get("tags") or [])
        ][-3:]
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
            "kidney_trend": self._trend_line("egfr"),
            "blood_pressure_trend": f"{self._trend_line('systolic')} / {self._trend_line('diastolic')}",
            "sleep_trend": self._trend_line("sleep_score"),
            "diabetes_trend": f"{self._trend_line('glucose')} · HbA1c {self._trend_line('hba1c')}",
            "imported_reports": docs,
            "health_timeline": timeline[:25],
        }
