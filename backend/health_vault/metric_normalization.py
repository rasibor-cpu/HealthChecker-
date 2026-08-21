"""
HC-201H — Canonical metric names and unit-compatible normalization for trends.
"""

from __future__ import annotations

from typing import Any

NORMALIZER_VERSION = "hc201h.metric.v1"

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
    "systolic_bp": "systolic_bp",
    "diastolic_bp": "diastolic_bp",
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
    "glucose": "mg/dL",
    "hba1c": "%",
    "egfr": "mL/min/1.73m2",
    "creatinine": "umol/L",
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

# Metrics allowed into classical / clinical trend engine
TREND_METRICS = {
    "systolic_bp",
    "diastolic_bp",
    "glucose",
    "hba1c",
    "egfr",
    "creatinine",
    "weight",
    "resting_hr",
    "heart_rate",
    "average_hr",
    "hrv_rmssd",
    "sleep_duration",
    "sleep_score",
    "respiratory_rate",
    "ldl",
}

# HC-321-C1: monitoring / Health Connect observational trend eligibility.
# Kept separate from TREND_METRICS so wearable SpO2/steps/etc. do not dilute
# stricter clinical/lab eligibility rules, while still participating in trends.
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

# Unit families that may convert
_DURATION_HOURS = {"h", "hr", "hrs", "hour", "hours"}
_DURATION_MIN = {"min", "mins", "minute", "minutes", "m"}
_GLUCOSE_MG = {"mg/dl", "mg/dL"}
_GLUCOSE_MMOL = {"mmol/l", "mmol/L"}
_CREAT_UMOL = {"umol/l", "µmol/l", "μmol/l", "umol/L"}
_CREAT_MG = {"mg/dl", "mg/dL"}


def canonicalize_metric(metric: str | None) -> str:
    key = str(metric or "unknown").strip().lower()
    return METRIC_ALIASES.get(key, key)


def normalize_measurement(raw: dict[str, Any] | Any) -> dict[str, Any]:
    """
    Return a measurement dict with:
      metric (canonical), units (canonical when known),
      value (normalized), original_value, original_units,
      unit_compatible, normalization_version
    """
    if hasattr(raw, "to_dict"):
        data = raw.to_dict()
    else:
        data = dict(raw or {})

    original_metric = str(data.get("metric") or "unknown")
    canonical = canonicalize_metric(original_metric)
    original_value = data.get("value")
    original_units = data.get("units")
    value = original_value
    units = original_units
    compatible = True
    notes: list[str] = []

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
            # Historical vault used hours — convert to minutes
            if not u or u_l in _DURATION_HOURS:
                value = round(num * 60.0, 3) if (not u or u_l in _DURATION_HOURS) else num
                if not u or u_l in _DURATION_HOURS:
                    units = "min"
                    notes.append("hours_to_minutes")
        elif u_l in _DURATION_MIN:
            value = num
            units = "min"
        else:
            compatible = False
            notes.append("unknown_duration_unit")
    elif num is not None and canonical == "glucose":
        if not u or u_l in {x.lower() for x in _GLUCOSE_MG}:
            value = num
            units = "mg/dL"
        elif u_l in {x.lower() for x in _GLUCOSE_MMOL}:
            value = round(num * 18.0182, 3)
            units = "mg/dL"
            notes.append("mmol_L_to_mg_dL")
        else:
            compatible = False
            notes.append("incompatible_glucose_unit")
    elif num is not None and canonical == "creatinine":
        if not u or u_l in {x.lower() for x in _CREAT_UMOL}:
            value = num
            units = "umol/L"
        elif u_l in {x.lower() for x in _CREAT_MG}:
            value = round(num * 88.4, 3)
            units = "umol/L"
            notes.append("mg_dL_to_umol_L")
        else:
            compatible = False
            notes.append("incompatible_creatinine_unit")
    elif num is not None and target:
        if not u or u.replace("²", "2") == target or u_l == target.lower():
            value = num
            units = target
        elif u and u_l not in {target.lower(), target.lower().replace("²", "2")}:
            # Same family names with slight spelling — accept common RR aliases
            if canonical == "respiratory_rate" and u_l in {"/min", "br/min", "breaths/min", "bpm"}:
                value = num
                units = "breaths/min"
            else:
                # Do not convert unknown incompatible units
                compatible = u_l == target.lower()
                if not compatible:
                    notes.append("unit_mismatch_no_conversion")
                    value = num
                    units = u or target
        else:
            value = num
            units = target or u

    data.update(
        {
            "metric": canonical,
            "value": value,
            "units": units,
            "original_metric": original_metric,
            "original_value": original_value,
            "original_units": original_units,
            "unit_compatible": compatible,
            "normalization_version": NORMALIZER_VERSION,
            "normalization_notes": notes,
        }
    )
    return data


def normalize_measurements(items: list[Any]) -> list[dict[str, Any]]:
    return [normalize_measurement(m) for m in items or []]
