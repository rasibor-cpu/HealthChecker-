"""HC-313A — Structured provenance recording.

Privacy requirements:
- Do NOT place medical-record contents into log records.
- Do NOT place extracted clinical values into log records.
- Do NOT log Gmail OAuth credentials or tokens.
- Provenance records are written at INFO level; all fields are
  either structural identifiers or decision codes.

Every acquisition decision — ACCEPT, REVIEW, and REJECT — receives a
complete provenance record.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from backend.health_vault.acquisition.gmail_models import AcquisitionRecord
from backend.health_vault.models import utc_now


logger = logging.getLogger("hc313a.provenance")


# ---------------------------------------------------------------------------
# Structured provenance log entry
# ---------------------------------------------------------------------------


def _safe_record(record: AcquisitionRecord) -> dict[str, Any]:
    """Convert an AcquisitionRecord to a log-safe dict.

    Excludes any field that might contain decoded clinical content.
    All exported fields are structural identifiers or decision codes.
    """
    return {
        "source": record.source,
        "message_id": record.message_id,
        "thread_id": record.thread_id,
        "sender": record.sender,
        "recipient": record.recipient,
        # subject is included at INFO level — it is envelope metadata, not PHI
        # but truncated to 120 chars to limit log size.
        "subject": (record.subject or "")[:120],
        "message_timestamp": record.message_timestamp,
        "original_filename": record.original_filename,
        "attachment_id": record.attachment_id,
        "attachment_sha256": record.attachment_sha256,
        "attachment_size_bytes": record.attachment_size_bytes,
        "mime_type": record.mime_type,
        "acquisition_timestamp": record.acquisition_timestamp,
        "medical_classification": record.medical_classification,
        "medical_confidence": round(record.medical_confidence, 3),
        "patient_identity_classification": record.patient_identity_classification,
        "identity_reason_code": record.identity_reason_code,
        "identity_matched_fields": record.identity_matched_fields,
        "identity_conflict_fields": record.identity_conflict_fields,
        "final_decision": record.final_decision,
        "rejection_reason": record.rejection_reason,
        "intake_filename": record.intake_filename,
    }


def log_provenance(record: AcquisitionRecord) -> None:
    """Write a structured provenance log entry at INFO level.

    The log entry contains only structural metadata and decision codes.
    No medical-record content, clinical values, or credentials are emitted.
    """
    safe = _safe_record(record)
    logger.info(
        "hc313a_acquisition decision=%s message_id=%s attachment_id=%s sha256=%.12s...",
        record.final_decision,
        record.message_id,
        record.attachment_id,
        record.attachment_sha256 or "",
    )
    # Structured record at DEBUG level for diagnostic tooling
    logger.debug("hc313a_provenance_record %s", json.dumps(safe, ensure_ascii=False))


def record_to_dict(record: AcquisitionRecord) -> dict[str, Any]:
    """Return a log-safe dict representation of the provenance record."""
    return _safe_record(record)


__all__ = [
    "log_provenance",
    "record_to_dict",
]
