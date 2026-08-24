"""
HC-201H / HC321-UAT12J — Canonical metric names and unit-compatible normalization.
"""

from __future__ import annotations

from typing import Any

from backend.health_vault.clinical_semantics import (
    CLASS_ALIASES,
    GLYCEMIC_TREND_CLASSES,
    RENAL_CLASSES,
    classify_clinical_observation,
)
from backend.health_vault.unit_conversion import (
    CANONICAL_UNITS as _CLINICAL_CANONICAL_UNITS,
    NORMALIZER_VERSION,
    UNIT_NORMALIZATION_REQUIRES_VERIFICATION,
    convert_reference_range,
    to_canonical,
)

# Alias → canonical metric
METRIC_ALIASES = {
    "systolic": "systolic_bp",
    "systolic_bp": "systolic_bp",
    "diastolic": "diastolic_bp",
    "diastolic_bp": "diastolic_bp",
    "pulse": "heart_rate",
    "hr": "heart_rate",
    "heart_rate": "heart_rate",
    "average_hr": "average_hr",
    "resting_hr": "resting_hr",
    "hrv": "hrv_rmssd",
    "hrv_rmssd": "hrv_rmssd",
    "sleep_duration": "sleep_duration",
    "deep_sleep": "deep_sleep_duration",
    "deep_sleep_duration": "deep_sleep_duration",
    "rem_sleep": "rem_sleep_duration",
    "rem_sleep_duration": "rem_sleep_duration",
    "skin_temperature_deviation": "skin_temperature_delta",
    "skin_temperature_delta": "skin_temperature_delta",
    "respiratory_rate": "respiratory_rate",
    "glucose": "glucose",
    "hba1c": "hba1c",
    "egfr": "egfr",
    "creatinine": "creatinine",
    "weight": "weight",
    "bmi": "bmi",
    "sleep_score": "sleep_score",
    "energy_score": "energy_score",
    "oxygen_saturation": "oxygen_saturation",
    "spo2": "oxygen_saturation",
    "steps": "steps",
    "activity_minutes": "activity_minutes",
    "exercise_minutes": "exercise_minutes",
    "ldl": "ldl",
    "ldl_c": "ldl",
    "ldl_cholesterol": "ldl",
    "hdl": "hdl",
    "triglycerides": "triglycerides",
}

CANONICAL_UNITS = {
    "systolic_bp": "mmHg",
    "diastolic_bp": "mmHg",
    "heart_rate": "bpm",
    "resting_hr": "bpm",
    "hrv_rmssd": "ms",
    "sleep_duration": "min",
    "deep_sleep_duration": "min",
    "rem_sleep_duration": "min",
    "sleep_latency": "min",
    "respiratory_rate": "breaths/min",
    "skin_temperature_delta": "C",
    "hba1c": "%",
    "weight": "kg",
    "bmi": "kg/m2",
    "sleep_score": "score",
    "energy_score": "score",
    "oxygen_saturation": "%",
    "steps": "count",
    "activity_minutes": "min",
    "exercise_minutes": "min",
    "ldl": "mg/dL",
    "hdl": "mg/dL",
    "triglycerides": "mg/dL",
}
CANONICAL_UNITS.update(_CLINICAL_CANONICAL_UNITS)

TREND_METRICS = {
    "systolic_bp",
    "systolic_bp_sitting",
    "systolic_bp_standing",
    "systolic_bp_supine",
    "diastolic_bp",
    "diastolic_bp_sitting",
    "diastolic_bp_standing",
    "diastolic_bp_supine",
    "hba1c",
    "weight",
    "resting_hr",
    "heart_rate",
    "average_hr",
    "hrv_rmssd",
    "sleep_duration",
    "sleep_score",
    "respiratory_rate",
    "ldl",
    "hemoglobin",
    "hematocrit",
    "sodium",
    "potassium",
    "magnesium",
    "inr",
    "troponin_i_hs",
    "troponin_t_hs",
    "albumin_serum",
    "protein_total_serum",
    "calcium_total",
    "calcium_ionized",
}
TREND_METRICS.update(GLYCEMIC_TREND_CLASSES)
TREND_METRICS.update(c for c in RENAL_CLASSES if c != "uacr")
TREND_METRICS.add("uacr")

MONITORING_TREND_METRICS = {
    "heart_rate",
    "resting_hr",
    "average_hr",
    "hrv_rmssd",
    "sleep_duration",
    "sleep_score",
    "respiratory_rate",
    "oxygen_saturation",
    "steps",
    "activity_minutes",
    "exercise_minutes",
}

