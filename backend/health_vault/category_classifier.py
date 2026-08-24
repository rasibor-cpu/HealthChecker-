"""
HC-201H — Health record category classifier (observational taxonomy only).

Does not diagnose. Classifies record type / measurement category for vault UX and trends.
"""

from __future__ import annotations

from typing import Any

CLASSIFIER_VERSION = "hc201h.category.v1"

PRIMARY_CATEGORIES = (
    "blood_pressure",
    "sleep",
    "ecg_cardiology",
    "glucose_diabetes",
    "kidney_renal",
    "laboratory_report",
    "weight_body_metrics",
    "medication",
    "respiratory_oxygen",
    "activity_fitness",
    "hospital_clinical_report",
    "symptom_record",
    "other",
)

CATEGORY_LABELS = {
    "blood_pressure": "Blood Pressure",
    "sleep": "Sleep",
    "ecg_cardiology": "ECG / Heart",
    "glucose_diabetes": "Glucose",
    "kidney_renal": "Kidney",
    "laboratory_report": "Labs",
    "weight_body_metrics": "Weight",
    "medication": "Medications",
    "respiratory_oxygen": "Respiratory",
    "activity_fitness": "Activity",
    "hospital_clinical_report": "Hospital / Clinical",
    "symptom_record": "Symptoms",
    "other": "Other",
}

# Metric → primary category votes
_METRIC_CATEGORY = {
    "systolic": "blood_pressure",
    "diastolic": "blood_pressure",
    "systolic_bp": "blood_pressure",
    "diastolic_bp": "blood_pressure",
    "pulse": "blood_pressure",
    "sleep_score": "sleep",
    "sleep_duration": "sleep",
    "deep_sleep": "sleep",
    "rem_sleep": "sleep",
    "sleep_latency": "sleep",
    "deep_sleep_duration": "sleep",
    "rem_sleep_duration": "sleep",
    "energy_score": "sleep",
    "hrv": "sleep",
    "hrv_rmssd": "sleep",
    "skin_temperature": "sleep",
    "skin_temperature_deviation": "sleep",
    "skin_temperature_delta": "sleep",
    "ecg_result": "ecg_cardiology",
    "heart_rhythm": "ecg_cardiology",
    "average_hr": "ecg_cardiology",
    "heart_rate": "ecg_cardiology",
    "resting_hr": "ecg_cardiology",
    "glucose": "glucose_diabetes",
    "glucose_fasting": "glucose_diabetes",
    "glucose_random": "glucose_diabetes",
    "glucose_postprandial": "glucose_diabetes",
    "glucose_cgm_interstitial": "glucose_diabetes",
    "glucose_capillary": "glucose_diabetes",
    "glucose_serum_plasma": "glucose_diabetes",
    "hba1c": "glucose_diabetes",
    "cgm_average": "glucose_diabetes",
    "cgm_time_in_range": "glucose_diabetes",
    "cgm_gmi": "glucose_diabetes",
    "egfr": "kidney_renal",
    "creatinine": "kidney_renal",
    "creatinine_serum": "kidney_renal",
    "creatinine_urine": "kidney_renal",
    "uacr": "kidney_renal",
    "protein": "kidney_renal",
    "protein_urine": "kidney_renal",
    "urea": "kidney_renal",
    "albumin_serum": "laboratory_report",
    "albumin_urine": "kidney_renal",
    "protein_total_serum": "laboratory_report",
    "calcium_total": "laboratory_report",
    "calcium_ionized": "laboratory_report",
    "troponin_i_hs": "ecg_cardiology",
    "hemoglobin": "laboratory_report",
    "hematocrit": "laboratory_report",
    "rbc": "laboratory_report",
    "wbc": "laboratory_report",
    "neutrophils": "laboratory_report",
    "sodium": "laboratory_report",
    "magnesium": "laboratory_report",
    "inr": "laboratory_report",
    "potassium": "laboratory_report",
    "weight": "weight_body_metrics",
    "bmi": "weight_body_metrics",
    "medication": "medication",
    "respiratory_rate": "respiratory_oxygen",
    "spo2": "respiratory_oxygen",
}

