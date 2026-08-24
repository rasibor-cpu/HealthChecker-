"""HC321-UAT12J — analyte-aware unit conversion.

Storage: original value/unit → canonical value/unit.
Display: re-render from canonical using the user's region preference.
Never overwrite original source values. Never convert from an already
canonical value when originals are present (no cumulative reconversion).
"""

from __future__ import annotations

import re
from typing import Any

from backend.health_vault.clinical_semantics import observation_family

NORMALIZER_VERSION = "hc321uat12j.clinical.v1"
UNIT_NORMALIZATION_REQUIRES_VERIFICATION = "UNIT_NORMALIZATION_REQUIRES_VERIFICATION"

# Canonical (storage) units — SI-leaning, matching common Canadian lab reporting
# for new clinical classes. Legacy glucose remains mg/dL for compatibility.
CANONICAL_UNITS = {
    "glucose": "mg/dL",
    "glucose_fasting": "mg/dL",
    "glucose_random": "mg/dL",
    "glucose_postprandial": "mg/dL",
    "glucose_cgm_interstitial": "mg/dL",
    "glucose_capillary": "mg/dL",
    "glucose_serum_plasma": "mg/dL",
    "hba1c": "%",
    "creatinine": "umol/L",
    "creatinine_serum": "umol/L",
    "creatinine_urine": "umol/L",
    "egfr": "mL/min/1.73m2",
    "urea": "mmol/L",
    "albumin_serum": "g/L",
    "albumin_urine": "mg/L",
    "protein_total_serum": "g/L",
    "protein_urine": "g/L",
    "calcium_total": "mmol/L",
    "calcium_ionized": "mmol/L",
    "troponin_i_hs": "ng/L",
    "troponin_t_hs": "ng/L",
    "hemoglobin": "g/L",
    "hematocrit": "L/L",
    "rbc": "x10^12/L",
    "wbc": "x10^9/L",
    "neutrophils": "x10^9/L",
    "sodium": "mmol/L",
    "potassium": "mmol/L",
    "magnesium": "mmol/L",
    "inr": "ratio",
    "systolic_bp": "mmHg",
    "systolic_bp_sitting": "mmHg",
    "systolic_bp_standing": "mmHg",
    "systolic_bp_supine": "mmHg",
    "diastolic_bp": "mmHg",
    "diastolic_bp_sitting": "mmHg",
    "diastolic_bp_standing": "mmHg",
    "diastolic_bp_supine": "mmHg",
}

GLUCOSE_MGDL_PER_MMOL = 18.0182
CREAT_UMOL_PER_MGDL = 88.4
UREA_MMOL_PER_BUN_MGDL = 0.357
CA_MMOL_PER_MGDL = 0.2495
MG_MMOL_PER_MGDL = 0.4114
ALBUMIN_GL_PER_GDL = 10.0
HB_GL_PER_GDL = 10.0

_REGION_DEFAULTS = {
    "CA": {"glucose": "mmol/L", "creatinine": "umol/L", "urea": "mmol/L", "calcium": "mmol/L", "albumin": "g/L", "hemoglobin": "g/L", "hematocrit": "L/L"},
    "NG": {"glucose": "mmol/L", "creatinine": "umol/L", "urea": "mmol/L", "calcium": "mmol/L", "albumin": "g/L", "hemoglobin": "g/L", "hematocrit": "L/L"},
    "GB": {"glucose": "mmol/L", "creatinine": "umol/L", "urea": "mmol/L", "calcium": "mmol/L", "albumin": "g/L", "hemoglobin": "g/L", "hematocrit": "L/L"},
    "US": {"glucose": "mg/dL", "creatinine": "mg/dL", "urea": "mg/dL", "calcium": "mg/dL", "albumin": "g/dL", "hemoglobin": "g/dL", "hematocrit": "%"},
}

_DISPLAY_ROUND = {
    "mmol/L": 1,
    "mg/dL": 0,
    "umol/L": 0,
    "g/L": 0,
    "g/dL": 1,
    "L/L": 2,
    "%": 0,
    "ng/L": 0,
    "mL/min/1.73m2": 0,
    "x10^12/L": 2,
    "x10^9/L": 1,
    "mmHg": 0,
    "ratio": 1,
}


def _norm_unit(unit: str | None) -> str:
    text = str(unit or "").strip()
    text = text.replace("µ", "u").replace("μ", "u").replace("²", "2")
    text = text.replace(" ", "")
    return text


