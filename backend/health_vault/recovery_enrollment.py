"""HC311 reusable multi-user recovery enrollment.

The enrollment API:
- supports any HealthChecker profile;
- uses that profile's selected recovery questions and random salt;
- requires confirmation of all answers during enrollment;
- returns the derived recovery passphrase only in memory;
- never stores plaintext answers, answer hashes, or hints.

Persistence of profile metadata is separate from recovery secrets.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Iterable

from backend.health_vault.recovery_profiles import (
    RecoveryProfile,
    RecoveryProfileError,
    create_recovery_profile,
    serialize_profile,
)

from backend.health_vault.vault_question_recovery import (
    QuestionRecoveryError,
    credential_to_passphrase,
    derive_recovery_credential,
)


class RecoveryEnrollmentError(RuntimeError):
    """Fail-closed profile recovery-enrollment error."""


@dataclass(frozen=True)
class RecoveryEnrollmentResult:
    """Enrollment metadata plus an in-memory recovery credential.

    recovery_passphrase MUST NOT be serialized or logged.
    """

    profile: RecoveryProfile
    recovery_passphrase: str


def _answers_tuple(answers: Iterable[str]) -> tuple[str, ...]:
    try:
        return tuple(answers)
    except Exception as exc:
        raise RecoveryEnrollmentError(
            "invalid_recovery_answers"
        ) from exc


def enroll_recovery_profile(
    profile_id: str,
    question_ids: Iterable[str],
    answers_first: Iterable[str],
    answers_confirm: Iterable[str],
) -> RecoveryEnrollmentResult:
    """Create profile-scoped recovery enrollment in memory.

    No answer material is persisted by this function.
    """

    ids = tuple(question_ids)
    first = _answers_tuple(answers_first)
    confirm = _answers_tuple(answers_confirm)

    if len(first) != len(ids):
        raise RecoveryEnrollmentError(
            "answer_count_does_not_match_questions"
        )

    if len(confirm) != len(ids):
        raise RecoveryEnrollmentError(
            "confirmation_count_does_not_match_questions"
        )

    try:
        profile = create_recovery_profile(
            profile_id,
            ids,
        )
    except RecoveryProfileError as exc:
        raise RecoveryEnrollmentError(
            "recovery_profile_creation_failed"
        ) from exc

    salt = bytes.fromhex(profile.question_salt_hex)

    try:
        credential_first = derive_recovery_credential(
            first,
            salt,
        )

        credential_confirm = derive_recovery_credential(
            confirm,
            salt,
        )
    except QuestionRecoveryError as exc:
        raise RecoveryEnrollmentError(
            "recovery_credential_derivation_failed"
        ) from exc

    if not hmac.compare_digest(
        credential_first,
        credential_confirm,
    ):
        raise RecoveryEnrollmentError(
            "recovery_answer_confirmation_mismatch"
        )

    passphrase = credential_to_passphrase(
        credential_first
    )

    return RecoveryEnrollmentResult(
        profile=profile,
        recovery_passphrase=passphrase,
    )


def serialize_enrollment_metadata(
    result: RecoveryEnrollmentResult,
) -> bytes:
    """Serialize ONLY non-secret profile enrollment metadata."""

    if not isinstance(result, RecoveryEnrollmentResult):
        raise RecoveryEnrollmentError(
            "invalid_enrollment_result"
        )

    payload = serialize_profile(result.profile)

    # Explicit defense against accidental credential persistence.
    forbidden = (
        result.recovery_passphrase.encode("utf-8"),
        b'"answer"',
        b'"answers"',
        b'"passphrase"',
        b'"data_key"',
        b'"aes_key"',
    )

    for marker in forbidden:
        if marker and marker in payload:
            raise RecoveryEnrollmentError(
                "secret_material_detected_in_profile_metadata"
            )

    return payload
