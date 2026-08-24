"""HC321-UAT12J — semantic clinical observation classes.

Do not collapse clinically different measurements into one trend key
just because analyte names are related.
"""

from __future__ import annotations

from typing import Any

SEMANTICS_VERSION = "hc321uat12j.semantics.v1"

# Identity classes used for storage + like-for-like trends.
OBSERVATION_CLASSES = {
    "glucose_fasting",
    "glucose_random",
    "glucose_postprandial",
    "glucose_cgm_interstitial",
    "glucose_capillary",
    "glucose_serum_plasma",
    "glucose",  # unspecified legacy; never a merge of the classes above
    "hba1c",
    "creatinine_serum",
    "creatinine_urine",
    "creatinine",
    "egfr",
    "urea",
    "albumin_serum",
    "albumin_urine",
    "protein_total_serum",
    "protein_urine",
    "protein",
    "calcium_total",
    "calcium_ionized",
    "troponin_i_hs",
    "troponin_t_hs",
    "hemoglobin",
    "hematocrit",
    "rbc",
    "wbc",
    "neutrophils",
    "sodium",
    "potassium",
    "magnesium",
    "inr",
    "systolic_bp",
    "systolic_bp_sitting",
    "systolic_bp_standing",
    "systolic_bp_supine",
    "diastolic_bp",
    "diastolic_bp_sitting",
    "diastolic_bp_standing",
    "diastolic_bp_supine",
}

# Alias → specific class. Related names must NOT map onto a sibling class.
CLASS_ALIASES = {
    "fasting_glucose": "glucose_fasting",
    "glucose_fasting": "glucose_fasting",
    "fpg": "glucose_fasting",
    "random_glucose": "glucose_random",
    "glucose_random": "glucose_random",
    "glucose_nonfasting": "glucose_random",
    "postprandial_glucose": "glucose_postprandial",
    "glucose_postprandial": "glucose_postprandial",
    "pp_glucose": "glucose_postprandial",
    "cgm_glucose": "glucose_cgm_interstitial",
    "glucose_cgm_interstitial": "glucose_cgm_interstitial",
    "interstitial_glucose": "glucose_cgm_interstitial",
    "capillary_glucose": "glucose_capillary",
    "glucose_capillary": "glucose_capillary",
    "poc_glucose": "glucose_capillary",
    "serum_glucose": "glucose_serum_plasma",
    "plasma_glucose": "glucose_serum_plasma",
    "glucose_serum": "glucose_serum_plasma",
    "glucose_plasma": "glucose_serum_plasma",
    "glucose_serum_plasma": "glucose_serum_plasma",
    "serum_creatinine": "creatinine_serum",
    "creatinine_serum": "creatinine_serum",
    "plasma_creatinine": "creatinine_serum",
    "urine_creatinine": "creatinine_urine",
    "creatinine_urine": "creatinine_urine",
    "serum_albumin": "albumin_serum",
    "albumin_serum": "albumin_serum",
    "urine_albumin": "albumin_urine",
    "albumin_urine": "albumin_urine",
    "microalbumin": "albumin_urine",
    "total_protein": "protein_total_serum",
    "protein_total": "protein_total_serum",
    "protein_total_serum": "protein_total_serum",
    "urine_protein": "protein_urine",
    "protein_urine": "protein_urine",
    "calcium_total": "calcium_total",
    "total_calcium": "calcium_total",
    "calcium_ionized": "calcium_ionized",
    "ionized_calcium": "calcium_ionized",
    "ica": "calcium_ionized",
    "troponin_i_hs": "troponin_i_hs",
    "hs_troponin_i": "troponin_i_hs",
    "troponinihs": "troponin_i_hs",
    "high_sensitivity_troponin_i": "troponin_i_hs",
    "troponin_t_hs": "troponin_t_hs",
    "hs_troponin_t": "troponin_t_hs",
    "hemoglobin": "hemoglobin",
    "hgb": "hemoglobin",
    "hb": "hemoglobin",
    "haemoglobin": "hemoglobin",
    "hematocrit": "hematocrit",
    "hct": "hematocrit",
    "pcv": "hematocrit",
    "rbc": "rbc",
    "red_blood_cells": "rbc",
    "wbc": "wbc",
    "white_blood_cells": "wbc",
    "neutrophils": "neutrophils",
    "neutrophil_count": "neutrophils",
    "sodium": "sodium",
    "na": "sodium",
    "potassium": "potassium",
    "k": "potassium",
    "magnesium": "magnesium",
    "mg": "magnesium",
    "urea": "urea",
    "bun": "urea",
    "inr": "inr",
    "egfr": "egfr",
    "estimated_gfr": "egfr",
}

GLYCEMIC_CLASSES = (
    "glucose_fasting",
    "glucose_random",
    "glucose_postprandial",
    "glucose_cgm_interstitial",
    "glucose_capillary",
    "glucose_serum_plasma",
    "glucose",
    "hba1c",
)
RENAL_CLASSES = (
    "creatinine_serum",
    "creatinine_urine",
    "creatinine",
    "egfr",
    "urea",
    "uacr",
    "albumin_urine",
    "protein_urine",
)
GLYCEMIC_TREND_CLASSES = tuple(c for c in GLYCEMIC_CLASSES if c != "hba1c")

