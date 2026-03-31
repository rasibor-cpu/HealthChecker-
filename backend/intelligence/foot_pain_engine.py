from __future__ import annotations
from typing import Dict, List


class FootPainEngine:
    """
    HealthChecker+ Foot Pain Diagnostic Engine

    Determines likely cause of foot pain:
    - Medication-induced edema (amlodipine, etc.)
    - Kidney-related swelling
    - Gout (uric acid)
    - Neuropathy (glucose-related)
    """

    def evaluate(
        self,
        pain_location: str,
        swelling: bool,
        symmetry: str,
        onset_speed: str,
        recent_med_change: bool,
        glucose: float,
        kidney_status: str,
        symptoms: List[str],
    ) -> Dict:

        result = {
            "likely_cause": None,
            "confidence": 0.0,
            "notes": [],
            "actions": [],
        }

        # --- Medication-induced edema ---
        if recent_med_change and swelling:
            if symmetry in ["one_side", "asymmetrical"]:
                result["likely_cause"] = "medication_edema"
                result["confidence"] = 0.85
                result["notes"].append("Likely amlodipine-related fluid shift")
                result["actions"].append("Use consistent brand and dosage (avoid splitting tablets)")
                result["actions"].append("Elevate feet 10–15 minutes, 2–3 times daily")
                result["actions"].append("Reduce salt intake temporarily")
                return result

        # --- Kidney-related swelling ---
        if swelling and kidney_status == "reduced_eGFR":
            if symmetry == "both":
                result["likely_cause"] = "renal_edema"
                result["confidence"] = 0.75
                result["notes"].append("Possible fluid retention from reduced kidney function")
                result["actions"].append("Monitor hydration balance (avoid excess or low intake)")
                result["actions"].append("Check creatinine/eGFR if swelling persists")
                return result

        # --- Gout ---
        if "sharp_pain" in symptoms or "joint_focus" in symptoms:
            if pain_location in ["big_toe", "toe_joint"]:
                result["likely_cause"] = "gout"
                result["confidence"] = 0.7
                result["notes"].append("Possible uric acid crystal inflammation")
                result["actions"].append("Check uric acid levels")
                result["actions"].append("Reduce red meat and high purine foods")
                return result

        # --- Neuropathy ---
        if "tingling" in symptoms or "burning" in symptoms:
            if glucose > 140:
                result["likely_cause"] = "neuropathy"
                result["confidence"] = 0.65
                result["notes"].append("Glucose-related nerve irritation")
                result["actions"].append("Tighten glucose control")
                return result

        # --- Default ---
        result["likely_cause"] = "undetermined"
        result["confidence"] = 0.4
        result["notes"].append("Monitor symptoms progression")
        result["actions"].append("Re-evaluate if symptoms persist beyond 48 hours")

        return result
