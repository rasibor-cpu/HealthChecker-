"""Generic medical document + measurement models (FHIR-ready naming)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class MedicalDocument:
    """Generic MedicalDocument — not Samsung-specific. Maps toward DocumentReference."""

    id: str = field(default_factory=lambda: str(uuid4()))
    patient_id: str = "default-patient"
    document_type: str = "unknown"
    source_system: str = "unknown"
    acquisition_method: str = "manual_upload"
    original_filename: str | None = None
    storage_uri: str | None = None
    sha256: str | None = None
    imported_at: str = field(default_factory=utc_now)
    measured_at: str | None = None
    parser_version: str | None = None
    parser_confidence: float | None = None
    status: str = "imported"
    tags: list[str] = field(default_factory=list)
    fhir_resource: str = "DocumentReference"
    interpretation: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    duplicate_of: str | None = None
    # Provenance (HC-201F): original_document_verified | user_reported |
    # historical_summary | wearable_screenshot | wearable_pdf
    provenance: str | None = None
    # HC-201G batch / multi-image grouping (non-destructive)
    batch_id: str | None = None
    group_id: str | None = None
    sequence_number: int | None = None
    page_number: int | None = None
    group_title: str | None = None
    # HC-201H classification + dating
    primary_category: str | None = None
    secondary_categories: list[str] = field(default_factory=list)
    classification_confidence: float | None = None
    classification_method: str | None = None
    classification_version: str | None = None
    requires_review: bool = False
    report_date: str | None = None
    file_capture_date: str | None = None
    date_confidence: float | None = None
    date_source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Measurement:
    """Universal Measurement entity — maps toward FHIR Observation."""

    measurement_id: str = field(default_factory=lambda: str(uuid4()))
    document_id: str | None = None
    category: str = "Uncategorized"
    metric: str = "unknown"
    value: Any = None
    units: str | None = None
    reference_range: str | None = None
    abnormal_flag: str | None = None
    confidence: float | None = None
    measured_at: str | None = None
    fhir_resource: str = "Observation"
    original_metric: str | None = None
    original_value: Any = None
    original_units: str | None = None
    unit_compatible: bool = True
    normalization_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Extensible metric catalog (future measurements register here)
METRIC_CATALOG: dict[str, dict[str, Any]] = {
    "ecg_result": {"category": "ECG", "units": None},
    "heart_rhythm": {"category": "Heart Rhythm", "units": None},
    "heart_rate": {"category": "Heart Rate", "units": "bpm"},
    "average_hr": {"category": "Heart Rate", "units": "bpm"},
    "resting_hr": {"category": "Resting HR", "units": "bpm"},
    "hrv": {"category": "HRV", "units": "ms"},
    "sleep_score": {"category": "Sleep Score", "units": "score"},
    "sleep_duration": {"category": "Sleep Duration", "units": "h"},
    "deep_sleep": {"category": "Deep Sleep", "units": "h"},
    "rem_sleep": {"category": "REM", "units": "h"},
    "respiratory_rate": {"category": "Respiratory Rate", "units": "/min"},
    "skin_temperature": {"category": "Skin Temperature", "units": "C"},
    "energy_score": {"category": "Energy Score", "units": "score"},
    "creatinine": {"category": "Creatinine", "units": "umol/L"},
    "egfr": {"category": "eGFR", "units": "mL/min/1.73m2"},
    "protein": {"category": "Protein", "units": None},
    "uacr": {"category": "UACR", "units": "mg/mmol"},
    "potassium": {"category": "Potassium", "units": "mmol/L"},
    "glucose": {"category": "Glucose", "units": "mg/dL"},
    "hba1c": {"category": "HbA1c", "units": "%"},
    "cgm_average": {"category": "CGM", "units": "mg/dL"},
    "cgm_time_in_range": {"category": "CGM", "units": "%"},
    "cgm_gmi": {"category": "CGM", "units": "%"},
    "systolic": {"category": "Systolic", "units": "mmHg"},
    "diastolic": {"category": "Diastolic", "units": "mmHg"},
    "weight": {"category": "Weight", "units": "kg"},
    "bmi": {"category": "BMI", "units": "kg/m2"},
    "sleep_latency": {"category": "Sleep Latency", "units": "h"},
    "urea": {"category": "Urea", "units": "mmol/L"},
    "skin_temperature_deviation": {"category": "Skin Temperature", "units": "C"},
    "medication": {"category": "Medication", "units": None},
    "diagnosis": {"category": "Diagnosis", "units": None},
    "oxygen_saturation": {"category": "Respiratory", "units": "%"},
    "pulse": {"category": "Heart Rate", "units": "bpm"},
    "steps": {"category": "Activity", "units": "count"},
    "activity_minutes": {"category": "Activity", "units": "min"},
    "exercise_minutes": {"category": "Activity", "units": "min"},
    "spo2": {"category": "Respiratory", "units": "%"},
}

PROVENANCE_VALUES = (
    "original_document_verified",
    "user_reported",
    "historical_summary",
    "wearable_screenshot",
    "wearable_pdf",
    "health_connect_sync",
    "libre_authorized_live",
    "continuous_monitoring",
    "simulated_test_only",
)

PROVENANCE_CONFIDENCE = {
    "original_document_verified": 0.95,
    "wearable_pdf": 0.9,
    "wearable_screenshot": 0.85,
    "historical_summary": 0.7,
    "user_reported": 0.65,
}


def create_measurement(**kwargs: Any) -> Measurement:
    metric = str(kwargs.get("metric") or "unknown")
    meta = METRIC_CATALOG.get(metric, {})
    return Measurement(
        measurement_id=kwargs.get("measurement_id") or str(uuid4()),
        document_id=kwargs.get("document_id"),
        category=kwargs.get("category") or meta.get("category") or "Uncategorized",
        metric=metric,
        value=kwargs.get("value"),
        units=kwargs["units"] if "units" in kwargs else meta.get("units"),
        reference_range=kwargs.get("reference_range"),
        abnormal_flag=kwargs.get("abnormal_flag"),
        confidence=kwargs.get("confidence"),
        measured_at=kwargs.get("measured_at"),
        fhir_resource=kwargs.get("fhir_resource") or "Observation",
    )


def register_metric(metric: str, **meta: Any) -> None:
    METRIC_CATALOG[metric] = {**METRIC_CATALOG.get(metric, {}), **meta}


DOCUMENT_TYPES = (
    "samsung_health_ecg",
    "samsung_health_sleep",
    "samsung_health_energy_score",
    "galaxy_watch_report",
    "blood_pressure_screenshot",
    "blood_glucose",
    "libre_cgm_report",
    "laboratory_pdf",
    "hospital_report",
    "medication_report",
    "imaging_report",
    "ai_assisted_import",
    "json_measurements",
    "continuous_monitoring_observation",
    "unknown",
)


def classify_document_type(filename: str | None, mime: str | None, hint: str | None = None) -> str:
    if hint and hint in DOCUMENT_TYPES:
        return hint
    name = (filename or "").lower()
    type_ = (mime or "").lower()
    if "ecg" in name or "ekg" in name:
        return "samsung_health_ecg"
    if "sleep" in name:
        return "samsung_health_sleep"
    if "energy" in name:
        return "samsung_health_energy_score"
    if "galaxy" in name or "watch" in name:
        return "galaxy_watch_report"
    if "libre" in name or "cgm" in name:
        return "libre_cgm_report"
    if "glucose" in name or name.endswith("bg"):
        return "blood_glucose"
    if "medication" in name or "rx" in name:
        return "medication_report"
    if any(x in name for x in ("imaging", "xray", "mri", "ct")):
        return "imaging_report"
    if "bp" in name or "blood_pressure" in name or "pressure" in name:
        return "blood_pressure_screenshot"
    if "pdf" in type_ or name.endswith(".pdf"):
        if any(x in name for x in ("lab", "lifelabs", "blood")):
            return "laboratory_pdf"
        return "hospital_report"
    if "json" in type_ or name.endswith(".json"):
        return "json_measurements"
    return "unknown"
