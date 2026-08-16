"""HC-313A — Patient identity normalization and verification.

HARD SAFETY BOUNDARY
--------------------
A medical document MUST NOT be automatically merged into a user's
HealthChecker medical record unless the patient identity contained in the
medical document matches the registered HealthChecker user's identity.

Design rules:
- Production code contains NO hard-coded patient names.
- Identity is read from the registered HealthChecker user profile via
  ``VaultStore.get_profile()`` — a dict that may contain:
      name            (str)
      date_of_birth   (str — ISO-8601 date or YYYY-MM-DD)
      sex             (str — "male"/"female"/"other"/...)
      mrn             (str — medical record number, optional)
- Normalization is deterministic: lower, strip, collapse spaces, strip
  select punctuation. NO approximate/fuzzy matching.
- "LAST, FIRST MIDDLE" and "FIRST LAST" formats are handled.
- Middle-name presence/absence is tolerated only if the remaining name
  parts match exactly after normalization.
- Supporting identifiers (DOB, sex, MRN) MUST NOT override a clear name
  mismatch — they can only upgrade REVIEW → ACCEPT or downgrade ACCEPT →
  REVIEW/REJECT.

Reason codes returned:
    PATIENT_IDENTITY_MATCH          — name matches; all present secondaries corroborate
    PATIENT_NAME_MISSING            — no patient name extractable from document
    PATIENT_IDENTITY_AMBIGUOUS      — partial/ambiguous evidence
    PATIENT_NAME_MISMATCH           — normalized names are clearly different
    PATIENT_DOB_CONFLICT            — name matches but DOB clearly conflicts
    PATIENT_SECONDARY_ID_CONFLICT   — name matches but a secondary ID conflicts
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from backend.health_vault.acquisition.gmail_models import (
    AcquisitionDecision,
    IdentityReasonCode,
    PatientIdentityResult,
)


# ---------------------------------------------------------------------------
# Name normalization
# ---------------------------------------------------------------------------

# Characters stripped during normalization (punctuation that frequently
# appears in names on medical records but is not semantically meaningful).
_STRIP_PUNCT = re.compile(r"[,.\-'\"_]+")
_MULTI_SPACE = re.compile(r"\s+")


def normalize_name(raw: str) -> str:
    """Return a deterministic normalized form of a person's name.

    Steps:
    1. Decode to NFC Unicode.
    2. Lower-case.
    3. Strip leading/trailing whitespace.
    4. Strip select punctuation (commas, dots, hyphens, apostrophes, quotes).
    5. Collapse internal whitespace to single spaces.

    This is NOT fuzzy matching.  Two names are equal only if their
    normalized forms are byte-identical.
    """
    if not raw or not isinstance(raw, str):
        return ""
    # NFC normalize
    s = unicodedata.normalize("NFC", raw)
    s = s.lower()
    s = _STRIP_PUNCT.sub(" ", s)
    s = _MULTI_SPACE.sub(" ", s)
    s = s.strip()
    return s


def parse_name_parts(raw: str) -> dict[str, str]:
    """Parse a raw name string into ``first``, ``middle``, ``last`` parts.

    Handles two common medical-record name formats:
    - "LAST, FIRST MIDDLE"  (comma separates family from given)
    - "FIRST [MIDDLE] LAST" (space-separated, last word = family)

    Returns a dict with keys ``first``, ``middle``, ``last``.
    All values are normalized via ``normalize_name()``.
    """
    if not raw or not isinstance(raw, str):
        return {"first": "", "middle": "", "last": ""}

    raw = raw.strip()

    if "," in raw:
        # "LAST, FIRST MIDDLE" format
        parts = raw.split(",", 1)
        last = normalize_name(parts[0])
        given_parts = normalize_name(parts[1]).split()
        first = given_parts[0] if given_parts else ""
        middle = " ".join(given_parts[1:]) if len(given_parts) > 1 else ""
    else:
        # "FIRST [MIDDLE] LAST" format
        tokens = normalize_name(raw).split()
        if not tokens:
            return {"first": "", "middle": "", "last": ""}
        last = tokens[-1]
        first = tokens[0] if len(tokens) > 1 else ""
        middle = " ".join(tokens[1:-1]) if len(tokens) > 2 else ""

    return {"first": first, "middle": middle, "last": last}


def _names_match(doc_name: str, profile_name: str) -> bool:
    """Return True if the two names match under the deterministic identity policy.

    Rules (in order):
    1. If normalized full-string forms are identical → MATCH.
    2. Parse both into parts.  If first+last match and middle is absent in
       either side → MATCH (middle-name omission tolerance).
    3. Otherwise → NO MATCH.

    No approximate/edit-distance comparison is performed.
    """
    norm_doc = normalize_name(doc_name)
    norm_profile = normalize_name(profile_name)

    if norm_doc == norm_profile:
        return True

    doc_parts = parse_name_parts(doc_name)
    pro_parts = parse_name_parts(profile_name)

    # Both must have a non-empty first and last for part-level comparison.
    if not doc_parts["first"] or not doc_parts["last"]:
        return False
    if not pro_parts["first"] or not pro_parts["last"]:
        return False

    first_match = doc_parts["first"] == pro_parts["first"]
    last_match = doc_parts["last"] == pro_parts["last"]

    if not (first_match and last_match):
        return False

    # Middle-name tolerance: match only if at least one side has no middle.
    doc_mid = doc_parts["middle"]
    pro_mid = pro_parts["middle"]
    if doc_mid and pro_mid and doc_mid != pro_mid:
        return False  # Both have middles that differ → no match

    return True


# ---------------------------------------------------------------------------
# Secondary identifier helpers
# ---------------------------------------------------------------------------

_DATE_NORM = re.compile(r"[-/.]")


def _normalize_date(raw: str | None) -> str:
    """Return YYYYMMDD from common date formats, or '' on failure."""
    if not raw or not isinstance(raw, str):
        return ""
    cleaned = _DATE_NORM.sub("", raw.strip())
    # Accept YYYYMMDD (8 digits)
    if re.fullmatch(r"\d{8}", cleaned):
        return cleaned
    # Accept MMDDYYYY (8 digits) — detect by plausible month range
    if re.fullmatch(r"\d{8}", cleaned):
        month = int(cleaned[:2])
        if 1 <= month <= 12:
            return cleaned[4:] + cleaned[:4]  # convert to YYYYMMDD
    return ""


def _normalize_sex(raw: str | None) -> str:
    """Normalize sex/gender to a canonical lowercase token."""
    if not raw or not isinstance(raw, str):
        return ""
    val = raw.strip().lower()
    if val in ("m", "male"):
        return "male"
    if val in ("f", "female"):
        return "female"
    return val


# ---------------------------------------------------------------------------
# PatientIdentityVerifier
# ---------------------------------------------------------------------------


class PatientIdentityVerifier:
    """Profile-driven patient identity verifier.

    Reads the registered HealthChecker user's identity from
    ``VaultStore.get_profile()`` and compares it against identity
    fields extracted from a medical document.

    The verifier is stateless across calls; ``profile`` is supplied at
    construction time (or can be overridden per-call in tests).

    Profile keys consumed (all optional, but ``name`` is the primary gate):
        name            (str)
        date_of_birth   (str — ISO-8601 or YYYY-MM-DD)
        sex             (str)
        mrn             (str)
    """

    def __init__(self, profile: dict[str, Any]) -> None:
        self._profile = dict(profile or {})

    @classmethod
    def from_store(cls, store: Any) -> "PatientIdentityVerifier":
        """Construct from a live VaultStore instance."""
        profile = store.get_profile()
        return cls(profile)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def verify(
        self,
        document_fields: dict[str, Any],
    ) -> PatientIdentityResult:
        """Compare document patient fields against the registered profile.

        Parameters
        ----------
        document_fields:
            Dict of patient-identity fields extracted from the medical
            document itself.  Recognised keys:
                patient_name    (str)
                date_of_birth   (str)
                sex             (str)
                mrn             (str)

        Returns
        -------
        PatientIdentityResult
            Structured result with decision and reason code.

        Decision matrix
        ---------------
        - PATIENT_NAME_MISSING  → REVIEW (cannot auto-accept without name)
        - PATIENT_NAME_MISMATCH → REJECT
        - PATIENT_IDENTITY_AMBIGUOUS → REVIEW
          (name matches but registered profile has no name to compare against,
           or document has ambiguous/partial name)
        - PATIENT_DOB_CONFLICT → REVIEW (name matches, DOB clearly conflicts)
        - PATIENT_SECONDARY_ID_CONFLICT → REVIEW (name matches, secondary ID conflicts)
        - PATIENT_IDENTITY_MATCH → ACCEPT-eligible
        """
        doc_name: str = (document_fields.get("patient_name") or "").strip()
        profile_name: str = (self._profile.get("name") or "").strip()

        # --- Primary gate: patient name from document ---
        if not doc_name:
            return PatientIdentityResult(
                decision=AcquisitionDecision.REVIEW,
                reason_code=IdentityReasonCode.PATIENT_NAME_MISSING,
                detail="No patient name found in document",
            )

        # If the registered profile has no name, we cannot confirm identity.
        if not profile_name:
            return PatientIdentityResult(
                decision=AcquisitionDecision.REVIEW,
                reason_code=IdentityReasonCode.PATIENT_IDENTITY_AMBIGUOUS,
                detail="Registered user profile contains no name for comparison",
            )

        # Name comparison
        if not _names_match(doc_name, profile_name):
            return PatientIdentityResult(
                decision=AcquisitionDecision.REJECT,
                reason_code=IdentityReasonCode.PATIENT_NAME_MISMATCH,
                detail="Document patient name does not match registered user",
            )

        # --- Name matched — evaluate secondary identifiers ---
        matched_fields: list[str] = ["name"]
        conflict_fields: list[str] = []

        # Date of birth
        doc_dob = _normalize_date(document_fields.get("date_of_birth"))
        profile_dob = _normalize_date(self._profile.get("date_of_birth"))
        if doc_dob and profile_dob:
            if doc_dob == profile_dob:
                matched_fields.append("date_of_birth")
            else:
                conflict_fields.append("date_of_birth")
                return PatientIdentityResult(
                    decision=AcquisitionDecision.REVIEW,
                    reason_code=IdentityReasonCode.PATIENT_DOB_CONFLICT,
                    matched_fields=["name"],
                    conflict_fields=["date_of_birth"],
                    detail="Name matched but date_of_birth conflicts",
                )

        # Sex / gender
        doc_sex = _normalize_sex(document_fields.get("sex"))
        profile_sex = _normalize_sex(self._profile.get("sex"))
        if doc_sex and profile_sex:
            if doc_sex == profile_sex:
                matched_fields.append("sex")
            else:
                conflict_fields.append("sex")
                # Sex conflict with name match → REVIEW (not hard REJECT;
                # recording errors are more common for sex than name)
                return PatientIdentityResult(
                    decision=AcquisitionDecision.REVIEW,
                    reason_code=IdentityReasonCode.PATIENT_SECONDARY_ID_CONFLICT,
                    matched_fields=["name"],
                    conflict_fields=["sex"],
                    detail="Name matched but sex/gender conflicts",
                )

        # MRN / patient identifier (optional supporting field)
        doc_mrn = (document_fields.get("mrn") or "").strip()
        profile_mrn = (self._profile.get("mrn") or "").strip()
        if doc_mrn and profile_mrn:
            if doc_mrn == profile_mrn:
                matched_fields.append("mrn")
            else:
                conflict_fields.append("mrn")
                return PatientIdentityResult(
                    decision=AcquisitionDecision.REVIEW,
                    reason_code=IdentityReasonCode.PATIENT_SECONDARY_ID_CONFLICT,
                    matched_fields=["name"],
                    conflict_fields=["mrn"],
                    detail="Name matched but MRN conflicts",
                )

        # All present identifiers corroborate → MATCH
        return PatientIdentityResult(
            decision=AcquisitionDecision.ACCEPT,
            reason_code=IdentityReasonCode.PATIENT_IDENTITY_MATCH,
            matched_fields=matched_fields,
            conflict_fields=[],
            detail="Patient identity confirmed",
        )


__all__ = [
    "PatientIdentityVerifier",
    "normalize_name",
    "parse_name_parts",
]
