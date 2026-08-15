"""HC311 question-based recovery credential derivation.

Security contract:
- answers are never persisted by this module;
- answers are normalized deterministically;
- all enrolled answers are required;
- answers are combined before a memory-hard KDF;
- per-package random salt prevents precomputed tables;
- derived credential is suitable as input to the existing
  authenticated recovery-package layer.

This module does NOT create or store production AES keys.
"""

from __future__ import annotations

import hashlib
import unicodedata
from typing import Iterable

from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


SCRYPT_N = 32768
SCRYPT_R = 8
SCRYPT_P = 1
DERIVED_BYTES = 32
MIN_QUESTIONS = 8


class QuestionRecoveryError(RuntimeError):
    pass


def normalize_answer(answer: str) -> str:
    if not isinstance(answer, str):
        raise QuestionRecoveryError("answer_must_be_string")

    # Unicode canonicalization.
    value = unicodedata.normalize("NFKC", answer)

    # Case-independent comparison.
    value = value.casefold()

    # Normalize whitespace without guessing spelling.
    value = " ".join(value.split())

    if not value:
        raise QuestionRecoveryError("empty_answer")

    return value


def combine_answers(answers: Iterable[str]) -> bytes:
    normalized = [normalize_answer(a) for a in answers]

    if len(normalized) < MIN_QUESTIONS:
        raise QuestionRecoveryError("insufficient_recovery_answers")

    # Length-prefix every normalized answer so boundaries are unambiguous.
    material = bytearray()

    for answer in normalized:
        encoded = answer.encode("utf-8")
        material.extend(len(encoded).to_bytes(4, "big"))
        material.extend(encoded)

    return bytes(material)


def derive_recovery_credential(
    answers: Iterable[str],
    salt: bytes,
) -> bytes:
    if not isinstance(salt, bytes) or len(salt) != 16:
        raise QuestionRecoveryError("recovery_salt_must_be_16_bytes")

    material = combine_answers(answers)

    try:
        return Scrypt(
            salt=salt,
            length=DERIVED_BYTES,
            n=SCRYPT_N,
            r=SCRYPT_R,
            p=SCRYPT_P,
        ).derive(material)
    except Exception as exc:
        raise QuestionRecoveryError("question_recovery_kdf_failure") from exc


def credential_to_passphrase(credential: bytes) -> str:
    if not isinstance(credential, bytes) or len(credential) != DERIVED_BYTES:
        raise QuestionRecoveryError("invalid_recovery_credential")

    # Existing HCRP API accepts a string passphrase.
    # Hex encoding is reversible representation of the 256-bit KDF result;
    # it does not reduce the derived credential's entropy.
    return credential.hex()


def question_set_id(question_ids: Iterable[str]) -> str:
    ids = list(question_ids)

    if len(ids) < MIN_QUESTIONS:
        raise QuestionRecoveryError("insufficient_question_ids")

    if len(ids) != len(set(ids)):
        raise QuestionRecoveryError("duplicate_question_id")

    material = "\n".join(ids).encode("utf-8")

    return hashlib.sha256(
        b"HealthChecker-HC311-question-set-v1\x00" + material
    ).hexdigest()
