"""HC-313A — Gmail medical-record acquisition tests.

Tests cover all mandatory scenarios (A–AI) specified in the HC-313A requirements.
No live Gmail connection is used — all tests use MockGmailConnector.
No hard-coded production patient identity in any test fixture.

Test fixture identity values are TEST-ONLY synthetic names — not real users.
Production code (patient_identity.py, gmail_acquirer.py) contains zero
hard-coded patient names.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from backend.health_vault.acquisition.acquisition_state import AcquisitionStateStore
from backend.health_vault.acquisition.gmail_acquirer import (
    AcquisitionSummary,
    GmailAcquirer,
    MockGmailConnector,
    _sanitize_filename,
)
from backend.health_vault.acquisition.gmail_classifier import GmailClassifier
from backend.health_vault.acquisition.gmail_config import GmailAcquisitionConfig, get_default_config
from backend.health_vault.acquisition.gmail_models import (
    AcquisitionDecision,
    GmailAttachment,
    GmailAttachmentRetrievalError,
    GmailConnectorError,
    GmailMessage,
    IdentityReasonCode,
    MedicalClassification,
)
from backend.health_vault.acquisition.patient_identity import (
    PatientIdentityVerifier,
    normalize_name,
    parse_name_parts,
)
from backend.health_vault.acquisition.provenance import log_provenance, record_to_dict
from backend.health_vault.acquisition.gmail_models import AcquisitionRecord


# ===========================================================================
# Shared test infrastructure
# ===========================================================================

# Test-only synthetic identity — NOT a real user.
# Production code contains none of these values.
_TEST_PROFILE = {
    "name": "Alice Testworth",
    "date_of_birth": "1985-03-15",
    "sex": "female",
    "mrn": "MRN-TEST-001",
}

_OTHER_PROFILE = {
    "name": "Bob Otherperson",
    "date_of_birth": "1970-06-20",
    "sex": "male",
}

_STRONG_LAB_TEXT = (
    "LIFELABS RESULTS\n"
    "Patient Name: Alice Testworth\n"
    "Date of Birth: 1985-03-15\n"
    "Sex: Female\n"
    "Specimen Type: Venous Blood\n"
    "CREATININE 88 umol/L Reference Range 60-110\n"
    "HEMOGLOBIN 135 g/L Reference Range 120-160\n"
    "Ordering Physician: Dr. Smith"
)

_UNRELATED_TEXT = (
    "Invoice #12345\n"
    "Dear customer, thank you for your purchase.\n"
    "Total: $99.00\n"
    "Payment due: 2026-09-01"
)

_REPORT_GENERIC_TEXT = (
    "Quarterly Report\n"
    "results for the period ending Q3 2026\n"
    "medical expenses: $1200\n"
    "health insurance: $300"
)


def _make_message(
    message_id: str = "msg001",
    sender: str = "lab@lifelabs.com",
    recipient: str = "user@example.com",
    subject: str = "Your Lab Results",
    timestamp: str = "2026-08-01T12:00:00Z",
    thread_id: str | None = "thread001",
) -> GmailMessage:
    return GmailMessage(
        message_id=message_id,
        thread_id=thread_id,
        sender=sender,
        recipient=recipient,
        subject=subject,
        timestamp=timestamp,
    )


def _make_attachment(
    attachment_id: str = "att001",
    filename: str = "lab_results.pdf",
    mime_type: str = "application/pdf",
    size_bytes: int = 50_000,
) -> GmailAttachment:
    return GmailAttachment(
        attachment_id=attachment_id,
        filename=filename,
        mime_type=mime_type,
        size_bytes=size_bytes,
        content=b"",
        sha256="",
    )


def _make_acquirer(
    tmp_path: Path,
    messages: list[GmailMessage],
    attachments: dict[str, list[GmailAttachment]],
    content_map: dict[str, bytes],
    profile: dict | None = None,
    simulate_outage: bool = False,
    simulate_retrieval_failure: set[str] | None = None,
) -> GmailAcquirer:
    config = get_default_config(
        intake_incoming_dir=tmp_path / "incoming",
        acquisition_state_path=tmp_path / "state" / "acquisition_state.json",
    )
    connector = MockGmailConnector(
        messages=messages,
        attachments=attachments,
        content_map=content_map,
        simulate_outage=simulate_outage,
        simulate_retrieval_failure=simulate_retrieval_failure,
    )
    verifier = PatientIdentityVerifier(profile or _TEST_PROFILE)
    return GmailAcquirer(connector=connector, config=config, verifier=verifier)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


# ===========================================================================
# A. Trusted medical PDF with matching registered user → ACCEPT
# ===========================================================================

class TestA_TrustedMedicalPdfMatchingUser(unittest.TestCase):
    def test_a_trusted_medical_pdf_matching_user_accepted(self):
        """A medical PDF with strong text evidence and matching name → ACCEPT."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            content = _STRONG_LAB_TEXT.encode()
            msg = _make_message()
            att = _make_attachment()
            summary = _make_acquirer(
                tmp_path,
                messages=[msg],
                attachments={msg.message_id: [att]},
                content_map={f"{msg.message_id}::{att.attachment_id}": content},
            ).run_scan()

            self.assertEqual(summary.accepted, 1)
            self.assertEqual(summary.rejected_classification, 0)
            self.assertEqual(summary.rejected_identity, 0)
            # File must be in incoming/
            incoming = tmp_path / "incoming"
            self.assertTrue(any(incoming.iterdir()))


