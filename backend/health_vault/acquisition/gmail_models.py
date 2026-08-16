"""HC-313A Gmail medical-record acquisition — data models.

All decision types, enums, and structured records used across the acquisition
pipeline. No PHI is stored in log fields; all string fields that touch
medical-record content are kept out of ordinary logging.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from backend.health_vault.models import utc_now


# ---------------------------------------------------------------------------
# Decision enums
# ---------------------------------------------------------------------------


class AcquisitionDecision(str, Enum):
    """Terminal acquisition decision for a single Gmail attachment."""

    ACCEPT = "ACCEPT"
    REVIEW = "REVIEW"
    REJECT = "REJECT"


class MedicalClassification(str, Enum):
    """Medical-document classification result."""

    CONFIRMED = "MEDICAL_DOCUMENT_CONFIRMED"
    UNCERTAIN = "MEDICAL_DOCUMENT_UNCERTAIN"
    NOT_MEDICAL = "NOT_MEDICAL"


class IdentityReasonCode(str, Enum):
    """Structured patient-identity comparison reason codes."""

    PATIENT_IDENTITY_MATCH = "PATIENT_IDENTITY_MATCH"
    PATIENT_NAME_MISSING = "PATIENT_NAME_MISSING"
    PATIENT_IDENTITY_AMBIGUOUS = "PATIENT_IDENTITY_AMBIGUOUS"
    PATIENT_NAME_MISMATCH = "PATIENT_NAME_MISMATCH"
    PATIENT_DOB_CONFLICT = "PATIENT_DOB_CONFLICT"
    PATIENT_SECONDARY_ID_CONFLICT = "PATIENT_SECONDARY_ID_CONFLICT"


# ---------------------------------------------------------------------------
# Gmail message + attachment
# ---------------------------------------------------------------------------


@dataclass
class GmailMessage:
    """Envelope-level Gmail message metadata (no body/content stored here)."""

    message_id: str
    thread_id: str | None
    sender: str
    recipient: str
    subject: str
    timestamp: str  # ISO-8601 UTC


@dataclass
class GmailAttachment:
    """A single attachment as retrieved from Gmail."""

    attachment_id: str
    filename: str
    mime_type: str
    size_bytes: int
    content: bytes
    sha256: str = ""  # computed by acquirer after retrieval


# ---------------------------------------------------------------------------
# Identity verification
# ---------------------------------------------------------------------------


@dataclass
class PatientIdentityResult:
    """Structured result of patient-identity comparison.

    ``decision`` expresses the acquisition-eligibility outcome:
      ACCEPT  → PATIENT_IDENTITY_MATCH (proceed to final safety gates)
      REVIEW  → name missing / ambiguous / secondary conflict
      REJECT  → clear name mismatch or hard conflict
    """

    decision: AcquisitionDecision
    reason_code: IdentityReasonCode
    matched_fields: list[str] = field(default_factory=list)
    conflict_fields: list[str] = field(default_factory=list)
    detail: str = ""  # privacy-safe human description (no extracted clinical values)


# ---------------------------------------------------------------------------
# Acquisition record (structured provenance)
# ---------------------------------------------------------------------------


@dataclass
class AcquisitionRecord:
    """Complete structured provenance record for one Gmail attachment decision.

    IMPORTANT:
    - Do NOT put medical-record content into this record.
    - Do NOT put extracted clinical values into any string field.
    - All fields that could be logged are deliberately limited to:
        envelope metadata, structural identifiers, decision codes.
    """

    # Source
    source: str = "gmail"

    # Gmail envelope
    message_id: str = ""
    thread_id: str | None = None
    sender: str = ""
    recipient: str = ""
    subject: str = ""
    message_timestamp: str = ""

    # Attachment identity
    original_filename: str = ""
    attachment_id: str = ""
    attachment_sha256: str = ""
    attachment_size_bytes: int = 0
    mime_type: str = ""

    # Acquisition timing
    acquisition_timestamp: str = field(default_factory=utc_now)

    # Classification
    medical_classification: str = ""
    medical_confidence: float = 0.0

    # Identity
    patient_identity_classification: str = ""
    identity_reason_code: str = ""
    identity_matched_fields: list[str] = field(default_factory=list)
    identity_conflict_fields: list[str] = field(default_factory=list)

    # Final decision
    final_decision: str = ""
    rejection_reason: str = ""  # reason code only — no clinical content

    # Handoff (ACCEPT only)
    intake_filename: str | None = None  # basename written to hc_intake/incoming/


# ---------------------------------------------------------------------------
# Gmail connector protocol (abstraction boundary for testability)
# ---------------------------------------------------------------------------


class GmailConnectorError(RuntimeError):
    """Raised when the Gmail connector encounters an unrecoverable error."""


class GmailAttachmentRetrievalError(GmailConnectorError):
    """Raised when a specific attachment cannot be retrieved."""


__all__ = [
    "AcquisitionDecision",
    "AcquisitionRecord",
    "GmailAttachment",
    "GmailAttachmentRetrievalError",
    "GmailConnectorError",
    "GmailMessage",
    "IdentityReasonCode",
    "MedicalClassification",
    "PatientIdentityResult",
]
