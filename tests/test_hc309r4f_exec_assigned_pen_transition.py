from __future__ import annotations

import copy
import json

import pytest

from backend.health_vault.companion_host.assigned_pen_transition import (
    EXIT_BLOCKED,
    EXIT_FAIL,
    INPUT_SCHEMA,
    MAX_INPUT_BYTES,
    TransitionError,
    evaluate_transition_proposal,
    parse_transition_proposal,
    validate_transition_proposal,
)


PEN = 55_555
ARC = f"1.3.6.1.4.1.{PEN}"


def _proposal() -> dict:
    return {
        "schema_version": INPUT_SCHEMA,
        "owner": "Robert Asibor",
        "environment": "pilot",
        "registry_verification": {
            "registry": "IANA Private Enterprise Numbers registry",
            "assignee": "Robert Asibor",
            "assigned_pen": PEN,
            "verification_status": "independently_verified",
            "verified_by_role": "independent_reviewer",
            "evidence_location": "protected_offline_registry_evidence_outside_git",
        },
        "identifiers": {
            "enterprise_arc": ARC,
            "certificate_policy_oid": f"{ARC}.1.1",
            "evidence_eku_oid": f"{ARC}.2.1",
            "derivation_profile": (
                "enterprise.1.1_certificate_policy_and_enterprise.2.1_evidence_eku"
            ),
        },
        "review": {
            "owner_review": "approved",
            "independent_oid_review": "approved",
            "reviewers_distinct": True,
            "separate_commit_required": True,
            "offline_ceremony_review_required": True,
        },
        "authorization": {
            "pki_mutation_authorized": False,
            "key_creation_authorized": False,
            "certificate_creation_authorized": False,
            "signing_authorized": False,
            "runtime_reinstall_authorized": False,
            "activation_authorized": False,
            "certification_pass_reachable": False,
        },
    }


def _set(path: tuple[str, ...], value: object) -> dict:
    proposal = copy.deepcopy(_proposal())
    target = proposal
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = value
    return proposal


def _proposal_for_pen(pen: int) -> dict:
    proposal = _proposal()
    arc = f"1.3.6.1.4.1.{pen}"
    proposal["registry_verification"]["assigned_pen"] = pen
    proposal["identifiers"]["enterprise_arc"] = arc
    proposal["identifiers"]["certificate_policy_oid"] = f"{arc}.1.1"
    proposal["identifiers"]["evidence_eku_oid"] = f"{arc}.2.1"
    return proposal


def test_valid_reviewed_proposal_remains_blocked_and_deterministic():
    first = validate_transition_proposal(_proposal())
    second = validate_transition_proposal(_proposal())
    assert first == second
    assert first["certification_status"] == "BLOCKED"
    assert first["live_execution_status"] == "BLOCKED"
    assert first["authorization"] == "transition_review_only"
    assert first["exit_code"] == EXIT_BLOCKED
    assert "PASS" not in json.dumps(first)
    assert {item["status"] for item in first["checks"]} == {
        "READY_FOR_SEPARATE_APPROVAL",
        "BLOCKED",
    }


@pytest.mark.parametrize(
    "pen", [True, False, None, 0, -1, 32_473, 2**32 - 1, 2**32, "55555"]
)
def test_invalid_reserved_example_and_wrong_type_pens_are_rejected(pen: object):
    with pytest.raises(TransitionError, match="transition_pen_invalid"):
        validate_transition_proposal(_set(("registry_verification", "assigned_pen"), pen))