_FASTING = ("fasting", "fasted", "npo", "pre-meal", "premeal")
_RANDOM = ("random", "non-fasting", "nonfasting", "casual", "non fasting")
_POST = ("postprandial", "post-prandial", "post prandial", "pp ", " 2hr", "2-hour", "2 hour")
_CGM = ("cgm", "interstitial", "libre", "dexcom", "freestyle")
_CAPILLARY = ("capillary", "fingerstick", "finger stick", "poc")
_SERUM = ("serum", "plasma", "venous", "blood")
_URINE = ("urine", "urinary", "ua ")
_SITTING = ("sitting", "seated")
_STANDING = ("standing", "erect")
_SUPINE = ("supine", "lying", "recumbent")


def _blob(*parts: Any) -> str:
    return " ".join(str(part or "") for part in parts).lower()


def observation_family(observation_class: str | None) -> str:
    key = str(observation_class or "")
    if key.startswith("glucose") or key == "hba1c":
        return "glycemic"
    if key in {"creatinine", "creatinine_serum", "creatinine_urine", "egfr", "urea", "uacr"}:
        return "renal"
    if key.startswith("systolic") or key.startswith("diastolic"):
        return "blood_pressure"
    if key.startswith("albumin"):
        return "albumin"
    if key.startswith("protein"):
        return "protein"
    if key.startswith("calcium"):
        return "calcium"
    if key.startswith("troponin"):
        return "troponin"
    return key or "unknown"


def related_observation_classes(observation_class: str | None) -> tuple[str, ...]:
    """Domain neighbours for side-by-side views. Never a merge key."""
    family = observation_family(observation_class)
    if family == "glycemic":
        return GLYCEMIC_CLASSES
    if family == "renal":
        return RENAL_CLASSES
    if family == "blood_pressure":
        return (
            "systolic_bp",
            "diastolic_bp",
            "systolic_bp_sitting",
            "diastolic_bp_sitting",
            "systolic_bp_standing",
            "diastolic_bp_standing",
            "systolic_bp_supine",
            "diastolic_bp_supine",
        )
    return (str(observation_class or ""),)


def classify_clinical_observation(
    *,
    metric: str | None,
    original_name: str | None = None,
    specimen: str | None = None,
    context: str | None = None,
    document_type: str | None = None,
    source_system: str | None = None,
) -> dict[str, Any]:
    """Return a specific observation class without merging sibling analytes."""
    raw = str(metric or "unknown").strip()
    key = raw.lower().replace("-", "_").replace(" ", "_")
    blob = _blob(raw, original_name, specimen, context, document_type, source_system)
    specimen_l = str(specimen or "").lower()
    context_l = str(context or "").lower()

    if key in CLASS_ALIASES:
        klass = CLASS_ALIASES[key]
    elif key in OBSERVATION_CLASSES:
        klass = key
    else:
        klass = key

    if klass in {"glucose", "blood_glucose"} or key in {"glucose", "blood_glucose"}:
        dtype = str(document_type or "")
        if dtype == "libre_cgm_report" or any(token in blob for token in _CGM):
            klass = "glucose_cgm_interstitial"
        elif any(token in blob for token in _FASTING):
            klass = "glucose_fasting"
        elif any(token in blob for token in _POST):
            klass = "glucose_postprandial"
        elif any(token in blob for token in _CAPILLARY):
            klass = "glucose_capillary"
        elif any(token in blob for token in _RANDOM):
            klass = "glucose_random"
        elif "serum" in blob or "plasma" in blob:
            klass = "glucose_serum_plasma"

    if klass in {"creatinine"} or key == "creatinine":
        if any(token in blob for token in _URINE) or "urine" in specimen_l:
            klass = "creatinine_urine"
        elif any(token in blob for token in _SERUM) or specimen_l in {"serum", "plasma", "blood", "whole blood"}:
            klass = "creatinine_serum"

    if klass in {"albumin"} or key == "albumin":
        if "urine" in blob:
            klass = "albumin_urine"
        elif any(token in blob for token in _SERUM) or specimen_l in {"serum", "plasma", "blood"}:
            klass = "albumin_serum"

    if klass in {"protein", "total_protein"} or key in {"protein", "total_protein"}:
        if "urine" in blob:
            klass = "protein_urine"
        elif "total" in blob or specimen_l in {"serum", "plasma", "blood"}:
            klass = "protein_total_serum"

    if klass in {"calcium"} or key == "calcium":
        if "ion" in blob:
            klass = "calcium_ionized"
        else:
            klass = "calcium_total"

    if "troponin" in blob:
        if "t" in key.split("_")[-1:] or "troponin_t" in blob or "troponin t" in blob:
            klass = "troponin_t_hs" if "hs" in blob or "high" in blob else "troponin_t_hs"
        else:
            klass = "troponin_i_hs"

    if klass in {"systolic_bp", "systolic"} or key in {"systolic", "systolic_bp"}:
        if any(token in context_l or token in blob for token in _SITTING):
            klass = "systolic_bp_sitting"
        elif any(token in context_l or token in blob for token in _STANDING):
            klass = "systolic_bp_standing"
        elif any(token in context_l or token in blob for token in _SUPINE):
            klass = "systolic_bp_supine"
        else:
            klass = "systolic_bp"

    if klass in {"diastolic_bp", "diastolic"} or key in {"diastolic", "diastolic_bp"}:
        if any(token in context_l or token in blob for token in _SITTING):
            klass = "diastolic_bp_sitting"
        elif any(token in context_l or token in blob for token in _STANDING):
            klass = "diastolic_bp_standing"
        elif any(token in context_l or token in blob for token in _SUPINE):
            klass = "diastolic_bp_supine"
        else:
            klass = "diastolic_bp"

    return {
        "observation_class": klass,
        "family": observation_family(klass),
        "specimen": specimen or None,
        "context": context or None,
        "original_analyte_name": original_name or raw,
        "semantics_version": SEMANTICS_VERSION,
    }