def _unit_key(unit: str | None) -> str:
    return _norm_unit(unit).lower()


def _is_glucose_class(klass: str) -> bool:
    return klass.startswith("glucose")


def _family_pref_key(klass: str) -> str:
    if _is_glucose_class(klass):
        return "glucose"
    if klass.startswith("creatinine"):
        return "creatinine"
    if klass.startswith("calcium"):
        return "calcium"
    if klass.startswith("albumin"):
        return "albumin"
    if klass == "hemoglobin":
        return "hemoglobin"
    if klass == "hematocrit":
        return "hematocrit"
    if klass == "urea":
        return "urea"
    return klass


def region_display_unit(observation_class: str, region: str | None, overrides: dict[str, str] | None = None) -> str:
    klass = str(observation_class or "")
    canonical = CANONICAL_UNITS.get(klass) or ""
    overrides = overrides or {}
    fam = _family_pref_key(klass)
    if fam in overrides:
        return overrides[fam]
    if klass in overrides:
        return overrides[klass]
    region_key = str(region or "").strip().upper()
    if region_key in _REGION_DEFAULTS and fam in _REGION_DEFAULTS[region_key]:
        return _REGION_DEFAULTS[region_key][fam]
    return canonical


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def round_display(value: float, units: str) -> float:
    places = _DISPLAY_ROUND.get(units, 2)
    return round(float(value), places)


def convert_value(value: float, src_unit: str, dest_unit: str, observation_class: str) -> tuple[float | None, str | None]:
    """Return (converted, error_flag). None flag means success."""
    src = _unit_key(src_unit)
    dest = _unit_key(dest_unit)
    if not src or not dest:
        return None, UNIT_NORMALIZATION_REQUIRES_VERIFICATION
    if src == dest:
        return float(value), None
    klass = str(observation_class or "")

    def glucose(v: float, s: str, d: str) -> float | None:
        if s in {"mg/dl"} and d in {"mmol/l"}:
            return v / GLUCOSE_MGDL_PER_MMOL
        if s in {"mmol/l"} and d in {"mg/dl"}:
            return v * GLUCOSE_MGDL_PER_MMOL
        return None

    def creatinine(v: float, s: str, d: str) -> float | None:
        if s in {"mg/dl"} and d in {"umol/l"}:
            return v * CREAT_UMOL_PER_MGDL
        if s in {"umol/l"} and d in {"mg/dl"}:
            return v / CREAT_UMOL_PER_MGDL
        return None

    converted: float | None = None
    if _is_glucose_class(klass):
        converted = glucose(value, src, dest)
    elif klass.startswith("creatinine"):
        converted = creatinine(value, src, dest)
    elif klass == "urea":
        if src in {"mg/dl"} and dest in {"mmol/l"}:
            converted = value * UREA_MMOL_PER_BUN_MGDL
        elif src in {"mmol/l"} and dest in {"mg/dl"}:
            converted = value / UREA_MMOL_PER_BUN_MGDL
    elif klass.startswith("calcium"):
        if src in {"mg/dl"} and dest in {"mmol/l"}:
            converted = value * CA_MMOL_PER_MGDL
        elif src in {"mmol/l"} and dest in {"mg/dl"}:
            converted = value / CA_MMOL_PER_MGDL
    elif klass == "magnesium":
        if src in {"mg/dl"} and dest in {"mmol/l"}:
            converted = value * MG_MMOL_PER_MGDL
        elif src in {"mmol/l"} and dest in {"mg/dl"}:
            converted = value / MG_MMOL_PER_MGDL
    elif klass in {"albumin_serum", "protein_total_serum", "protein_urine"}:
        if src in {"g/dl"} and dest in {"g/l"}:
            converted = value * ALBUMIN_GL_PER_GDL
        elif src in {"g/l"} and dest in {"g/dl"}:
            converted = value / ALBUMIN_GL_PER_GDL
    elif klass == "hemoglobin":
        if src in {"g/dl"} and dest in {"g/l"}:
            converted = value * HB_GL_PER_GDL
        elif src in {"g/l"} and dest in {"g/dl"}:
            converted = value / HB_GL_PER_GDL
    elif klass == "hematocrit":
        if src in {"%"} and dest in {"l/l"}:
            converted = value / 100.0
        elif src in {"l/l"} and dest in {"%"}:
            converted = value * 100.0
    elif klass in {"troponin_i_hs", "troponin_t_hs"}:
        if src in {"ng/ml", "ug/l"} and dest in {"ng/l"}:
            converted = value * 1000.0
        elif src in {"ng/l"} and dest in {"ng/ml", "ug/l"}:
            converted = value / 1000.0
    elif src == dest:
        converted = value

    if converted is None:
        return None, UNIT_NORMALIZATION_REQUIRES_VERIFICATION
    return float(converted), None