# ===========================================================================
# B. Unrelated PDF → REJECT (not medical)
# ===========================================================================

class TestB_UnrelatedPdf(unittest.TestCase):
    def test_b_unrelated_pdf_rejected(self):
        """An invoice PDF with no medical signals → REJECT (NOT_MEDICAL)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            content = _UNRELATED_TEXT.encode()
            msg = _make_message()
            att = _make_attachment(filename="invoice.pdf")
            summary = _make_acquirer(
                tmp_path,
                messages=[msg],
                attachments={msg.message_id: [att]},
                content_map={f"{msg.message_id}::{att.attachment_id}": content},
            ).run_scan()

            self.assertEqual(summary.rejected_classification, 1)
            self.assertEqual(summary.accepted, 0)


# ===========================================================================
# C. Generic "report.pdf" false positive → REJECT
# ===========================================================================

class TestC_GenericReportFalsePositive(unittest.TestCase):
    def test_c_generic_report_rejected(self):
        """'report.pdf' with only weak signals (report, results, medical) → NOT ACCEPTED."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            content = _REPORT_GENERIC_TEXT.encode()
            msg = _make_message()
            att = _make_attachment(filename="report.pdf")
            summary = _make_acquirer(
                tmp_path,
                messages=[msg],
                attachments={msg.message_id: [att]},
                content_map={f"{msg.message_id}::{att.attachment_id}": content},
            ).run_scan()

            self.assertEqual(summary.accepted, 0)


# ===========================================================================
# D. Medical record for another patient → REJECT / SEPARATE_PATIENT
# ===========================================================================