_DOC_TYPE_CATEGORY = {
    "samsung_health_ecg": "ecg_cardiology",
    "samsung_health_sleep": "sleep",
    "samsung_health_energy_score": "sleep",
    "blood_pressure_screenshot": "blood_pressure",
    "blood_glucose": "glucose_diabetes",
    "libre_cgm_report": "glucose_diabetes",
    "laboratory_pdf": "laboratory_report",
    "hospital_report": "hospital_clinical_report",
    "medication_report": "medication",
    "galaxy_watch_report": "activity_fitness",
}

_FILENAME_HINTS = (
    (("bp", "blood_pressure", "blood-pressure", "pressure"), "blood_pressure"),
    (("sleep", "rem", "deep_sleep"), "sleep"),
    (("ecg", "ekg", "rhythm"), "ecg_cardiology"),
    (("glucose", "libre", "cgm", "hba1c", "a1c"), "glucose_diabetes"),
    (("egfr", "creatinine", "uacr", "kidney", "renal"), "kidney_renal"),
    (("lab", "lifelabs", "labcorp", "panel"), "laboratory_report"),
    (("weight", "bmi", "scale"), "weight_body_metrics"),
    (("med", "rx", "prescription", "medication"), "medication"),
    (("spo2", "oxygen", "respiratory"), "respiratory_oxygen"),
    (("hospital", "discharge", "clinical"), "hospital_clinical_report"),
    (("symptom", "pain"), "symptom_record"),
)


def classify_health_record(
    *,
    document_type: str | None = None,
    filename: str | None = None,
    source_system: str | None = None,
    measurements: list[Any] | None = None,
    ocr_text: str | None = None,
    group_title: str | None = None,
) -> dict[str, Any]:
    """Return classification fields for a MedicalDocument (no diagnosis)."""
    votes: dict[str, float] = {}
    methods: list[str] = []

    def _vote(cat: str, weight: float, method: str) -> None:
        if cat not in PRIMARY_CATEGORIES:
            cat = "other"
        votes[cat] = votes.get(cat, 0.0) + weight
        if method not in methods:
            methods.append(method)

    dtype = str(document_type or "").lower()
    if dtype in _DOC_TYPE_CATEGORY:
        _vote(_DOC_TYPE_CATEGORY[dtype], 3.0, "document_type")

    name = str(filename or "").lower()
    for keys, cat in _FILENAME_HINTS:
        if any(k in name for k in keys):
            _vote(cat, 2.0, "filename")
            break

    src = str(source_system or "").lower()
    if "samsung" in src and "ecg" in (dtype + name):
        _vote("ecg_cardiology", 1.5, "source_system")
    if "samsung" in src and "sleep" in (dtype + name + str(group_title or "").lower()):
        _vote("sleep", 1.5, "source_system")

    metric_cats: set[str] = set()
    for m in measurements or []:
        metric = str(
            m.get("metric") if isinstance(m, dict) else getattr(m, "metric", "")
        ).lower()
        if metric in _METRIC_CATEGORY:
            cat = _METRIC_CATEGORY[metric]
            metric_cats.add(cat)
            _vote(cat, 2.5, "measurements")

    text = f"{ocr_text or ''} {group_title or ''}".lower()
    if text.strip():
        for keys, cat in _FILENAME_HINTS:
            if any(k in text for k in keys):
                _vote(cat, 1.0, "ocr_text")
                break

    if not votes:
        primary = "other"
        confidence = 0.35
        methods = ["fallback"]
    else:
        primary = max(votes.items(), key=lambda kv: kv[1])[0]
        total = sum(votes.values())
        confidence = min(0.99, max(0.4, votes[primary] / max(total, 1.0)))

    secondary = sorted(
        c for c in votes if c != primary and votes[c] >= 1.5
    )
    # Kidney labs also get laboratory_report secondary
    if primary == "kidney_renal" and "laboratory_report" not in secondary:
        secondary.append("laboratory_report")
    if primary == "glucose_diabetes" and "laboratory_report" in metric_cats:
        if "laboratory_report" not in secondary:
            secondary.append("laboratory_report")

    requires_review = confidence < 0.55 or primary == "other"

    return {
        "primary_category": primary,
        "secondary_categories": secondary,
        "classification_confidence": round(confidence, 3),
        "classification_method": "+".join(methods) if methods else "fallback",
        "classification_version": CLASSIFIER_VERSION,
        "requires_review": requires_review,
        "category_label": CATEGORY_LABELS.get(primary, primary),
    }