@pytest.mark.parametrize("pen", [1, 4_294_967_294])
def test_assignable_pen_boundaries_are_accepted_for_separate_review(pen: int):
    assert validate_transition_proposal(_proposal_for_pen(pen))["exit_code"] == EXIT_BLOCKED


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("enterprise_arc", "1.3.6.1.4.1.99999"),
        ("certificate_policy_oid", f"{ARC}.1.2"),
        ("certificate_policy_oid", "1.3.6.1.4.1.311.1.1"),
        ("certificate_policy_oid", "2.25.123"),
        ("certificate_policy_oid", f"{ARC}.01.1"),
        ("evidence_eku_oid", f"{ARC}.1.1"),
        ("evidence_eku_oid", "not-an-oid"),
    ],
)
def test_foreign_placeholder_malformed_and_reused_oids_are_rejected(
    field: str, value: str
):
    with pytest.raises(TransitionError, match="transition_oid"):
        validate_transition_proposal(_set(("identifiers", field), value))


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("owner",), "another owner"),
        (("environment",), "production"),
        (("registry_verification", "registry"), "untrusted registry"),
        (("registry_verification", "assignee"), "different assignee"),
        (("registry_verification", "verification_status"), "self_asserted"),
        (("registry_verification", "verified_by_role"), "owner"),
        (("registry_verification", "evidence_location"), "repository"),
        (("identifiers", "derivation_profile"), "caller_selected"),
    ],
)
def test_identity_registry_and_policy_substitution_is_rejected(
    path: tuple[str, ...], value: object
):
    with pytest.raises(TransitionError, match="transition_policy_invalid"):
        validate_transition_proposal(_set(path, value))


@pytest.mark.parametrize("field", list(_proposal()["review"]))
def test_every_review_gate_is_mandatory(field: str):
    wrong = False if field not in {"owner_review", "independent_oid_review"} else "pending"
    with pytest.raises(TransitionError, match="transition_review_invalid"):
        validate_transition_proposal(_set(("review", field), wrong))


@pytest.mark.parametrize("field", list(_proposal()["authorization"]))
@pytest.mark.parametrize("value", [True, 0, None, "false"])
def test_no_authorization_field_can_be_enabled_or_loosely_typed(
    field: str, value: object
):
    with pytest.raises(TransitionError, match="transition_authorization_invalid"):
        validate_transition_proposal(_set(("authorization", field), value))


@pytest.mark.parametrize("container", [(), ("registry_verification",), ("identifiers",), ("review",), ("authorization",)])
def test_unknown_fields_are_rejected(container: tuple[str, ...]):
    proposal = _proposal()
    target = proposal
    for component in container:
        target = target[component]
    target["unexpected"] = "value"
    with pytest.raises(TransitionError, match="transition_schema_invalid"):
        validate_transition_proposal(proposal)


def test_parser_rejects_duplicates_floats_constants_non_objects_and_oversize():
    invalid = [
        b'{"schema_version":"a","schema_version":"b"}',
        b'{"assigned_pen":1.0}',
        b'{"assigned_pen":NaN}',
        b"[]",
    ]
    for raw in invalid:
        with pytest.raises(TransitionError):
            parse_transition_proposal(raw)
    with pytest.raises(TransitionError, match="transition_input_invalid"):
        parse_transition_proposal(b" " * (MAX_INPUT_BYTES + 1))


def test_redacted_evaluator_never_echoes_sensitive_or_malformed_input():
    raw = b'{"private-reviewer-name":"sensitive-canary"}'
    result = evaluate_transition_proposal(raw)
    assert result == {
        "authorization": "transition_review_only",
        "certification_status": "FAIL",
        "environment": "pilot",
        "error": "transition_proposal_invalid",
        "exit_code": EXIT_FAIL,
        "live_execution_status": "BLOCKED",
        "schema_version": "hc.r4f_exec_assigned_pen_transition_result.v1",
    }
    assert "sensitive-canary" not in json.dumps(result)


def test_validation_does_not_mutate_caller_input_or_return_identifiers():
    proposal = _proposal()
    original = copy.deepcopy(proposal)
    result = validate_transition_proposal(proposal)
    assert proposal == original
    rendered = json.dumps(result)
    assert str(PEN) not in rendered
    assert ARC not in rendered