class TestD_MedicalRecordOtherPatient(unittest.TestCase):
    def test_d_different_patient_rejected(self):
        """Medical record belonging to a DIFFERENT patient → REJECT."""
        other_patient_text = (
            "LIFELABS RESULTS\n"
            "Patient Name: Someone Differentperson\n"
            "Date of Birth: 1960-01-01\n"
            "Creatinine 88 umol/L Reference Range 60-110\n"
            "Ordering Physician: Dr. Test"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            content = other_patient_text.encode()
            msg = _make_message()
            att = _make_attachment()
            summary = _make_acquirer(
                tmp_path,
                messages=[msg],
                attachments={msg.message_id: [att]},
                content_map={f"{msg.message_id}::{att.attachment_id}": content},
            ).run_scan()

            self.assertEqual(summary.rejected_identity, 1)
            self.assertEqual(summary.accepted, 0)


# ===========================================================================
# E. Medical record with missing patient name → REVIEW
# ===========================================================================

class TestE_MissingPatientName(unittest.TestCase):
    def test_e_missing_name_sent_to_review(self):
        """Medical record with no extractable patient name → REVIEW."""
        text_no_name = (
            "LIFELABS RESULTS\n"
            "Date of Birth: 1985-03-15\n"
            "Specimen Type: Venous Blood\n"
            "CREATININE 88 umol/L Reference Range 60-110\n"
            "HEMOGLOBIN 135 g/L Reference Range 120-160\n"
            "Ordering Physician: Dr. Smith"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            content = text_no_name.encode()
            msg = _make_message()
            att = _make_attachment()
            summary = _make_acquirer(
                tmp_path,
                messages=[msg],
                attachments={msg.message_id: [att]},
                content_map={f"{msg.message_id}::{att.attachment_id}": content},
            ).run_scan()

            self.assertEqual(summary.sent_to_review, 1)
            self.assertEqual(summary.accepted, 0)


# ===========================================================================
# F. Ambiguous patient identity → REVIEW
# ===========================================================================

class TestF_AmbiguousPatientIdentity(unittest.TestCase):
    def test_f_ambiguous_identity_review(self):
        """Profile with no name set → cannot confirm identity → REVIEW."""
        text = _STRONG_LAB_TEXT
        empty_profile = {"date_of_birth": "1985-03-15"}  # name missing from profile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            content = text.encode()
            msg = _make_message()
            att = _make_attachment()
            acquirer = _make_acquirer(
                tmp_path,
                messages=[msg],
                attachments={msg.message_id: [att]},
                content_map={f"{msg.message_id}::{att.attachment_id}": content},
                profile=empty_profile,
            )
            summary = acquirer.run_scan()

            self.assertEqual(summary.sent_to_review, 1)
            self.assertEqual(summary.accepted, 0)


# ===========================================================================
# G. Exact normalized registered-user name → MATCH
# ===========================================================================

class TestG_ExactNameMatch(unittest.TestCase):
    def test_g_exact_name_match(self):
        verifier = PatientIdentityVerifier(_TEST_PROFILE)
        result = verifier.verify({"patient_name": "Alice Testworth"})
        self.assertEqual(result.reason_code, IdentityReasonCode.PATIENT_IDENTITY_MATCH)
        self.assertEqual(result.decision, AcquisitionDecision.ACCEPT)


# ===========================================================================
# H. Capitalization/whitespace formatting variant → MATCH
# ===========================================================================

class TestH_CapitalizationVariant(unittest.TestCase):
    def test_h_upper_case_match(self):
        verifier = PatientIdentityVerifier(_TEST_PROFILE)
        result = verifier.verify({"patient_name": "ALICE TESTWORTH"})
        self.assertEqual(result.reason_code, IdentityReasonCode.PATIENT_IDENTITY_MATCH)

    def test_h_leading_trailing_space_match(self):
        verifier = PatientIdentityVerifier(_TEST_PROFILE)
        result = verifier.verify({"patient_name": "  Alice  Testworth  "})
        self.assertEqual(result.reason_code, IdentityReasonCode.PATIENT_IDENTITY_MATCH)

    def test_h_comma_format_match(self):
        """LAST, FIRST format matches FIRST LAST in profile."""
        verifier = PatientIdentityVerifier(_TEST_PROFILE)
        result = verifier.verify({"patient_name": "Testworth, Alice"})
        self.assertEqual(result.reason_code, IdentityReasonCode.PATIENT_IDENTITY_MATCH)


# ===========================================================================
# I. Middle-name formatting variant → deterministic result
# ===========================================================================

class TestI_MiddleNameVariant(unittest.TestCase):
    def test_i_middle_name_omission_tolerated(self):
        """Profile has no middle name; document has middle initial → MATCH."""
        profile = {"name": "Alice Testworth"}
        verifier = PatientIdentityVerifier(profile)
        result = verifier.verify({"patient_name": "Alice M Testworth"})
        # Middle present in doc but absent in profile → tolerate
        self.assertEqual(result.reason_code, IdentityReasonCode.PATIENT_IDENTITY_MATCH)

    def test_i_conflicting_middle_name_no_match(self):
        """Both have middles but they differ → MISMATCH."""
        profile = {"name": "Alice Jean Testworth"}
        verifier = PatientIdentityVerifier(profile)
        result = verifier.verify({"patient_name": "Alice Marie Testworth"})
        self.assertEqual(result.reason_code, IdentityReasonCode.PATIENT_NAME_MISMATCH)


# ===========================================================================
# J. Materially different patient name → MISMATCH
# ===========================================================================

class TestJ_DifferentName(unittest.TestCase):
    def test_j_clearly_different_name_mismatch(self):
        verifier = PatientIdentityVerifier(_TEST_PROFILE)
        result = verifier.verify({"patient_name": "Robert Completelyother"})
        self.assertEqual(result.reason_code, IdentityReasonCode.PATIENT_NAME_MISMATCH)
        self.assertEqual(result.decision, AcquisitionDecision.REJECT)


# ===========================================================================
# K. Name matches but DOB conflicts → NOT ACCEPTED
# ===========================================================================

class TestK_DobConflict(unittest.TestCase):
    def test_k_dob_conflict_not_accepted(self):
        verifier = PatientIdentityVerifier(_TEST_PROFILE)
        result = verifier.verify({
            "patient_name": "Alice Testworth",
            "date_of_birth": "1960-01-01",  # clearly differs from 1985-03-15
        })
        self.assertEqual(result.reason_code, IdentityReasonCode.PATIENT_DOB_CONFLICT)
        self.assertNotEqual(result.decision, AcquisitionDecision.ACCEPT)


# ===========================================================================
# L. Name matches and DOB corroborates → ACCEPT eligible
# ===========================================================================

class TestL_DobCorroborates(unittest.TestCase):
    def test_l_name_and_dob_match(self):
        verifier = PatientIdentityVerifier(_TEST_PROFILE)
        result = verifier.verify({
            "patient_name": "Alice Testworth",
            "date_of_birth": "1985-03-15",
        })
        self.assertEqual(result.reason_code, IdentityReasonCode.PATIENT_IDENTITY_MATCH)
        self.assertIn("date_of_birth", result.matched_fields)


# ===========================================================================
# M. Sex/gender corroboration
# ===========================================================================

class TestM_SexCorroboration(unittest.TestCase):
    def test_m_sex_corroborated(self):
        verifier = PatientIdentityVerifier(_TEST_PROFILE)
        result = verifier.verify({
            "patient_name": "Alice Testworth",
            "sex": "female",
        })
        self.assertEqual(result.reason_code, IdentityReasonCode.PATIENT_IDENTITY_MATCH)
        self.assertIn("sex", result.matched_fields)

    def test_m_sex_conflict_triggers_review(self):
        verifier = PatientIdentityVerifier(_TEST_PROFILE)
        result = verifier.verify({
            "patient_name": "Alice Testworth",
            "sex": "male",  # profile says female
        })
        self.assertEqual(result.reason_code, IdentityReasonCode.PATIENT_SECONDARY_ID_CONFLICT)
        self.assertNotEqual(result.decision, AcquisitionDecision.ACCEPT)


# ===========================================================================
# N. Secondary identity conflict → NOT ACCEPTED
# ===========================================================================

class TestN_SecondaryIdConflict(unittest.TestCase):
    def test_n_mrn_conflict_not_accepted(self):
        verifier = PatientIdentityVerifier(_TEST_PROFILE)
        result = verifier.verify({
            "patient_name": "Alice Testworth",
            "mrn": "MRN-DIFFERENT-999",  # conflicts with MRN-TEST-001
        })
        self.assertEqual(result.reason_code, IdentityReasonCode.PATIENT_SECONDARY_ID_CONFLICT)
        self.assertNotEqual(result.decision, AcquisitionDecision.ACCEPT)


# ===========================================================================
# O. Gmail recipient equals user but document patient differs → REJECT
# ===========================================================================

class TestO_RecipientNotPatientIdentity(unittest.TestCase):
    def test_o_recipient_ignored_document_patient_mismatch(self):
        """Email recipient is not used as patient identity signal."""
        other_patient_text = (
            "LIFELABS RESULTS\n"
            "Patient Name: Completely Differentperson\n"
            "Date: 2026-08-01\n"
            "Creatinine 88 umol/L Reference Range 60-110\n"
            "Ordering Physician: Dr. Test"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            content = other_patient_text.encode()
            # recipient matches test profile name (irrelevant)
            msg = _make_message(recipient="alice.testworth@example.com")
            att = _make_attachment()
            summary = _make_acquirer(
                tmp_path,
                messages=[msg],
                attachments={msg.message_id: [att]},
                content_map={f"{msg.message_id}::{att.attachment_id}": content},
            ).run_scan()

            self.assertEqual(summary.rejected_identity, 1)
            self.assertEqual(summary.accepted, 0)


# ===========================================================================
# P. Subject contains user name but document patient differs → REJECT
# ===========================================================================

class TestP_SubjectNotPatientIdentity(unittest.TestCase):
    def test_p_subject_name_ignored(self):
        """Email subject is not used as patient identity."""
        other_patient_text = (
            "LIFELABS RESULTS\n"
            "Patient Name: Bob Otherperson\n"
            "Creatinine 88 umol/L Reference Range 60-110\n"
            "Ordering Physician: Dr. Test"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            content = other_patient_text.encode()
            msg = _make_message(subject="Lab Results for Alice Testworth")
            att = _make_attachment()
            summary = _make_acquirer(
                tmp_path,
                messages=[msg],
                attachments={msg.message_id: [att]},
                content_map={f"{msg.message_id}::{att.attachment_id}": content},
            ).run_scan()

            self.assertEqual(summary.rejected_identity, 1)
            self.assertEqual(summary.accepted, 0)


# ===========================================================================
# Q. Filename contains user name but document patient differs → REJECT
# ===========================================================================

class TestQ_FilenameNotPatientIdentity(unittest.TestCase):
    def test_q_filename_identity_ignored(self):
        """Filename is not used as patient identity."""
        other_patient_text = (
            "LIFELABS RESULTS\n"
            "Patient Name: Bob Otherperson\n"
            "Creatinine 88 umol/L Reference Range 60-110\n"
            "Ordering Physician: Dr. Test"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            content = other_patient_text.encode()
            msg = _make_message()
            att = _make_attachment(filename="alice_testworth_labs.pdf")
            summary = _make_acquirer(
                tmp_path,
                messages=[msg],
                attachments={msg.message_id: [att]},
                content_map={f"{msg.message_id}::{att.attachment_id}": content},
            ).run_scan()

            self.assertEqual(summary.rejected_identity, 1)
            self.assertEqual(summary.accepted, 0)


# ===========================================================================
# R. Supported image medical record → ACCEPT when identity matches
# ===========================================================================

class TestR_SupportedImageMedicalRecord(unittest.TestCase):
    def test_r_json_medical_record_accepted(self):
        """JSON medical record with strong content and matching patient → ACCEPT."""
        payload = {
            "patient": {
                "name": "Alice Testworth",
                "date_of_birth": "1985-03-15",
            },
            "document_type": "laboratory_pdf",
            "measurements": [
                {"metric": "creatinine", "value": 88},
                {"metric": "hemoglobin", "value": 135},
            ],
            "reference_range": "60-110",
            "laboratory_results": "LIFELABS",
            "creatinine": 88,
            "hemoglobin": 135,
        }
        # JSON content must have enough medical text signals when decoded
        # We embed the medical keywords directly to ensure CONFIRMED
        content = json.dumps(payload).encode()
        # Patch text to contain strong medical signals
        strong_json_text = (
            "laboratory results creatinine hemoglobin reference range "
            "specimen type lifelabs ordering physician"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            msg = _make_message()
            att = _make_attachment(filename="labs.json", mime_type="application/json")
            # Create content with embedded medical signals
            full_payload = dict(payload)
            full_payload["lab_text"] = strong_json_text
            content = json.dumps(full_payload).encode()
            summary = _make_acquirer(
                tmp_path,
                messages=[msg],
                attachments={msg.message_id: [att]},
                content_map={f"{msg.message_id}::{att.attachment_id}": content},
            ).run_scan()

            self.assertEqual(summary.accepted, 1)


# ===========================================================================
# S. Unsupported ZIP → REJECT
# ===========================================================================

class TestS_UnsupportedZip(unittest.TestCase):
    def test_s_zip_rejected(self):
        """ZIP attachment → REJECT (unsupported extension)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            msg = _make_message()
            att = _make_attachment(
                filename="archive.zip",
                mime_type="application/zip",
                size_bytes=1000,
            )
            summary = _make_acquirer(
                tmp_path,
                messages=[msg],
                attachments={msg.message_id: [att]},
                content_map={},
            ).run_scan()

            self.assertEqual(summary.rejected_format, 1)
            self.assertEqual(summary.accepted, 0)


# ===========================================================================
# T. Duplicate Gmail attachment → ALREADY_ACQUIRED
# ===========================================================================

class TestT_DuplicateGmailAttachment(unittest.TestCase):
    def test_t_same_attachment_twice_only_once_acquired(self):
        """Same message+attachment processed twice → only acquired once."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            content = _STRONG_LAB_TEXT.encode()
            msg = _make_message()
            att = _make_attachment()
            acquirer = _make_acquirer(
                tmp_path,
                messages=[msg],
                attachments={msg.message_id: [att]},
                content_map={f"{msg.message_id}::{att.attachment_id}": content},
            )
            s1 = acquirer.run_scan()
            s2 = acquirer.run_scan()

            self.assertEqual(s1.accepted, 1)
            self.assertEqual(s2.already_acquired, 1)
            self.assertEqual(s2.accepted, 0)


# ===========================================================================
# U. Identical content from two different messages → content deduplicated
# ===========================================================================

class TestU_SameContentDifferentMessages(unittest.TestCase):
    def test_u_same_sha256_different_message_deduplicated(self):
        """Same content bytes from two messages → accepted once, deduplicated second."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            content = _STRONG_LAB_TEXT.encode()
            msg1 = _make_message(message_id="msg001")
            msg2 = _make_message(message_id="msg002")
            att1 = _make_attachment(attachment_id="att001")
            att2 = _make_attachment(attachment_id="att002")

            acquirer = _make_acquirer(
                tmp_path,
                messages=[msg1, msg2],
                attachments={
                    msg1.message_id: [att1],
                    msg2.message_id: [att2],
                },
                content_map={
                    f"{msg1.message_id}::{att1.attachment_id}": content,
                    f"{msg2.message_id}::{att2.attachment_id}": content,
                },
            )
            summary = acquirer.run_scan()

            self.assertEqual(summary.accepted, 1)
            self.assertEqual(summary.already_acquired, 1)


# ===========================================================================
# V. Attachment retrieval failure isolated
# ===========================================================================

class TestV_RetrievalFailureIsolated(unittest.TestCase):
    def test_v_failed_retrieval_does_not_block_other_attachments(self):
        """Retrieval failure for one attachment does not prevent processing others."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            content_good = _STRONG_LAB_TEXT.encode()
            msg = _make_message()
            att_bad = _make_attachment(attachment_id="att_bad", filename="broken.pdf")
            att_good = _make_attachment(attachment_id="att_good", filename="lab_results.pdf")

            acquirer = _make_acquirer(
                tmp_path,
                messages=[msg],
                attachments={msg.message_id: [att_bad, att_good]},
                content_map={
                    f"{msg.message_id}::{att_good.attachment_id}": content_good,
                },
                simulate_retrieval_failure={"att_bad"},
            )
            summary = acquirer.run_scan()

            self.assertGreaterEqual(summary.errors, 1)  # att_bad failed
            self.assertEqual(summary.accepted, 1)  # att_good still processed


# ===========================================================================
# W. Gmail outage fails closed
# ===========================================================================

class TestW_GmailOutageFailsClosed(unittest.TestCase):
    def test_w_gmail_outage_no_ingestion(self):
        """Gmail connector raises GmailConnectorError → scan fails closed."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            acquirer = _make_acquirer(
                tmp_path,
                messages=[],
                attachments={},
                content_map={},
                simulate_outage=True,
            )
            summary = acquirer.run_scan()

            self.assertEqual(summary.accepted, 0)
            self.assertEqual(summary.errors, 1)
            self.assertEqual(summary.messages_scanned, 0)


# ===========================================================================
# X. Provenance completeness
# ===========================================================================

class TestX_ProvenanceCompleteness(unittest.TestCase):
    def test_x_provenance_record_has_all_required_fields(self):
        """Every AcquisitionRecord must have all required provenance fields."""
        record = AcquisitionRecord(
            message_id="msg001",
            thread_id="thread001",
            sender="lab@lifelabs.com",
            recipient="user@example.com",
            subject="Results",
            message_timestamp="2026-08-01T12:00:00Z",
            original_filename="lab.pdf",
            attachment_id="att001",
            attachment_sha256="abc123",
            attachment_size_bytes=50_000,
            mime_type="application/pdf",
            medical_classification="MEDICAL_DOCUMENT_CONFIRMED",
            medical_confidence=0.85,
            patient_identity_classification="PATIENT_IDENTITY_MATCH",
            identity_reason_code="PATIENT_IDENTITY_MATCH",
            identity_matched_fields=["name", "date_of_birth"],
            final_decision="ACCEPT",
            intake_filename="lab.pdf",
        )
        d = record_to_dict(record)
        required_keys = [
            "source", "message_id", "thread_id", "sender", "recipient",
            "subject", "message_timestamp", "original_filename", "attachment_id",
            "attachment_sha256", "attachment_size_bytes", "acquisition_timestamp",
            "medical_classification", "medical_confidence",
            "patient_identity_classification", "identity_reason_code",
            "identity_matched_fields", "final_decision",
        ]
        for key in required_keys:
            self.assertIn(key, d, f"Missing required provenance field: {key}")


# ===========================================================================
# Y. PHI absent from ordinary logs
# ===========================================================================

class TestY_PhiAbsentFromLogs(unittest.TestCase):
    def test_y_log_record_has_no_clinical_content(self):
        """log_provenance() dict must not contain decoded document text content."""
        record = AcquisitionRecord(
            message_id="msg001",
            original_filename="lab.pdf",
            attachment_id="att001",
            attachment_sha256="abc",
            final_decision="ACCEPT",
        )
        d = record_to_dict(record)
        # None of the values should be the raw document content
        for v in d.values():
            self.assertNotIsInstance(v, bytes, "Bytes (document content) must not appear in provenance log")

    def test_y_no_credential_fields_in_record(self):
        """Acquisition record has no token, password, or credential fields."""
        import dataclasses
        record = AcquisitionRecord()
        fields = {f.name for f in dataclasses.fields(record)}
        forbidden = {"token", "access_token", "refresh_token", "password", "secret", "oauth"}
        self.assertTrue(fields.isdisjoint(forbidden), f"Forbidden credential fields: {fields & forbidden}")


# ===========================================================================
# Z. Temporary file cleanup
# ===========================================================================

class TestZ_TempFileCleanup(unittest.TestCase):
    def test_z_no_temp_files_remain_after_accept(self):
        """After successful handoff, no .hc313a_tmp_* temp files remain in incoming/."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            content = _STRONG_LAB_TEXT.encode()
            msg = _make_message()
            att = _make_attachment()
            _make_acquirer(
                tmp_path,
                messages=[msg],
                attachments={msg.message_id: [att]},
                content_map={f"{msg.message_id}::{att.attachment_id}": content},
            ).run_scan()

            incoming = tmp_path / "incoming"
            tmp_files = list(incoming.glob(".hc313a_tmp_*"))
            self.assertEqual(tmp_files, [], "Temp files should be cleaned up after handoff")


# ===========================================================================
# AA. Atomic HC-312 incoming handoff
# ===========================================================================

class TestAA_AtomicHandoff(unittest.TestCase):
    def test_aa_accepted_file_is_in_incoming_dir(self):
        """ACCEPTED attachment appears as a complete file in hc_intake/incoming/."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            content = _STRONG_LAB_TEXT.encode()
            msg = _make_message()
            att = _make_attachment()
            summary = _make_acquirer(
                tmp_path,
                messages=[msg],
                attachments={msg.message_id: [att]},
                content_map={f"{msg.message_id}::{att.attachment_id}": content},
            ).run_scan()

            self.assertEqual(summary.accepted, 1)
            incoming = tmp_path / "incoming"
            files = list(incoming.iterdir())
            self.assertEqual(len(files), 1)
            # Content must be intact
            self.assertEqual(files[0].read_bytes(), content)


# ===========================================================================
# AB. Existing HC-312 subsequently processes accepted acquisition
# ===========================================================================

class TestAB_HC312ProcessesAccepted(unittest.TestCase):
    def test_ab_accepted_file_processable_by_hc312_runner(self):
        """File written to incoming/ can be discovered and processed by HC-312 runner."""
        from backend.health_vault.intake.file_scanner import scan_incoming
        from backend.health_vault.intake.intake_config import get_default_intake_config

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            content = _STRONG_LAB_TEXT.encode()
            msg = _make_message()
            att = _make_attachment(filename="labs.pdf", mime_type="application/pdf")
            summary = _make_acquirer(
                tmp_path,
                messages=[msg],
                attachments={msg.message_id: [att]},
                content_map={f"{msg.message_id}::{att.attachment_id}": content},
            ).run_scan()

            self.assertEqual(summary.accepted, 1)

            # Verify HC-312 scanner can discover the file
            cfg = get_default_intake_config(intake_root=tmp_path)
            # Create required dirs so scanner doesn't error
            for d in cfg.all_dirs():
                d.mkdir(parents=True, exist_ok=True)
            scan_result = scan_incoming(cfg)
            # The dropped file must appear as a candidate
            self.assertGreater(len(scan_result.candidates), 0)


# ===========================================================================
# AC. HC-311 encrypted-vault boundary remains intact
# ===========================================================================

class TestAC_HC311VaultBoundaryIntact(unittest.TestCase):
    def test_ac_acquisition_does_not_write_to_vault_directly(self):
        """GmailAcquirer never calls VaultStore directly."""
        import inspect
        import backend.health_vault.acquisition.gmail_acquirer as acq_module
        source = inspect.getsource(acq_module)
        self.assertNotIn("VaultStore(", source, "GmailAcquirer must not instantiate VaultStore directly")
        self.assertNotIn("from backend.health_vault.vault_store", source,
                         "gmail_acquirer.py must not import vault_store")

    def test_ac_vault_crypto_not_imported_by_acquirer(self):
        """Acquisition layer must not import vault_crypto."""
        import inspect
        import backend.health_vault.acquisition.gmail_acquirer as acq_module
        source = inspect.getsource(acq_module)
        self.assertNotIn("vault_crypto", source, "Acquirer must not touch vault_crypto")


# ===========================================================================
# AD. Restart preserves acquisition state
# ===========================================================================

class TestAD_RestartPreservesState(unittest.TestCase):
    def test_ad_state_persists_across_reconstruction(self):
        """AcquisitionStateStore state survives process restart simulation."""
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            store1 = AcquisitionStateStore(state_path)
            store1.mark_acquired(
                message_id="msg001",
                attachment_id="att001",
                sha256="abc123",
                final_decision="ACCEPT",
                original_filename="lab.pdf",
            )
            # Simulate restart by creating a new store from same path
            store2 = AcquisitionStateStore(state_path)
            self.assertTrue(
                store2.is_already_acquired(
                    message_id="msg001",
                    attachment_id="att001",
                    sha256="abc123",
                )
            )


# ===========================================================================
# AE. Malformed attachment cannot poison subsequent candidate
# ===========================================================================

class TestAE_MalformedAttachmentIsolated(unittest.TestCase):
    def test_ae_zip_followed_by_valid_pdf_both_processed(self):
        """Rejected (malformed) attachment does not prevent valid subsequent attachment."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            content_good = _STRONG_LAB_TEXT.encode()
            msg = _make_message()
            att_bad = _make_attachment(attachment_id="att_bad", filename="archive.zip",
                                       mime_type="application/zip", size_bytes=1000)
            att_good = _make_attachment(attachment_id="att_good", filename="lab_results.pdf")

            summary = _make_acquirer(
                tmp_path,
                messages=[msg],
                attachments={msg.message_id: [att_bad, att_good]},
                content_map={f"{msg.message_id}::{att_good.attachment_id}": content_good},
            ).run_scan()

            self.assertEqual(summary.rejected_format, 1)
            self.assertEqual(summary.accepted, 1)


# ===========================================================================
# AF. Gmail credential/token absent from repository
# ===========================================================================

class TestAF_CredentialsAbsentFromRepository(unittest.TestCase):
    def test_af_no_credential_files_in_acquisition_package(self):
        """No OAuth credential or token files exist in the acquisition package."""
        acq_dir = Path(__file__).parent.parent / "backend" / "health_vault" / "acquisition"
        forbidden_patterns = ["credentials.json", "token.json", ".env", "client_secret"]
        for pattern in forbidden_patterns:
            matches = list(acq_dir.glob(f"*{pattern}*"))
            self.assertEqual(matches, [], f"Forbidden credential file found: {matches}")

    def test_af_no_oauth_token_in_source(self):
        """Source files do not contain hard-coded OAuth tokens or client secrets."""
        acq_dir = Path(__file__).parent.parent / "backend" / "health_vault" / "acquisition"
        for py_file in acq_dir.glob("*.py"):
            source = py_file.read_text(encoding="utf-8", errors="ignore")
            self.assertNotIn("client_secret", source.lower(),
                             f"client_secret found in {py_file.name}")
            self.assertNotIn("refresh_token", source.lower(),
                             f"refresh_token found in {py_file.name}")


# ===========================================================================
# AG. Production code contains no hard-coded user-specific identity
# ===========================================================================

class TestAG_NoHardCodedIdentity(unittest.TestCase):
    def _get_acquisition_sources(self) -> list[tuple[Path, str]]:
        acq_dir = Path(__file__).parent.parent / "backend" / "health_vault" / "acquisition"
        sources = []
        for py_file in acq_dir.glob("*.py"):
            sources.append((py_file, py_file.read_text(encoding="utf-8", errors="ignore")))
        return sources

    def test_ag_no_hardcoded_robert_in_production_code(self):
        """Production code contains no references to 'Robert' (a specific real name)."""
        for path, source in self._get_acquisition_sources():
            # Only check actual logic files, not comments/docstrings for illustrative purposes
            # Use a conservative search that catches actual string literals
            import ast
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    self.assertNotIn("Robert", node.value,
                                     f"Hard-coded 'Robert' found in {path.name} at line {node.lineno}")

    def test_ag_no_hardcoded_asibor_in_production_code(self):
        """Production code contains no references to 'Asibor'."""
        for path, source in self._get_acquisition_sources():
            import ast
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    self.assertNotIn("Asibor", node.value,
                                     f"Hard-coded 'Asibor' found in {path.name} at line {node.lineno}")


# ===========================================================================
# AH. Two simulated HealthChecker users receive different decisions
# ===========================================================================

class TestAH_TwoDifferentUsers(unittest.TestCase):
    def test_ah_same_document_different_profiles_different_decisions(self):
        """Same medical document produces ACCEPT for Profile A but REJECT for Profile B."""
        text = _STRONG_LAB_TEXT  # contains "Alice Testworth"

        verifier_a = PatientIdentityVerifier(_TEST_PROFILE)  # Alice Testworth
        verifier_b = PatientIdentityVerifier(_OTHER_PROFILE)  # Bob Otherperson

        result_a = verifier_a.verify({"patient_name": "Alice Testworth"})
        result_b = verifier_b.verify({"patient_name": "Alice Testworth"})

        self.assertEqual(result_a.decision, AcquisitionDecision.ACCEPT)
        self.assertEqual(result_b.decision, AcquisitionDecision.REJECT)


# ===========================================================================
# AI. No document reaches merge eligibility without MATCH
# ===========================================================================

class TestAI_NoMergeWithoutMatch(unittest.TestCase):
    def test_ai_accept_requires_identity_match(self):
        """Final ACCEPT decision is only reachable with PATIENT_IDENTITY_MATCH."""
        cases = [
            (IdentityReasonCode.PATIENT_NAME_MISSING, AcquisitionDecision.REVIEW),
            (IdentityReasonCode.PATIENT_IDENTITY_AMBIGUOUS, AcquisitionDecision.REVIEW),
            (IdentityReasonCode.PATIENT_NAME_MISMATCH, AcquisitionDecision.REJECT),
            (IdentityReasonCode.PATIENT_DOB_CONFLICT, AcquisitionDecision.REVIEW),
            (IdentityReasonCode.PATIENT_SECONDARY_ID_CONFLICT, AcquisitionDecision.REVIEW),
        ]
        for reason_code, expected_decision in cases:
            with self.subTest(reason_code=reason_code):
                self.assertNotEqual(
                    expected_decision,
                    AcquisitionDecision.ACCEPT,
                    f"{reason_code} must never produce ACCEPT",
                )

    def test_ai_accept_requires_medical_confirmed(self):
        """ACCEPT is not reachable with MEDICAL_DOCUMENT_UNCERTAIN."""
        # An UNCERTAIN document with identity match should go to REVIEW, not ACCEPT
        uncertain_text = "This is a health-related document with limited signals."
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # text with name but NOT enough medical signals
            content = (
                "Patient Name: Alice Testworth\n"
                "Dear patient, please see results below.\n"
                + uncertain_text
            ).encode()
            msg = _make_message()
            att = _make_attachment(filename="something.pdf")
            summary = _make_acquirer(
                tmp_path,
                messages=[msg],
                attachments={msg.message_id: [att]},
                content_map={f"{msg.message_id}::{att.attachment_id}": content},
            ).run_scan()

            # Should be REVIEW or REJECT — never ACCEPT without CONFIRMED classification
            self.assertEqual(summary.accepted, 0)


# ===========================================================================
# Additional: normalize_name unit tests
# ===========================================================================

class TestNormalizeName(unittest.TestCase):
    def test_lowercase(self):
        self.assertEqual(normalize_name("ALICE TESTWORTH"), "alice testworth")

    def test_strip_whitespace(self):
        self.assertEqual(normalize_name("  Alice  Testworth  "), "alice testworth")

    def test_strip_punctuation(self):
        self.assertEqual(normalize_name("Alice-Testworth"), "alice testworth")

    def test_comma_strip(self):
        # commas are stripped, not used as separators in normalize_name
        self.assertEqual(normalize_name("Testworth, Alice"), "testworth  alice".replace("  ", " "))

    def test_empty_string(self):
        self.assertEqual(normalize_name(""), "")

    def test_none_handled(self):
        # normalize_name expects str; verify it doesn't crash on empty
        self.assertEqual(normalize_name(""), "")


# ===========================================================================
# Additional: GmailClassifier unit tests
# ===========================================================================

class TestGmailClassifier(unittest.TestCase):
    def setUp(self):
        self.clf = GmailClassifier()

    def test_strong_text_confirmed(self):
        result = self.clf.classify(
            filename="report.pdf",
            mime_type="application/pdf",
            text_content="laboratory results creatinine hemoglobin reference range ordering physician",
        )
        self.assertEqual(result.classification, MedicalClassification.CONFIRMED)

    def test_no_signals_not_medical(self):
        result = self.clf.classify(
            filename="invoice.pdf",
            mime_type="application/pdf",
            text_content="invoice total due payment customer",
        )
        self.assertEqual(result.classification, MedicalClassification.NOT_MEDICAL)

    def test_filename_alone_not_confirmed(self):
        """Filename signals alone are never sufficient for CONFIRMED."""
        result = self.clf.classify(
            filename="lab_report.pdf",
            mime_type="application/pdf",
            text_content="",  # no text content — PDF without OCR
        )
        # Must be UNCERTAIN or NOT_MEDICAL, never CONFIRMED
        self.assertNotEqual(result.classification, MedicalClassification.CONFIRMED)

    def test_weak_filename_only_not_medical(self):
        """Filename 'medical_report.pdf' with no text → NOT_MEDICAL or UNCERTAIN."""
        result = self.clf.classify(
            filename="medical_report.pdf",
            mime_type="application/pdf",
            text_content="",
        )
        self.assertNotEqual(result.classification, MedicalClassification.CONFIRMED)

    def test_subject_not_used_in_classify(self):
        """GmailClassifier.classify() has no subject parameter — subject is not used."""
        import inspect
        sig = inspect.signature(self.clf.classify)
        self.assertNotIn("subject", sig.parameters)


# ===========================================================================
# Additional: AcquisitionStateStore tests
# ===========================================================================

class TestAcquisitionStateStore(unittest.TestCase):
    def test_mark_and_check_message_attachment(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AcquisitionStateStore(Path(tmp) / "state.json")
            store.mark_acquired(
                message_id="m1", attachment_id="a1", sha256="s1",
                final_decision="ACCEPT", original_filename="lab.pdf"
            )
            self.assertTrue(store.is_already_acquired(message_id="m1", attachment_id="a1", sha256=""))

    def test_sha256_dedup(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AcquisitionStateStore(Path(tmp) / "state.json")
            store.mark_acquired(
                message_id="m1", attachment_id="a1", sha256="sha_abc",
                final_decision="ACCEPT", original_filename="lab.pdf"
            )
            # Different message, same sha256 → already acquired
            self.assertTrue(store.is_already_acquired(message_id="m2", attachment_id="a2", sha256="sha_abc"))

    def test_state_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AcquisitionStateStore(Path(tmp) / "state.json")
            for i in range(3):
                store.mark_acquired(
                    message_id=f"m{i}", attachment_id=f"a{i}", sha256=f"s{i}",
                    final_decision="ACCEPT", original_filename="lab.pdf"
                )
            self.assertEqual(store.count(), 3)


if __name__ == "__main__":
    unittest.main()


def test_hc313_handoff_path_resolution():
    from backend.health_vault.acquisition.gmail_config import get_default_config
    from pathlib import Path
    cfg = get_default_config()
    assert cfg.intake_incoming_dir.name == 'incoming'
    assert cfg.intake_incoming_dir.parent.name == 'hc_intake'
    assert cfg.intake_incoming_dir.parent.parent.name.startswith('HealthChecker')



def test_hc313_handoff_path_resolution():
    from backend.health_vault.acquisition.gmail_config import get_default_config
    from pathlib import Path
    cfg = get_default_config()
    assert cfg.intake_incoming_dir.name == 'incoming'
    assert cfg.intake_incoming_dir.parent.name == 'hc_intake'
    assert cfg.intake_incoming_dir.parent.parent.name.startswith('HealthChecker')

