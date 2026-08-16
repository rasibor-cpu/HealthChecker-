"""HC-313A — Gmail medical-record acquirer (main orchestration).

Architecture
------------

GmailConnectorProtocol (abstraction boundary)
    ↓
GmailAcquirer.run_scan()
    For each Gmail message:
        For each attachment:
            1. Check idempotency (AcquisitionStateStore) → skip if already seen
            2. Check extension/MIME/size → REJECT unsupported
            3. Retrieve attachment bytes (GmailConnectorProtocol.get_attachment())
            4. Compute SHA-256
            5. Check content-level idempotency → skip if sha256 already acquired
            6. Classify medical content (GmailClassifier)
            7. Extract patient identity fields from document
            8. Verify patient identity (PatientIdentityVerifier) — HARD SAFETY GATE
            9. Make final decision (ACCEPT / REVIEW / REJECT)
           10. Record provenance (log_provenance)
           11. Mark in AcquisitionStateStore
           12. If ACCEPT: write atomically to hc_intake/incoming/ → HC-312

One failed attachment MUST NOT prevent independent candidates from being evaluated.
Gmail outage → FAIL CLOSED (no ingestion, no silent loss).

SAFETY BOUNDARIES
-----------------
- ACCEPT requires BOTH MEDICAL_DOCUMENT_CONFIRMED AND PATIENT_IDENTITY_MATCH.
- Neither alone is sufficient.
- Gmail mailbox ownership, email sender, recipient, subject, and filename are
  NOT patient-identity signals.
- No Gmail credentials or tokens are stored here.
- No VaultStore direct writes.
- No new ingestion pipeline — handoff goes to hc_intake/incoming/ only.

PRODUCTION GMAIL CONNECTOR
---------------------------
The GmailConnectorProtocol is a typing.Protocol.  Production use requires
a concrete implementation using google-auth + google-api-python-client with
a valid OAuth credential.  That credential MUST NOT be stored in source.
If not present → RESULT=HC313A_GMAIL_RUNTIME_CONNECTOR_REQUIRED.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Protocol, runtime_checkable

from backend.health_vault.acquisition.acquisition_state import AcquisitionStateStore
from backend.health_vault.acquisition.gmail_classifier import GmailClassifier
from backend.health_vault.acquisition.gmail_config import (
    GmailAcquisitionConfig,
    get_default_config,
)
from backend.health_vault.acquisition.gmail_models import (
    AcquisitionDecision,
    AcquisitionRecord,
    GmailAttachment,
    GmailAttachmentRetrievalError,
    GmailConnectorError,
    GmailMessage,
    IdentityReasonCode,
    MedicalClassification,
    PatientIdentityResult,
)
from backend.health_vault.acquisition.patient_identity import PatientIdentityVerifier
from backend.health_vault.acquisition.provenance import log_provenance, record_to_dict
from backend.health_vault.models import utc_now


logger = logging.getLogger("hc313a.acquirer")


# ---------------------------------------------------------------------------
# Gmail connector protocol (abstraction boundary)
# ---------------------------------------------------------------------------


@runtime_checkable
class GmailConnectorProtocol(Protocol):
    """Abstraction over the Gmail API.

    Tests supply a MockGmailConnector; production supplies a real OAuth
    connector.  Both implement this protocol.
    """

    def list_messages(self, *, label_filter: str = "") -> list[GmailMessage]:
        """Return candidate Gmail messages (envelope metadata only)."""
        ...

    def list_attachments(self, message: GmailMessage) -> list[GmailAttachment]:
        """Return attachment stubs for a message (content may be empty)."""
        ...

    def get_attachment_bytes(
        self, message_id: str, attachment_id: str
    ) -> bytes:
        """Download and return the raw bytes of a specific attachment.

        Raises GmailAttachmentRetrievalError on transient or permanent failure.
        """
        ...


# ---------------------------------------------------------------------------
# Patient identity extraction (placeholder — real extraction from text/JSON)
# ---------------------------------------------------------------------------


def _extract_patient_fields_from_text(text: str) -> dict[str, str]:
    """Extract patient identity fields from decoded document text.

    Looks for structured patterns commonly found in medical reports.
    Returns a dict with keys: patient_name, date_of_birth, sex, mrn.
    Empty strings indicate the field was not found.

    This extraction is best-effort and conservative: it will return missing
    fields rather than produce incorrect extractions.
    """
    fields: dict[str, str] = {
        "patient_name": "",
        "date_of_birth": "",
        "sex": "",
        "mrn": "",
    }
    if not text:
        return fields

    text_lower = text.lower()

    # Patient name patterns (common in lab reports)
    name_patterns = [
        r"patient[\s:]+name[\s:]+([A-Za-z][A-Za-z ,.\-']+)",
        r"patient[\s:]+([A-Za-z][A-Za-z ,.\-']+)\s*\n",
        r"name[\s:]+([A-Za-z][A-Za-z ,.\-']{2,})",
        r"patient\s+name\s*:?\s*([A-Za-z][A-Za-z ,.\-']+)",
    ]
    for pat in name_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip().rstrip(",:")
            # Sanity: must have at least 2 words or "LAST, FIRST" format
            if len(candidate.split()) >= 2 or "," in candidate:
                fields["patient_name"] = candidate
                break

    # Date of birth patterns
    dob_patterns = [
        r"(?:date\s+of\s+birth|dob|birth\s+date|born)[\s:]+(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})",
        r"(?:date\s+of\s+birth|dob|birth\s+date|born)[\s:]+(\d{4}[/\-\.]\d{1,2}[/\-\.]\d{1,2})",
    ]
    for pat in dob_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            fields["date_of_birth"] = m.group(1).strip()
            break

    # Sex / gender
    sex_patterns = [
        r"(?:sex|gender)[\s:]+([MF]|male|female)\b",
    ]
    for pat in sex_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            fields["sex"] = m.group(1).strip()
            break

    # MRN
    mrn_patterns = [
        r"(?:mrn|medical\s+record[\s#]+|patient\s+id)[\s:#]*([A-Za-z0-9\-]{4,20})",
    ]
    for pat in mrn_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            fields["mrn"] = m.group(1).strip()
            break

    return fields


def _extract_patient_fields_from_json(data: dict[str, Any]) -> dict[str, str]:
    """Extract patient identity fields from a parsed JSON medical record.

    Recognizes common JSON structures used by HC-312 test fixtures and
    structured medical JSON exports.
    """
    fields: dict[str, str] = {
        "patient_name": "",
        "date_of_birth": "",
        "sex": "",
        "mrn": "",
    }
    if not isinstance(data, dict):
        return fields

    # Flatten common nested locations
    patient_section: dict[str, Any] = (
        data.get("patient")
        or data.get("patient_info")
        or data.get("subject")
        or {}
    )

    def _get(*keys: str) -> str:
        for key in keys:
            v = data.get(key) or patient_section.get(key)
            if v and isinstance(v, str):
                return v.strip()
        return ""

    fields["patient_name"] = _get("patient_name", "name", "patient_full_name", "full_name")
    fields["date_of_birth"] = _get("date_of_birth", "dob", "birth_date", "birthdate")
    fields["sex"] = _get("sex", "gender")
    fields["mrn"] = _get("mrn", "patient_id", "medical_record_number")

    return fields


# ---------------------------------------------------------------------------
# Filename sanitization
# ---------------------------------------------------------------------------

_UNSAFE_CHARS = re.compile(r"[^\w\-. ]")
_MULTI_UNDERSCORE = re.compile(r"_+")


def _sanitize_filename(filename: str) -> str:
    """Return a safe basename for writing to the intake directory."""
    name = Path(filename).name
    stem = Path(name).stem
    suffix = Path(name).suffix.lower()
    stem = _UNSAFE_CHARS.sub("_", stem)
    stem = _MULTI_UNDERSCORE.sub("_", stem).strip("_")
    stem = stem[:100]  # limit length
    return f"{stem}{suffix}" if stem else f"attachment{suffix}"


# ---------------------------------------------------------------------------
# Acquisition summary
# ---------------------------------------------------------------------------


@dataclass
class AcquisitionSummary:
    """Privacy-safe summary of a single acquisition scan run."""

    started_at: str = field(default_factory=utc_now)
    finished_at: str = ""
    messages_scanned: int = 0
    attachments_evaluated: int = 0
    already_acquired: int = 0
    rejected_format: int = 0
    rejected_classification: int = 0
    rejected_identity: int = 0
    sent_to_review: int = 0
    accepted: int = 0
    errors: int = 0
    records: list[dict[str, Any]] = field(default_factory=list)
    gmail_auth_success: bool = False
    handoff_success: bool = False


# ---------------------------------------------------------------------------
# Main acquirer
# ---------------------------------------------------------------------------


class GmailAcquirer:
    """Orchestrates Gmail medical-record acquisition.

    Parameters
    ----------
    connector:
        A ``GmailConnectorProtocol`` implementation.  For tests, use
        ``MockGmailConnector``.  For production, supply a real OAuth connector.
    config:
        ``GmailAcquisitionConfig`` instance.  Defaults to production settings.
    verifier:
        ``PatientIdentityVerifier`` instance.  Must be constructed from the
        registered user's profile (``PatientIdentityVerifier.from_store(store)``).
    """

    def __init__(
        self,
        *,
        connector: GmailConnectorProtocol,
        config: GmailAcquisitionConfig | None = None,
        verifier: PatientIdentityVerifier,
    ) -> None:
        self._connector = connector
        self._config = config or get_default_config()
        self._verifier = verifier
        self._classifier = GmailClassifier(self._config)
        self._state = AcquisitionStateStore(self._config.acquisition_state_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_scan(self) -> AcquisitionSummary:
        """Execute one acquisition scan cycle.

        - Fail closed on Gmail outage.
        - One failed attachment does not prevent others.
        - Returns a privacy-safe summary (counts only; no clinical content).
        """
        summary = AcquisitionSummary()
        summary.started_at = utc_now()

        # Fetch candidate messages — fail closed on connector error
        try:
            messages = self._connector.list_messages(
                label_filter=self._config.gmail_label_filter
            )
        except GmailConnectorError as exc:
            logger.error("hc313a_gmail_outage connector_error=%s", type(exc).__name__)
            summary.finished_at = utc_now()
            summary.errors += 1
            return summary

        messages = messages[: self._config.max_messages_per_scan]
        summary.messages_scanned = len(messages)

        for message in messages:
            try:
                stubs = self._connector.list_attachments(message)
            except Exception as exc:
                logger.warning(
                    "hc313a_list_attachments_failed message_id=%s error=%s",
                    message.message_id,
                    type(exc).__name__,
                )
                summary.errors += 1
                continue

            for stub in stubs:
                try:
                    record = self._process_attachment(message, stub, summary)
                    if record is not None:
                        summary.records.append(record_to_dict(record))
                except Exception as exc:
                    logger.error(
                        "hc313a_attachment_unhandled_error message_id=%s attachment_id=%s error=%s",
                        message.message_id,
                        stub.attachment_id,
                        type(exc).__name__,
                    )
                    summary.errors += 1

        summary.finished_at = utc_now()
        logger.info(
            "hc313a_scan_complete accepted=%d review=%d rejected=%d errors=%d",
            summary.accepted,
            summary.sent_to_review,
            summary.rejected_format + summary.rejected_classification + summary.rejected_identity,
            summary.errors,
        )
        return summary

    # ------------------------------------------------------------------
    # Internal per-attachment processing
    # ------------------------------------------------------------------

    def _process_attachment(
        self,
        message: GmailMessage,
        stub: GmailAttachment,
        summary: AcquisitionSummary,
    ) -> AcquisitionRecord | None:
        """Process one attachment through all safety gates."""
        summary.attachments_evaluated += 1

        record = AcquisitionRecord(
            message_id=message.message_id,
            thread_id=message.thread_id,
            sender=message.sender,
            recipient=message.recipient,
            subject=message.subject,
            message_timestamp=message.timestamp,
            original_filename=stub.filename,
            attachment_id=stub.attachment_id,
            attachment_size_bytes=stub.size_bytes,
            mime_type=stub.mime_type,
        )

        # --- Gate 1: Format check (before retrieval) ---
        suffix = Path(stub.filename).suffix.lower()
        if suffix not in self._config.allowed_extensions:
            record.final_decision = AcquisitionDecision.REJECT
            record.rejection_reason = "unsupported_extension"
            summary.rejected_format += 1
            self._state.mark_acquired(
                message_id=message.message_id,
                attachment_id=stub.attachment_id,
                sha256="",
                final_decision=record.final_decision,
                original_filename=stub.filename,
            )
            log_provenance(record)
            return record

        if stub.size_bytes > self._config.max_attachment_bytes:
            record.final_decision = AcquisitionDecision.REJECT
            record.rejection_reason = "attachment_too_large"
            summary.rejected_format += 1
            self._state.mark_acquired(
                message_id=message.message_id,
                attachment_id=stub.attachment_id,
                sha256="",
                final_decision=record.final_decision,
                original_filename=stub.filename,
            )
            log_provenance(record)
            return record

        # --- Gate 2: Structural idempotency (pre-retrieval) ---
        # sha256 is unknown at this point; check message+attachment identity only
        if self._state.is_already_acquired(
            message_id=message.message_id,
            attachment_id=stub.attachment_id,
            sha256="",
        ):
            summary.already_acquired += 1
            logger.debug(
                "hc313a_already_acquired message_id=%s attachment_id=%s",
                message.message_id,
                stub.attachment_id,
            )
            return None

        # --- Gate 3: Retrieve content ---
        try:
            content = self._connector.get_attachment_bytes(
                message.message_id, stub.attachment_id
            )
        except GmailAttachmentRetrievalError as exc:
            logger.warning(
                "hc313a_retrieval_failure message_id=%s attachment_id=%s error=%s",
                message.message_id,
                stub.attachment_id,
                type(exc).__name__,
            )
            summary.errors += 1
            return None

        sha256 = hashlib.sha256(content).hexdigest()
        record.attachment_sha256 = sha256
        record.attachment_size_bytes = len(content)

        # --- Gate 4: Content-level idempotency ---
        if self._state.is_already_acquired(
            message_id=message.message_id,
            attachment_id=stub.attachment_id,
            sha256=sha256,
        ):
            summary.already_acquired += 1
            logger.debug(
                "hc313a_content_deduplicated sha256=%.12s...", sha256
            )
            return None

        # --- Gate 5: Decode text for classification + identity extraction ---
        text_content = self._decode_text(content, stub.mime_type, stub.filename)
        json_data: dict[str, Any] | None = None
        if suffix == ".json":
            import json as _json
            try:
                json_data = _json.loads(content.decode("utf-8", errors="replace"))
            except Exception:
                json_data = None

        # --- Gate 6: Medical document classification ---
        classification = self._classifier.classify(
            filename=stub.filename,
            mime_type=stub.mime_type,
            text_content=text_content,
        )
        record.medical_classification = classification.classification.value
        record.medical_confidence = classification.confidence

        if classification.classification == MedicalClassification.NOT_MEDICAL:
            record.final_decision = AcquisitionDecision.REJECT
            record.rejection_reason = "not_medical_document"
            summary.rejected_classification += 1
            self._state.mark_acquired(
                message_id=message.message_id,
                attachment_id=stub.attachment_id,
                sha256=sha256,
                final_decision=record.final_decision,
                original_filename=stub.filename,
            )
            log_provenance(record)
            return record

        if classification.classification == MedicalClassification.UNCERTAIN:
            # Uncertain → REVIEW (cannot auto-accept without CONFIRMED)
            record.final_decision = AcquisitionDecision.REVIEW
            record.rejection_reason = "medical_classification_uncertain"
            record.patient_identity_classification = "NOT_CHECKED"
            record.identity_reason_code = ""
            summary.sent_to_review += 1
            self._state.mark_acquired(
                message_id=message.message_id,
                attachment_id=stub.attachment_id,
                sha256=sha256,
                final_decision=record.final_decision,
                original_filename=stub.filename,
            )
            log_provenance(record)
            return record

        # classification is CONFIRMED — proceed to identity gate

        # --- Gate 7: Patient identity extraction ---
        if json_data and isinstance(json_data, dict):
            patient_fields = _extract_patient_fields_from_json(json_data)
        else:
            patient_fields = _extract_patient_fields_from_text(text_content)

        # --- Gate 8: Patient identity verification (HARD SAFETY BOUNDARY) ---
        identity_result = self._verifier.verify(patient_fields)

        record.patient_identity_classification = identity_result.reason_code.value
        record.identity_reason_code = identity_result.reason_code.value
        record.identity_matched_fields = identity_result.matched_fields
        record.identity_conflict_fields = identity_result.conflict_fields

        if identity_result.decision == AcquisitionDecision.REJECT:
            record.final_decision = AcquisitionDecision.REJECT
            record.rejection_reason = identity_result.reason_code.value
            summary.rejected_identity += 1
            self._state.mark_acquired(
                message_id=message.message_id,
                attachment_id=stub.attachment_id,
                sha256=sha256,
                final_decision=record.final_decision,
                original_filename=stub.filename,
            )
            log_provenance(record)
            return record

        if identity_result.decision == AcquisitionDecision.REVIEW:
            record.final_decision = AcquisitionDecision.REVIEW
            record.rejection_reason = identity_result.reason_code.value
            summary.sent_to_review += 1
            self._state.mark_acquired(
                message_id=message.message_id,
                attachment_id=stub.attachment_id,
                sha256=sha256,
                final_decision=record.final_decision,
                original_filename=stub.filename,
            )
            log_provenance(record)
            return record

        # Both gates passed — ACCEPT
        # --- Gate 9: Atomic handoff to HC-312 incoming ---
        intake_filename = self._handoff_to_intake(
            content=content,
            original_filename=stub.filename,
        )
        if intake_filename is None:
            # Handoff failed — do NOT mark as acquired; allow retry on next scan
            summary.errors += 1
            logger.error(
                "hc313a_handoff_failed message_id=%s attachment_id=%s",
                message.message_id,
                stub.attachment_id,
            )
            return None

        record.final_decision = AcquisitionDecision.ACCEPT
        record.intake_filename = intake_filename
        summary.accepted += 1
        summary.handoff_success = True
        self._state.mark_acquired(
            message_id=message.message_id,
            attachment_id=stub.attachment_id,
            sha256=sha256,
            final_decision=record.final_decision,
            original_filename=stub.filename,
        )
        log_provenance(record)
        return record

    # ------------------------------------------------------------------
    # Handoff to HC-312
    # ------------------------------------------------------------------

    def _handoff_to_intake(
        self,
        *,
        content: bytes,
        original_filename: str,
    ) -> str | None:
        """Atomically write content to hc_intake/incoming/.

        Uses write-to-temp + atomic rename (same volume) to avoid
        partial writes being claimed by HC-312's intake runner.

        Returns the basename of the file written, or None on failure.
        """
        incoming = self._config.intake_incoming_dir
        try:
            incoming.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error("hc313a_intake_dir_create_failed dir=%s error=%s", incoming, exc)
            return None

        safe_name = _sanitize_filename(original_filename)
        # Ensure no collision
        dest = incoming / safe_name
        if dest.exists():
            stem = Path(safe_name).stem
            suffix = Path(safe_name).suffix
            counter = 1
            while dest.exists():
                dest = incoming / f"{stem}__{counter}{suffix}"
                counter += 1
            safe_name = dest.name

        # Write via temp file on the same filesystem volume for atomic rename
        tmp_path = incoming / f".hc313a_tmp_{os.getpid()}_{safe_name}"
        try:
            tmp_path.write_bytes(content)
            os.replace(tmp_path, incoming / safe_name)
            return safe_name
        except Exception as exc:
            logger.error(
                "hc313a_handoff_write_failed dest=%s error=%s", safe_name, exc
            )
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            return None

    # ------------------------------------------------------------------
    # Text decoding
    # ------------------------------------------------------------------

    @staticmethod
    def _decode_text(content: bytes, mime_type: str, filename: str) -> str:
        """Attempt to decode content bytes to text.

        For JSON and plain-text attachments, decodes as UTF-8.
        For PDF, uses pypdf to extract text if available, then falls back to UTF-8 decoding.
        """
        suffix = Path(filename).suffix.lower()
        if suffix == ".json" or mime_type.startswith(("application/json", "text/")):
            try:
                return content.decode("utf-8", errors="replace")
            except Exception:
                return ""
        
        if suffix == ".pdf" or mime_type == "application/pdf":
            try:
                import io
                import pypdf
                reader = pypdf.PdfReader(io.BytesIO(content))
                text = "\n".join(page.extract_text() for page in reader.pages)
                if text.strip():
                    return text
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning("pypdf extraction failed: %s", exc)
                
        # Best-effort fallback for image/other: decode as UTF-8 and check
        # that the result is mostly readable text (< 20% replacement characters).
        try:
            decoded = content.decode("utf-8", errors="replace")
            replacement_ratio = decoded.count("\ufffd") / max(len(decoded), 1)
            if replacement_ratio > 0.20:
                return ""
            return decoded
        except Exception:
            return ""


# ---------------------------------------------------------------------------
# Mock connector for tests
# ---------------------------------------------------------------------------


class MockGmailConnector:
    """Test double for GmailConnectorProtocol.

    Populated by tests with synthetic messages and attachments.
    Raises GmailConnectorError when ``simulate_outage=True``.
    """

    def __init__(
        self,
        messages: list[GmailMessage] | None = None,
        attachments: dict[str, list[GmailAttachment]] | None = None,
        content_map: dict[str, bytes] | None = None,
        simulate_outage: bool = False,
        simulate_retrieval_failure: set[str] | None = None,
    ) -> None:
        self._messages = messages or []
        # message_id → list[GmailAttachment]
        self._attachments = attachments or {}
        # "<message_id>::<attachment_id>" → bytes
        self._content_map = content_map or {}
        self._simulate_outage = simulate_outage
        self._simulate_retrieval_failure = simulate_retrieval_failure or set()

    def list_messages(self, *, label_filter: str = "") -> list[GmailMessage]:
        if self._simulate_outage:
            raise GmailConnectorError("simulated_gmail_outage")
        return list(self._messages)

    def list_attachments(self, message: GmailMessage) -> list[GmailAttachment]:
        if self._simulate_outage:
            raise GmailConnectorError("simulated_gmail_outage")
        return list(self._attachments.get(message.message_id, []))

    def get_attachment_bytes(self, message_id: str, attachment_id: str) -> bytes:
        if self._simulate_outage:
            raise GmailConnectorError("simulated_gmail_outage")
        key = f"{message_id}::{attachment_id}"
        if attachment_id in self._simulate_retrieval_failure:
            raise GmailAttachmentRetrievalError(f"simulated_retrieval_failure:{attachment_id}")
        content = self._content_map.get(key)
        if content is None:
            raise GmailAttachmentRetrievalError(f"attachment_not_found:{attachment_id}")
        return content


__all__ = [
    "AcquisitionSummary",
    "GmailAcquirer",
    "GmailConnectorProtocol",
    "MockGmailConnector",
]
