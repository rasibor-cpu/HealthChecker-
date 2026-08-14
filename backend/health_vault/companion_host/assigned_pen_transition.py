"""Fail-closed HC-309 assigned-PEN transition proposal validation.

This module validates a bounded, operator-supplied review record. It does not
query IANA, authenticate reviewer identity, write repository configuration, or
authorize PKI mutation, signing, installation, activation, or certification.
"""

from __future__ import annotations

import json
import re
from typing import Any


INPUT_SCHEMA = "hc.r4f_exec_assigned_pen_transition.v1"
RESULT_SCHEMA = "hc.r4f_exec_assigned_pen_transition_result.v1"
EXIT_BLOCKED = 20
EXIT_FAIL = 22
MAX_INPUT_BYTES = 16_384
MAX_PEN = 4_294_967_294
EXAMPLE_PENS = frozenset({32_473})
OID = re.compile(r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))+\Z")


class TransitionError(ValueError):
    """A privacy-safe transition proposal validation failure."""


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise TransitionError("transition_json_invalid")
        result[key] = value
    return result


def _integer(value: str) -> int:
    if len(value.lstrip("-")) > 10:
        raise TransitionError("transition_json_invalid")
    return int(value)


def parse_transition_proposal(raw: bytes) -> dict[str, Any]:
    """Parse one bounded JSON object without accepting duplicate keys or floats."""

    if type(raw) is not bytes or not raw or len(raw) > MAX_INPUT_BYTES:
        raise TransitionError("transition_input_invalid")
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs,
            parse_int=_integer,
            parse_float=lambda _value: (_ for _ in ()).throw(
                TransitionError("transition_json_invalid")
            ),
            parse_constant=lambda _value: (_ for _ in ()).throw(
                TransitionError("transition_json_invalid")
            ),
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError, MemoryError):
        raise TransitionError("transition_input_invalid") from None
    if type(value) is not dict:
        raise TransitionError("transition_schema_invalid")
    return value


def _exact(value: Any, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise TransitionError("transition_schema_invalid")
    return value


def _text(value: Any, expected: str | None = None) -> str:
    if type(value) is not str or not value or len(value) > 160:
        raise TransitionError("transition_schema_invalid")
    if expected is not None and value != expected:
        raise TransitionError("transition_policy_invalid")
    return value


def _false(value: Any) -> None:
    if value is not False:
        raise TransitionError("transition_authorization_invalid")


def _validate_oid(value: Any, expected: str) -> None:
    if type(value) is not str or len(value) > 96 or OID.fullmatch(value) is None:
        raise TransitionError("transition_oid_invalid")
    if value != expected:
        raise TransitionError("transition_oid_arc_invalid")


def validate_transition_proposal(value: Any) -> dict[str, Any]:
    """Validate a reviewed proposal while leaving every live gate BLOCKED."""

    root = _exact(
        value,
        {
            "schema_version",
            "owner",
            "environment",
            "registry_verification",
            "identifiers",
            "review",
            "authorization",
        },
    )
    _text(root["schema_version"], INPUT_SCHEMA)
    _text(root["owner"], "Robert Asibor")
    _text(root["environment"], "pilot")

    registry = _exact(
        root["registry_verification"],
        {
            "registry",
            "assignee",
            "assigned_pen",
            "verification_status",
            "verified_by_role",
            "evidence_location",
        },
    )
    _text(registry["registry"], "IANA Private Enterprise Numbers registry")
    _text(registry["assignee"], "Robert Asibor")
    pen = registry["assigned_pen"]
    if type(pen) is not int or not 1 <= pen <= MAX_PEN or pen in EXAMPLE_PENS:
        raise TransitionError("transition_pen_invalid")
    _text(registry["verification_status"], "independently_verified")
    _text(registry["verified_by_role"], "independent_reviewer")
    _text(
        registry["evidence_location"],
        "protected_offline_registry_evidence_outside_git",
    )

    identifiers = _exact(
        root["identifiers"],
        {
            "enterprise_arc",
            "certificate_policy_oid",
            "evidence_eku_oid",
            "derivation_profile",
        },
    )
    enterprise_arc = f"1.3.6.1.4.1.{pen}"
    _validate_oid(identifiers["enterprise_arc"], enterprise_arc)
    _validate_oid(identifiers["certificate_policy_oid"], f"{enterprise_arc}.1.1")
    _validate_oid(identifiers["evidence_eku_oid"], f"{enterprise_arc}.2.1")
    if identifiers["certificate_policy_oid"] == identifiers["evidence_eku_oid"]:
        raise TransitionError("transition_oid_reuse_invalid")
    _text(
        identifiers["derivation_profile"],
        "enterprise.1.1_certificate_policy_and_enterprise.2.1_evidence_eku",
    )

    review = _exact(
        root["review"],
        {
            "owner_review",
            "independent_oid_review",
            "reviewers_distinct",
            "separate_commit_required",
            "offline_ceremony_review_required",
        },
    )
    expected_review = {
        "owner_review": "approved",
        "independent_oid_review": "approved",
        "reviewers_distinct": True,
        "separate_commit_required": True,
        "offline_ceremony_review_required": True,
    }
    if review != expected_review:
        raise TransitionError("transition_review_invalid")

    authorization = _exact(
        root["authorization"],
        {
            "pki_mutation_authorized",
            "key_creation_authorized",
            "certificate_creation_authorized",
            "signing_authorized",
            "runtime_reinstall_authorized",
            "activation_authorized",
            "certification_pass_reachable",
        },
    )
    for allowed in authorization.values():
        _false(allowed)

    return {
        "authorization": "transition_review_only",
        "certification_status": "BLOCKED",
        "checks": [
            {"name": "pen_assignment", "status": "READY_FOR_SEPARATE_APPROVAL"},
            {"name": "enterprise_arc", "status": "READY_FOR_SEPARATE_APPROVAL"},
            {"name": "purpose_oids", "status": "READY_FOR_SEPARATE_APPROVAL"},
            {"name": "two_reviewer_record", "status": "READY_FOR_SEPARATE_APPROVAL"},
            {"name": "offline_ceremony_review", "status": "BLOCKED"},
            {"name": "pki_mutation", "status": "BLOCKED"},
            {"name": "signing", "status": "BLOCKED"},
            {"name": "runtime_reinstall", "status": "BLOCKED"},
            {"name": "certification_pass", "status": "BLOCKED"},
        ],
        "environment": "pilot",
        "exit_code": EXIT_BLOCKED,
        "live_execution_status": "BLOCKED",
        "schema_version": RESULT_SCHEMA,
    }


def evaluate_transition_proposal(raw: bytes) -> dict[str, Any]:
    """Return a deterministic redacted result for untrusted proposal bytes."""

    try:
        return validate_transition_proposal(parse_transition_proposal(raw))
    except TransitionError:
        return {
            "authorization": "transition_review_only",
            "certification_status": "FAIL",
            "environment": "pilot",
            "error": "transition_proposal_invalid",
            "exit_code": EXIT_FAIL,
            "live_execution_status": "BLOCKED",
            "schema_version": RESULT_SCHEMA,
        }