def convert_reference_range(range_text: str | None, src_unit: str, dest_unit: str, observation_class: str) -> str | None:
    text = str(range_text or "").strip()
    if not text:
        return None
    if _unit_key(src_unit) == _unit_key(dest_unit):
        return text
    numbers = list(re.finditer(r"-?\d+(?:\.\d+)?", text))
    if not numbers:
        return text
    out = text
    # Replace from the end so offsets stay valid.
    for match in reversed(numbers):
        num = float(match.group(0))
        converted, flag = convert_value(num, src_unit, dest_unit, observation_class)
        if flag or converted is None:
            return text
        rendered = str(round_display(converted, dest_unit))
        out = out[: match.start()] + rendered + out[match.end() :]
    return out


def to_canonical(
    *,
    observation_class: str,
    original_value: Any,
    original_units: str | None,
    existing_value: Any = None,
    existing_units: str | None = None,
) -> dict[str, Any]:
    """Convert originals to canonical storage units. Never convert canonical→canonical."""
    klass = str(observation_class or "unknown")
    target = CANONICAL_UNITS.get(klass)
    notes: list[str] = []
    flag = None
    compatible = True
    original_num = _to_float(original_value)

    if original_num is None:
        return {
            "value": original_value if original_value is not None else existing_value,
            "units": original_units or existing_units or target,
            "unit_compatible": False,
            "conversion_flag": None,
            "normalization_notes": ["non_numeric_value"],
        }

    src = original_units
    if not src:
        flag = UNIT_NORMALIZATION_REQUIRES_VERIFICATION
        compatible = False
        notes.append("missing_unit")
        return {
            "value": original_num,
            "units": src,
            "unit_compatible": compatible,
            "conversion_flag": flag,
            "normalization_notes": notes,
        }

    if not target:
        return {
            "value": original_num,
            "units": src,
            "unit_compatible": True,
            "conversion_flag": None,
            "normalization_notes": notes,
        }

    converted, conv_flag = convert_value(original_num, src, target, klass)
    if conv_flag or converted is None:
        compatible = False
        flag = conv_flag or UNIT_NORMALIZATION_REQUIRES_VERIFICATION
        notes.append("ambiguous_or_unknown_unit")
        return {
            "value": original_num,
            "units": src,
            "unit_compatible": compatible,
            "conversion_flag": flag,
            "normalization_notes": notes,
        }
    return {
        "value": converted,
        "units": target,
        "unit_compatible": True,
        "conversion_flag": None,
        "normalization_notes": notes,
        "canonical_reference_source_unit": src,
    }


def apply_display_units(
    measurement: dict[str, Any],
    *,
    region: str | None = None,
    unit_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Re-render canonical storage into the user's preferred units. Does not mutate originals."""
    data = dict(measurement or {})
    klass = str(data.get("observation_class") or data.get("metric") or "")
    canonical_units = data.get("units") or CANONICAL_UNITS.get(klass)
    canonical_value = _to_float(data.get("value"))
    dest = region_display_unit(klass, region, unit_overrides)
    original_range = data.get("reference_range")
    src_range_unit = data.get("original_units") or canonical_units
    display_range = original_range
    if original_range and src_range_unit and dest and _unit_key(str(src_range_unit)) != _unit_key(dest):
        display_range = convert_reference_range(original_range, str(src_range_unit), dest, klass)
    if canonical_value is None or not dest:
        data["display_value"] = data.get("value")
        data["display_units"] = dest or canonical_units
        data["display_reference_range"] = display_range
        return data
    if _unit_key(dest) == _unit_key(str(canonical_units or "")):
        display_value = canonical_value
    else:
        converted, flag = convert_value(canonical_value, str(canonical_units), dest, klass)
        if flag or converted is None:
            data["display_value"] = canonical_value
            data["display_units"] = canonical_units
            data["display_reference_range"] = original_range
            data["display_conversion_flag"] = flag
            return data
        display_value = converted
    data["display_value"] = round_display(display_value, dest)
    data["display_units"] = dest
    data["display_reference_range"] = display_range
    data["observation_family"] = observation_family(klass)
    return data
