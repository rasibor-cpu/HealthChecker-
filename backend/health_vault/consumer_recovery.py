"""HC325-R5 consumer password-recovery question catalog.

Question IDs are stable. Display text is not used as storage identity.
This module stores no answers and no secrets.
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable

REQUIRED_ENROLLMENT_COUNT = 3
MIN_ANSWER_LENGTH = 2

CONSUMER_RECOVERY_QUESTIONS: tuple[tuple[str, str], ...] = (
    ("CQ01", "What was the name of your first school?"),
    ("CQ02", "What city were you born in?"),
    ("CQ03", "What was the name of your first pet?"),
    ("CQ04", "What was the make or model of your first car?"),
    ("CQ05", "What was the name of your childhood best friend?"),
    ("CQ06", "What street did you grow up on?"),
    ("CQ07", "What was the name of your primary school teacher?"),
    ("CQ08", "What city did your parents meet in?"),
)

_QUESTION_MAP = {qid: prompt for qid, prompt in CONSUMER_RECOVERY_QUESTIONS}


def catalog_public() -> list[dict[str, str]]:
    return [{"question_id": qid, "prompt": prompt} for qid, prompt in CONSUMER_RECOVERY_QUESTIONS]


def prompt_for(question_id: str) -> str | None:
    return _QUESTION_MAP.get(str(question_id or ""))


def normalize_answer(answer: str) -> str:
    return " ".join(str(answer or "").strip().lower().split())


def dummy_question_ids(user_id: str) -> list[str]:
    digest = hashlib.sha256(str(user_id or "unknown").encode("utf-8")).digest()
    ids = [qid for qid, _ in CONSUMER_RECOVERY_QUESTIONS]
    unique: list[str] = []
    for index in range(len(ids) * 2):
        candidate = ids[(digest[index % len(digest)] + index * 3) % len(ids)]
        if candidate not in unique:
            unique.append(candidate)
        if len(unique) == REQUIRED_ENROLLMENT_COUNT:
            return unique
    for qid in ids:
        if qid not in unique:
            unique.append(qid)
        if len(unique) == REQUIRED_ENROLLMENT_COUNT:
            break
    return unique


def public_questions(question_ids: Iterable[str]) -> list[dict[str, str]]:
    rows = []
    for qid in question_ids:
        prompt = prompt_for(qid)
        if prompt:
            rows.append({"question_id": qid, "prompt": prompt})
    return rows


def parse_enrollment_pairs(raw: Any) -> list[tuple[str, str]]:
    if not isinstance(raw, list):
        raise ValueError("recovery_enrollment_required")
    seen: set[str] = set()
    pairs: list[tuple[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("recovery_enrollment_required")
        qid = str(item.get("question_id") or "").strip()
        answer = normalize_answer(str(item.get("answer") or ""))
        if qid not in _QUESTION_MAP or qid in seen or len(answer) < MIN_ANSWER_LENGTH:
            raise ValueError("recovery_enrollment_required")
        seen.add(qid)
        pairs.append((qid, answer))
    if len(pairs) != REQUIRED_ENROLLMENT_COUNT:
        raise ValueError("recovery_enrollment_required")
    return pairs
