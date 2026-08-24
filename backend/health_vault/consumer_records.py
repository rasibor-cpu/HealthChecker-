"""HC323 — consumer Health Records titles, categories, and Health Connect aggregation.

Presentation-layer only. Does not delete, collapse, or rewrite vault documents.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from backend.health_vault.category_classifier import CATEGORY_LABELS, classify_health_record
from backend.health_vault.metric_normalization import canonicalize_metric
from backend.health_vault.models import RecordCategory

CONSUMER_CATEGORY_LABELS = {
    "cardiovascular": "Cardiovascular",
    "activity_fitness": "Activity",
    "sleep": "Sleep",
    "respiratory_oxygen": "Respiratory / Oxygen",
    "glucose_diabetes": "Glucose / Metabolic",
    "blood_pressure": "Blood Pressure",
    "laboratory_report": "Laboratory",
    "medication": "Medication / Prescription",
    "imaging": "Imaging",
    "hospital_clinical_report": "Clinical Document",
    "ecg_cardiology": "ECG",
    "kidney_renal": "Kidney / Renal",
    "weight_body_metrics": "Weight",
    "other": "Other",
}

METRIC_TITLES = {
    "heart_rate": "Heart rate observation",
    "resting_hr": "Resting heart rate observation",
    "oxygen_saturation": "Blood oxygen observation",
    "steps": "Steps observation",
    "sleep_duration": "Sleep observation",
    "sleep_score": "Sleep score observation",
    "deep_sleep_duration": "Deep sleep observation",
    "rem_sleep_duration": "REM sleep observation",
    "light_sleep_duration": "Light sleep observation",
    "sleep_awake_duration": "Sleep awake observation",
    "sleep_latency": "Sleep latency observation",
    "exercise_minutes": "Exercise observation",
    "activity_minutes": "Activity observation",
    "weight": "Weight observation",
    "systolic_bp": "Systolic blood pressure observation",
    "diastolic_bp": "Diastolic blood pressure observation",
    "glucose_capillary": "Capillary glucose observation",
    "glucose_cgm_interstitial": "CGM glucose observation",
    "glucose": "Glucose observation",
}

_METRIC_CONSUMER_CATEGORY = {
    "heart_rate": "cardiovascular",
    "resting_hr": "cardiovascular",
    "steps": "activity_fitness",
    "exercise_minutes": "activity_fitness",
    "activity_minutes": "activity_fitness",
    "sleep_duration": "sleep",
    "sleep_score": "sleep",
    "deep_sleep_duration": "sleep",
    "rem_sleep_duration": "sleep",
    "light_sleep_duration": "sleep",
    "sleep_awake_duration": "sleep",
    "sleep_latency": "sleep",
    "energy_score": "sleep",
    "oxygen_saturation": "respiratory_oxygen",
    "spo2": "respiratory_oxygen",
    "respiratory_rate": "respiratory_oxygen",
    "systolic_bp": "blood_pressure",
    "diastolic_bp": "blood_pressure",
    "blood_pressure": "blood_pressure",
    "glucose": "glucose_diabetes",
    "glucose_fasting": "glucose_diabetes",
    "glucose_random": "glucose_diabetes",
    "glucose_capillary": "glucose_diabetes",
    "glucose_cgm_interstitial": "glucose_diabetes",
    "hba1c": "glucose_diabetes",
    "ecg_result": "ecg_cardiology",
    "heart_rhythm": "ecg_cardiology",
    "weight": "weight_body_metrics",
}

_HC_FILENAME = re.compile(
    r"^health_connect_(?P<metric>.+)_(?P<uuid>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.json$",
    re.IGNORECASE,
)
_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_SAMSUNG_ORIGIN = ("samsung", "shealth", "com.sec.android.app.shealth")


def metric_from_health_connect_filename(filename: str) -> str:
    name = str(filename or "").strip()
    match = _HC_FILENAME.match(name)
    if match:
        return canonicalize_metric(match.group("metric"))
    lower = name.lower()
    if lower.startswith("health_connect_"):
        parts = lower.replace(".json", "").split("_")
        if len(parts) >= 4:
            return canonicalize_metric("_".join(parts[2:-1]))
    return ""


def consumer_category_for_metric(metric: str | None) -> str:
    key = canonicalize_metric(metric or "")
    return _METRIC_CONSUMER_CATEGORY.get(key, "")


def consumer_category_label(category: str | None) -> str:
    key = str(category or "other")
    return CONSUMER_CATEGORY_LABELS.get(key) or CATEGORY_LABELS.get(key) or "Other"


def to_record_category(consumer_category: str | None) -> RecordCategory:
    key = str(consumer_category or "").lower()
    mapping = {
        "cardiovascular": RecordCategory.CARDIOVASCULAR,
        "activity_fitness": RecordCategory.ACTIVITY,
        "sleep": RecordCategory.SLEEP,
        "respiratory_oxygen": RecordCategory.RESPIRATORY,
        "glucose_diabetes": RecordCategory.GLUCOSE,
        "blood_pressure": RecordCategory.BLOOD_PRESSURE,
        "laboratory_report": RecordCategory.LABS,
        "medication": RecordCategory.MEDICATION,
        "imaging": RecordCategory.IMAGING,
        "hospital_clinical_report": RecordCategory.CLINICAL_DOCUMENT,
        "ecg_cardiology": RecordCategory.ECG,
        "kidney_renal": RecordCategory.KIDNEY,
        "weight_body_metrics": RecordCategory.WEIGHT,
    }
    return mapping.get(key, RecordCategory.OTHER)


def classify_consumer_record(
    *,
    filename: str | None = None,
    document_type: str | None = None,
    source_system: str | None = None,
    stored_category: str | None = None,
    metrics: list[str] | None = None,
    measured_at: str | None = None,
) -> dict[str, Any]:
    metric = ""
    for name in metrics or []:
        metric = canonicalize_metric(name) or metric
        if consumer_category_for_metric(metric):
            break
    if not metric:
        metric = metric_from_health_connect_filename(filename or "")

    classified = classify_health_record(
        document_type=document_type,
        filename=filename,
        source_system=source_system,
        measurements=[{"metric": name} for name in (metrics or [])],
    )
    category = consumer_category_for_metric(metric) or classified.get("primary_category") or stored_category or "other"
    if str(stored_category or "").lower() in CONSUMER_CATEGORY_LABELS and category == "other":
        category = str(stored_category).lower()
    title = display_title(
        filename=filename,
        document_type=document_type,
        source_system=source_system,
        metric=metric,
        measured_at=measured_at,
        category=category,
    )
    return {
        "display_title": title,
        "consumer_category": category,
        "consumer_category_label": consumer_category_label(category),
        "record_category": to_record_category(category),
        "metric": metric,
        "technical_filename": filename,
    }


def display_title(
    *,
    filename: str | None,
    document_type: str | None = None,
    source_system: str | None = None,
    metric: str | None = None,
    measured_at: str | None = None,
    category: str | None = None,
) -> str:
    name = str(filename or "").strip()
    hc_metric = metric or metric_from_health_connect_filename(name)
    if name.lower().startswith("health_connect_"):
        if hc_metric in METRIC_TITLES:
            return METRIC_TITLES[hc_metric]
        if hc_metric:
            return f"{hc_metric.replace('_', ' ').capitalize()} observation"
        return "Health Connect observation"

    pretty = _pretty_document_name(name)
    date_bit = _short_date(measured_at)
    source_bit = _source_facility(name, source_system, document_type)
    if source_bit and date_bit:
        return f"{source_bit} — {date_bit}"
    if source_bit:
        return source_bit
    if pretty and date_bit and pretty.lower() not in date_bit.lower():
        return f"{pretty} — {date_bit}"
    return pretty or "Clinical document"


def _pretty_document_name(filename: str) -> str:
    stem = str(filename or "").rsplit("/", 1)[-1]
    stem = re.sub(r"\.(json|pdf|png|jpe?g|txt)$", "", stem, flags=re.IGNORECASE)
    stem = _UUID.sub("", stem)
    stem = re.sub(r"[_-]+", " ", stem).strip(" ._-")
    stem = re.sub(r"\s+", " ", stem)
    if not stem or stem.lower() in {"health connect", "document", "upload"}:
        return ""
    return stem.title()


def _source_facility(filename: str, source_system: str | None, document_type: str | None) -> str:
    blob = " ".join(str(part or "") for part in (filename, source_system, document_type)).lower()
    if "lifelabs" in blob:
        return "LifeLabs Laboratory Results"
    if "brantford" in blob:
        return "Brantford General clinical report"
    if "laboratory" in blob or "lab_report" in blob:
        return "Laboratory report"
    if "discharge" in blob:
        return "Discharge report"
    if "imaging" in blob or "radiology" in blob:
        return "Imaging report"
    if "prescription" in blob or "medication" in blob:
        return "Prescription"
    if "referral" in blob:
        return "Referral"
    if "hospital" in blob or "emergency" in blob:
        return "Hospital / emergency report"
    return ""


def _short_date(value: str | None) -> str:
    text = str(value or "").strip()
    if len(text) < 10:
        return ""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return f"{dt.day} {dt.strftime('%b %Y')}"
    except Exception:
        try:
            dt = datetime.strptime(text[:10], "%Y-%m-%d")
            return f"{dt.day} {dt.strftime('%b %Y')}"
        except Exception:
            return text[:10]


def health_connect_source_label(data_origins: list[str] | None) -> str:
    blob = " ".join(str(origin or "").lower() for origin in (data_origins or []))
    if any(token in blob for token in _SAMSUNG_ORIGIN):
        return "Health Connect — Samsung Health"
    if blob.strip():
        return "Health Connect"
    return "Health Connect"


def source_identity_key(
    *,
    patient_id: str,
    metric: str,
    source_record_id: str | None,
    observation_id: str | None = None,
) -> str | None:
    """Deterministic uniqueness when a stable originating record ID exists."""
    source_id = str(source_record_id or "").strip()
    if source_id:
        return "|".join(
            (
                str(patient_id or "default-patient"),
                canonicalize_metric(metric) or str(metric or ""),
                source_id,
            )
        )
    obs_id = str(observation_id or "").strip()
    if obs_id:
        return "|".join(
            (
                str(patient_id or "default-patient"),
                canonicalize_metric(metric) or str(metric or ""),
                f"observation:{obs_id}",
            )
        )
    return None