_DURATION_HOURS = {"h", "hr", "hrs", "hour", "hours"}
_DURATION_MIN = {"min", "mins", "minute", "minutes", "m"}


def canonicalize_metric(metric: str | None) -> str:
    key = str(metric or "unknown").strip().lower()
    if key in CLASS_ALIASES:
        return CLASS_ALIASES[key]
    return METRIC_ALIASES.get(key, key)


def normalize_measurement(
    raw: dict[str, Any] | Any,
    *,
    document_type: str | None = None,
    source_system: str | None = None,
    source_facility: str | None = None,
) -> dict[str, Any]:
    """
    Return a measurement dict with:
      metric (observation class), units (canonical when known),
      value (canonical), original_value, original_units,
      unit_compatible, normalization_version
    Originals are never overwritten.
    """
    if hasattr(raw, "to_dict"):
        data = raw.to_dict()
    else:
        data = dict(raw or {})

    original_metric = str(
        data.get("original_analyte_name")
        or data.get("original_metric")
        or data.get("metric")
        or "unknown"
    )
    original_value = data.get("original_value")
    original_units = data.get("original_units")
    if original_value is None:
        original_value = data.get("value")
    if original_units is None:
        original_units = data.get("units")

    classified = classify_clinical_observation(
        metric=str(data.get("metric") or original_metric),
        original_name=original_metric,
        specimen=data.get("specimen"),
        context=data.get("context") or data.get("collection_context"),
        document_type=document_type or data.get("document_type"),
        source_system=source_system or data.get("source_system"),
    )
    canonical = classified["observation_class"]
    if canonical in METRIC_ALIASES and canonical not in CLASS_ALIASES:
        canonical = METRIC_ALIASES.get(canonical, canonical)

    value = original_value
    units = original_units
    compatible = True
    notes: list[str] = []
    conversion_flag = None

    target = CANONICAL_UNITS.get(canonical)
    u = str(original_units or "").strip()
    u_l = u.lower()

    try:
        num = float(original_value) if original_value is not None and original_value != "" else None
    except (TypeError, ValueError):
        num = None
        compatible = False
        notes.append("non_numeric_value")

    if num is not None and canonical in {
        "sleep_duration",
        "deep_sleep_duration",
        "rem_sleep_duration",
        "sleep_latency",
    }:
        if not u or u_l in _DURATION_HOURS:
            value = round(num * 60.0, 3)
            units = "min"
            notes.append("hours_to_minutes")
        elif u_l in _DURATION_MIN:
            value = num
            units = "min"
        else:
            compatible = False
            notes.append("unknown_duration_unit")
    elif num is not None and target:
        converted = to_canonical(
            observation_class=canonical,
            original_value=original_value,
            original_units=original_units,
        )
        value = converted["value"]
        units = converted["units"]
        compatible = bool(converted["unit_compatible"])
        conversion_flag = converted.get("conversion_flag")
        notes.extend(converted.get("normalization_notes") or [])
    elif num is not None and canonical == "respiratory_rate":
        if u_l in {"/min", "br/min", "breaths/min", "bpm", ""}:
            value = num
            units = "breaths/min"
        else:
            compatible = False
            notes.append("unit_mismatch_no_conversion")
            value = num
            units = u

    canonical_range = data.get("canonical_reference_range")
    if data.get("reference_range") and original_units and units and str(original_units) != str(units):
        canonical_range = convert_reference_range(
            data.get("reference_range"),
            str(original_units),
            str(units),
            canonical,
        )

    facility = source_facility or data.get("source_facility")
    data.update(
        {
            "metric": canonical,
            "observation_class": canonical,
            "value": value,
            "units": units,
            "original_metric": data.get("original_metric") or original_metric,
            "original_analyte_name": original_metric,
            "original_value": original_value,
            "original_units": original_units,
            "specimen": classified.get("specimen") or data.get("specimen"),
            "context": classified.get("context") or data.get("context"),
            "source_facility": facility,
            "canonical_reference_range": canonical_range,
            "unit_compatible": compatible,
            "conversion_flag": conversion_flag,
            "normalization_version": NORMALIZER_VERSION,
            "normalization_notes": notes,
            "semantics_version": classified.get("semantics_version"),
        }
    )
    if conversion_flag == UNIT_NORMALIZATION_REQUIRES_VERIFICATION and UNIT_NORMALIZATION_REQUIRES_VERIFICATION not in notes:
        notes.append(UNIT_NORMALIZATION_REQUIRES_VERIFICATION)
        data["normalization_notes"] = notes
    return data


def normalize_measurements(items: list[Any], **kwargs: Any) -> list[dict[str, Any]]:
    return [normalize_measurement(m, **kwargs) for m in items or []]
