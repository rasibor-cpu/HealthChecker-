"""HC-313A — Gmail attachment medical-document classifier.

Classification outputs one of three values:
    MEDICAL_DOCUMENT_CONFIRMED  — strong evidence the attachment is a medical record
    MEDICAL_DOCUMENT_UNCERTAIN  — some signals but not enough to confirm
    NOT_MEDICAL                 — no medical-document evidence

IMPORTANT DESIGN CONSTRAINTS
-----------------------------
A document CANNOT reach ACCEPT merely because:
    - filename contains "medical"
    - filename contains "report" or "results"
    - email subject mentions hospital/lab
    - sender appears health-related
    - MIME type is PDF or image

CONFIRMED classification requires sufficiently strong INTERNAL evidence
from the document content (text) or a combination of multiple strong
filename/metadata signals.

Filename + MIME alone is NEVER sufficient for CONFIRMED.  They can raise
confidence only when combined with strong text evidence.

Patient identity verification is a SEPARATE mandatory gate — it is NOT
part of this module.

The implementation is deterministic and fully testable without a live
Gmail connection or OCR provider.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.health_vault.acquisition.gmail_models import MedicalClassification


# ---------------------------------------------------------------------------
# Signal dictionaries
# ---------------------------------------------------------------------------

# Strong clinical/medical text signals — presence of several in document text
# raises classification confidence substantially.
_STRONG_TEXT_SIGNALS: frozenset[str] = frozenset(
    {
        "laboratory results",
        "lab results",
        "blood test",
        "complete blood count",
        "cbc",
        "metabolic panel",
        "lipid panel",
        "pathology report",
        "biopsy",
        "radiology report",
        "imaging report",
        "mri",
        "ct scan",
        "x-ray",
        "discharge summary",
        "discharge instructions",
        "operative report",
        "clinical notes",
        "physician notes",
        "diagnostic report",
        "lifelabs",
        "labcorp",
        "quest diagnostics",
        "mayo clinic",
        "clinical laboratory",
        "hematology",
        "reference range",
        "normal range",
        "result value",
        "specimen type",
        "creatinine",
        "hemoglobin",
        "hba1c",
        "cholesterol",
        "triglycerides",
        "white blood cell",
        "red blood cell",
        "platelet",
        "glomerular filtration",
        "egfr",
        "urinalysis",
        "ecg",
        "electrocardiogram",
        "ekg",
        "echo",
        "echocardiogram",
        "prescription",
        "medication list",
        "diagnos",  # prefix: diagnosis, diagnoses, diagnosed
        "icd-10",
        "icd-9",
        "cpt code",
        "npi",
        "ordering physician",
        "attending physician",
        "collected:",
        "reported:",
        "specimen id",
        "patient dob",
        "date of birth",
    }
)

# Strong filename-only signals — when MULTIPLE appear, raise confidence.
# NOTE: A SINGLE filename signal alone never reaches CONFIRMED.
_STRONG_FILENAME_SIGNALS: frozenset[str] = frozenset(
    {
        "lab",
        "laboratory",
        "labs",
        "lifelabs",
        "labcorp",
        "pathology",
        "radiology",
        "imaging",
        "biopsy",
        "discharge",
        "clinical",
        "diagnostic",
        "ecg",
        "ekg",
        "mri",
        "xray",
        "xr",
        "ct",
        "prescription",
        "rx",
        "bloodwork",
        "blood",
        "urine",
        "specimen",
        "cbcreport",
        "lipid",
        "metabolic",
        "hba1c",
        "glucose",
    }
)

# Weak signals — common words found in non-medical files; must NOT trigger CONFIRMED
_WEAK_ONLY_SIGNALS: frozenset[str] = frozenset(
    {
        "report",
        "results",
        "medical",
        "health",
        "hospital",
        "clinic",
        "doctor",
        "patient",
    }
)

# Minimum number of distinct strong TEXT signals required for CONFIRMED from text
_MIN_STRONG_TEXT_FOR_CONFIRMED: int = 2

# Minimum number of strong FILENAME signals for UNCERTAIN (never CONFIRMED from filename alone)
_MIN_FILENAME_FOR_UNCERTAIN: int = 1


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


@dataclass
class ClassificationResult:
    """Result of medical-document classification."""

    classification: MedicalClassification
    confidence: float
    signals_found: list[str] = field(default_factory=list)
    detail: str = ""


class GmailClassifier:
    """Stateless medical-document classifier for Gmail attachments.

    Usage::

        classifier = GmailClassifier()
        result = classifier.classify(
            filename="lifelabs_report.pdf",
            mime_type="application/pdf",
            text_content="LIFELABS RESULTS\\nCREATININE 88 umol/L Reference Range ...",
        )
    """

    def __init__(self, config: Any | None = None) -> None:
        self._confidence_threshold: float = (
            config.medical_confidence_threshold if config is not None else 0.60
        )

    def classify(
        self,
        *,
        filename: str,
        mime_type: str,
        text_content: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> ClassificationResult:
        """Classify whether an attachment is a medical record.

        Parameters
        ----------
        filename:
            Original attachment filename (used for extension + keyword signals).
        mime_type:
            MIME type as reported by Gmail.
        text_content:
            Decoded text content, if available (e.g. from JSON or OCR).
            For binary PDFs/images without OCR, pass "".
        metadata:
            Optional supplementary metadata dict (currently unused in v1).

        Returns
        -------
        ClassificationResult
        """
        fname_lower = Path(filename).stem.lower() if filename else ""
        text_lower = (text_content or "").lower()
        signals: list[str] = []

        # --- Text signals (primary source of truth) ---
        strong_text_hits: list[str] = []
        for signal in _STRONG_TEXT_SIGNALS:
            if signal in text_lower:
                strong_text_hits.append(signal)
        signals.extend(f"text:{s}" for s in strong_text_hits)

        # --- Filename signals ---
        filename_hits: list[str] = []
        for signal in _STRONG_FILENAME_SIGNALS:
            if signal in fname_lower:
                filename_hits.append(signal)
        signals.extend(f"filename:{s}" for s in filename_hits)

        # Detect weak-only signals (to avoid false-positive CONFIRMED)
        weak_only_hits = [w for w in _WEAK_ONLY_SIGNALS if w in fname_lower]

        # --- MIME type contribution (additive only) ---
        is_medical_mime = mime_type.startswith((
            "application/pdf",
            "image/png",
            "image/jpeg",
            "image/jpg",
        ))

        # ---------------------------------------------------------------------------
        # Classification logic
        # ---------------------------------------------------------------------------
        # CONFIRMED requires:
        #   ≥ _MIN_STRONG_TEXT_FOR_CONFIRMED distinct strong text signals
        # OR
        #   ≥ 1 strong text signal AND ≥ 2 strong filename signals AND medical MIME
        # OR
        #   ≥ 4 strong text signals (even without filename signals)
        #
        # UNCERTAIN requires:
        #   ≥ 1 strong text OR ≥ 1 strong filename AND is_medical_mime
        #   but does NOT meet CONFIRMED criteria
        #
        # NOT_MEDICAL:
        #   No strong signals found (weak-only signals do NOT qualify)
        # ---------------------------------------------------------------------------

        n_text = len(strong_text_hits)
        n_fname = len(filename_hits)

        if n_text >= _MIN_STRONG_TEXT_FOR_CONFIRMED:
            confidence = min(0.95, 0.60 + n_text * 0.05 + (0.05 if is_medical_mime else 0.0))
            return ClassificationResult(
                classification=MedicalClassification.CONFIRMED,
                confidence=confidence,
                signals_found=signals,
                detail=f"Strong text evidence: {n_text} signals",
            )

        if (
            n_text >= 1
            and n_fname >= 2
            and is_medical_mime
        ):
            confidence = 0.65
            return ClassificationResult(
                classification=MedicalClassification.CONFIRMED,
                confidence=confidence,
                signals_found=signals,
                detail="Combined text + filename + MIME evidence",
            )

        if n_text >= 1 or (n_fname >= _MIN_FILENAME_FOR_UNCERTAIN and is_medical_mime):
            confidence = 0.30 + min(0.20, n_text * 0.08 + n_fname * 0.04)
            return ClassificationResult(
                classification=MedicalClassification.UNCERTAIN,
                confidence=confidence,
                signals_found=signals,
                detail="Insufficient evidence for CONFIRMED",
            )

        # Not enough evidence
        confidence = min(0.20, len(weak_only_hits) * 0.03)
        return ClassificationResult(
            classification=MedicalClassification.NOT_MEDICAL,
            confidence=confidence,
            signals_found=signals,
            detail="No strong medical-document signals found",
        )


__all__ = [
    "ClassificationResult",
    "GmailClassifier",
    "MedicalClassification",
]
