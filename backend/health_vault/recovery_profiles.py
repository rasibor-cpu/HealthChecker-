"""HC311 multi-user recovery-question catalog and profile metadata.

The question catalog is system-wide.

Each user profile receives:
- its own selected question IDs;
- its own question-set identifier;
- its own random recovery salt;
- its own recovery-package reference.

PLAINTEXT ANSWERS ARE NEVER STORED HERE.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Final, Iterable


RECOVERY_PROFILE_VERSION: Final[int] = 1
MIN_SELECTED_QUESTIONS: Final[int] = 10
RECOVERY_SALT_BYTES: Final[int] = 16


QUESTION_CATALOG: Final[tuple[tuple[str, str], ...]] = (

    ("Q01", "What was the colour of your first car?"),

    ("Q02", "In what city did you spend your honeymoon?"),

    ("Q03", "Where did your parents first meet?"),

    ("Q04", "What street did you live on while you were in Grade 11 or its equivalent?"),

    ("Q05", "What was the name of your elementary or primary school?"),

    ("Q06", "What was the make or model of the first car you regularly drove?"),

    ("Q07", "What was the first name of a childhood friend you remember especially well?"),

    ("Q08", "What was the name of the street or neighborhood where you spent most of your childhood?"),

    ("Q09", "What was the surname of a teacher you remember particularly well from school?"),

    ("Q10", "What was the name of a place your family visited repeatedly when you were young?"),

    ("Q11", "What was the name of your first employer?"),

    ("Q12", "In what city did you receive your first full-time salary?"),

    ("Q13", "What was the first name of your best friend in secondary or high school?"),

    ("Q14", "What was the name of the first school you attended?"),

    ("Q15", "What was the name of the street where your first job was located?"),

    ("Q16", "What was the name of the hospital or clinic where your oldest child was born?"),

    ("Q17", "What was the destination of your first trip outside your home country?"),

    ("Q18", "What was the name of a memorable childhood neighbor?"),

    ("Q19", "What was the first name of your favorite teacher in primary or elementary school?"),

    ("Q20", "What was the surname of the first manager or supervisor you worked for?"),

    ("Q21", "What was the name of the first neighborhood you remember living in?"),

    ("Q22", "What was the name of a childhood sports team, club, or group you belonged to?"),

    ("Q23", "What was the name of the first bank where you personally held an account?"),

    ("Q24", "What was the name of a school or institution where you completed an important qualification?"),
)


class RecoveryProfileError(RuntimeError):
    """Fail-closed recovery-profile error."""


def catalog() -> dict[str, str]:
    return dict(QUESTION_CATALOG)


def validate_profile_id(profile_id: str) -> str:

    if not isinstance(profile_id, str):
        raise RecoveryProfileError("profile_id_must_be_text")

    value = profile_id.strip()

    if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,128}", value):
        raise RecoveryProfileError("invalid_profile_id")

    return value


def question_set_id(question_ids: Iterable[str]) -> str:

    ids = tuple(question_ids)

    if len(ids) < MIN_SELECTED_QUESTIONS:
        raise RecoveryProfileError("insufficient_questions")

    if len(ids) != len(set(ids)):
        raise RecoveryProfileError("duplicate_question_id")

    known = catalog()

    unknown = [item for item in ids if item not in known]

    if unknown:
        raise RecoveryProfileError("unknown_question_id")

    material = "\n".join(ids).encode("utf-8")

    return hashlib.sha256(
        b"HealthChecker-HC311-profile-question-set-v1\x00"
        + material
    ).hexdigest()


@dataclass(frozen=True)
class RecoveryProfile:

    version: int
    profile_id: str
    question_ids: tuple[str, ...]
    question_set_id: str
    question_salt_hex: str
    recovery_package_reference: str | None
    enrolled_utc: str


def create_recovery_profile(
    profile_id: str,
    question_ids: Iterable[str],
) -> RecoveryProfile:

    profile = validate_profile_id(profile_id)
    ids = tuple(question_ids)

    qsid = question_set_id(ids)

    salt = os.urandom(RECOVERY_SALT_BYTES)

    return RecoveryProfile(
        version=RECOVERY_PROFILE_VERSION,
        profile_id=profile,
        question_ids=ids,
        question_set_id=qsid,
        question_salt_hex=salt.hex(),
        recovery_package_reference=None,
        enrolled_utc=datetime.now(timezone.utc).isoformat(),
    )


def serialize_profile(profile: RecoveryProfile) -> bytes:

    if not isinstance(profile, RecoveryProfile):
        raise RecoveryProfileError("invalid_profile_object")

    payload = asdict(profile)

    # Explicitly forbid accidental answer persistence.
    forbidden = {
        "answer",
        "answers",
        "plaintext_answer",
        "answer_hash",
        "answer_hint",
        "passphrase",
        "data_key",
        "aes_key",
    }

    if forbidden.intersection(payload):
        raise RecoveryProfileError("forbidden_recovery_secret_field")

    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def deserialize_profile(payload: bytes) -> RecoveryProfile:

    try:
        raw = json.loads(payload.decode("utf-8"))
    except Exception as exc:
        raise RecoveryProfileError(
            "invalid_recovery_profile"
        ) from exc

    allowed = {
        "version",
        "profile_id",
        "question_ids",
        "question_set_id",
        "question_salt_hex",
        "recovery_package_reference",
        "enrolled_utc",
    }

    if set(raw) != allowed:
        raise RecoveryProfileError(
            "unexpected_recovery_profile_fields"
        )

    if raw["version"] != RECOVERY_PROFILE_VERSION:
        raise RecoveryProfileError(
            "unsupported_recovery_profile_version"
        )

    profile_id = validate_profile_id(raw["profile_id"])

    ids = tuple(raw["question_ids"])
    expected_qsid = question_set_id(ids)

    if raw["question_set_id"] != expected_qsid:
        raise RecoveryProfileError(
            "question_set_identifier_mismatch"
        )

    try:
        salt = bytes.fromhex(raw["question_salt_hex"])
    except Exception as exc:
        raise RecoveryProfileError(
            "invalid_question_salt"
        ) from exc

    if len(salt) != RECOVERY_SALT_BYTES:
        raise RecoveryProfileError(
            "invalid_question_salt_length"
        )

    return RecoveryProfile(
        version=raw["version"],
        profile_id=profile_id,
        question_ids=ids,
        question_set_id=raw["question_set_id"],
        question_salt_hex=raw["question_salt_hex"],
        recovery_package_reference=raw[
            "recovery_package_reference"
        ],
        enrolled_utc=raw["enrolled_utc"],
    )
