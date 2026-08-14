from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest

from backend.health_vault.companion_host.offline_ceremony_readiness import (
    ABORT_CONDITIONS,
    CEREMONY_ORDER,
    EXIT_BLOCKED,
    EXIT_FAIL,
    INPUT_SCHEMA,
    MAX_INPUT_BYTES,
    CeremonyReadinessError,
    evaluate_ceremony_readiness,
    parse_ceremony_readiness,
    validate_ceremony_readiness,
)


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "backend/health_vault/companion_host/offline_ceremony_readiness.py"


def _proposal() -> dict:
    return {
        "schema_version": INPUT_SCHEMA,
        "environment": "pilot",
        "scope": "offline_ceremony_readiness_review_only",
        "custody": {
            "model": "two_custodian",
            "owner_custodian_role": "owner_custodian",
            "independent_custodian_role": "independent_second_custodian",
            "roles_distinct": True,
            "simultaneous_control_required": True,
            "personal_identities_location": "protected_offline_ceremony_record_outside_git",
        },
        "equipment": {
            "root_ca_station": "dedicated_offline",
            "issuing_ca_station": "offline_or_dedicated_isolated",
            "network_state": "disabled_and_independently_verified",
            "equipment_inventory_status": "independently_verified",
            "trusted_time_status": "independently_verified_with_120_second_ceiling",
            "evidence_location": "protected_offline_equipment_record_outside_git",
        },
        "transfer_media": {
            "class": "ceremony_only_removable_media",
            "dedicated": True,
            "encrypted": True,
            "tamper_evident": True,
            "inventory_verified": True,
            "offline_malware_scan_before_and_after": True,
            "write_protected_when_supported": True,
        },
        "backup": {
            "encrypted_copy_count": 2,
            "geographically_separated": True,
            "tamper_evident_storage": True,
            "restore_test_status": "independently_verified_without_live_activation",
            "private_material_in_git": False,
        },
        "tpm_capability": {
            "provider": "Microsoft Platform Crypto Provider",
            "readiness_status": "independently_verified_non_production",
            "algorithms_verified": ["RSA-3072", "ECDSA-P256"],
            "non_exportability_verified": True,
            "acl_enforcement_verified": True,
            "software_fallback": "prohibited",
            "evidence_location": "protected_offline_tpm_capability_record_outside_git",
        },
        "ceremony_sequence": {
            "steps": list(CEREMONY_ORDER),
            "order_locked": True,
            "resume_after_abort": "prohibited_start_new_ceremony",
        },
        "revocation": {
            "root_crl_max_validity_days": 90,
            "leaf_crl_max_freshness_days": 7,
            "explicit_next_update_required": True,
            "independent_signature_verification": True,
            "independent_transfer_verification": True,
            "missing_stale_or_unavailable": "BLOCKED",
            "invalid_or_revoked": "FAIL",
        },
        "rollback": {
            "abort_conditions": list(ABORT_CONDITIONS),
            "partial_state_retention": "prohibited",
            "owner_rollback_authority": True,
            "current_runtime_action": "retain_do_not_delete_or_overwrite",
            "incident_record_location": "protected_offline_incident_record_outside_git",
        },
        "approvals": {
            "owner_readiness_review": "approved",
            "independent_readiness_review": "approved",
            "reviewers_distinct": True,
            "assigned_pen_transition_approved": False,
            "actual_ceremony_authorized": False,
        },
        "authorization": {
            "equipment_mutation_authorized": False,
            "key_creation_authorized": False,
            "certificate_creation_authorized": False,
            "crl_creation_authorized": False,
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


def test_exact_readiness_proposal_is_deterministically_blocked():
    first = validate_ceremony_readiness(_proposal())
    second = validate_ceremony_readiness(_proposal())
    assert first == second
    assert first["authorization"] == "ceremony_readiness_review_only"
    assert first["certification_status"] == "BLOCKED"
    assert first["live_execution_status"] == "BLOCKED"
    assert first["exit_code"] == EXIT_BLOCKED
    assert "PASS" not in json.dumps(first)
    checks = {item["name"]: item["status"] for item in first["checks"]}
    assert checks["assigned_pen_transition"] == "BLOCKED"
    assert checks["actual_ceremony"] == "BLOCKED"
    assert checks["pki_mutation"] == "BLOCKED"
    assert checks["certification_pass"] == "BLOCKED"


@pytest.mark.parametrize(
    ("section", "error"),
    [
        ("custody", "ceremony_custody_invalid"),
        ("equipment", "ceremony_equipment_invalid"),
        ("transfer_media", "ceremony_media_invalid"),
        ("backup", "ceremony_backup_invalid"),
        ("tpm_capability", "ceremony_tpm_invalid"),
        ("revocation", "ceremony_revocation_invalid"),
    ],
)
def test_every_exact_readiness_section_fails_on_substitution(section: str, error: str):
    proposal = _proposal()
    first = next(iter(proposal[section]))
    proposal[section][first] = "substituted"
    with pytest.raises(CeremonyReadinessError, match=error):
        validate_ceremony_readiness(proposal)


@pytest.mark.parametrize(
    ("path", "value", "error"),
    [
        (("environment",), "production", "ceremony_policy_invalid"),
        (("scope",), "live_ceremony", "ceremony_policy_invalid"),
        (("custody", "roles_distinct"), False, "ceremony_custody_invalid"),
        (("custody", "simultaneous_control_required"), False, "ceremony_custody_invalid"),
        (("equipment", "network_state"), "connected", "ceremony_equipment_invalid"),
        (("transfer_media", "encrypted"), False, "ceremony_media_invalid"),
        (("backup", "encrypted_copy_count"), 1, "ceremony_backup_invalid"),
        (("backup", "private_material_in_git"), True, "ceremony_backup_invalid"),
        (("tpm_capability", "software_fallback"), "allowed", "ceremony_tpm_invalid"),
        (("revocation", "root_crl_max_validity_days"), 91, "ceremony_revocation_invalid"),
        (("revocation", "leaf_crl_max_freshness_days"), 8, "ceremony_revocation_invalid"),
    ],
)
def test_critical_policy_relaxations_are_rejected(
    path: tuple[str, ...], value: object, error: str
):
    with pytest.raises(CeremonyReadinessError, match=error):
        validate_ceremony_readiness(_set(path, value))


@pytest.mark.parametrize("steps", [list(reversed(CEREMONY_ORDER)), list(CEREMONY_ORDER[:-1]), list(CEREMONY_ORDER) + ["extra"]])
def test_sequence_is_complete_and_order_locked(steps: list[str]):
    with pytest.raises(CeremonyReadinessError, match="ceremony_sequence_invalid"):
        validate_ceremony_readiness(_set(("ceremony_sequence", "steps"), steps))


@pytest.mark.parametrize("conditions", [list(reversed(ABORT_CONDITIONS)), list(ABORT_CONDITIONS[:-1]), []])
def test_abort_conditions_cannot_be_removed_or_reordered(conditions: list[str]):
    with pytest.raises(CeremonyReadinessError, match="ceremony_rollback_invalid"):
        validate_ceremony_readiness(_set(("rollback", "abort_conditions"), conditions))


@pytest.mark.parametrize("field", list(_proposal()["approvals"]))
def test_approval_boundary_is_exact(field: str):
    current = _proposal()["approvals"][field]
    wrong = not current if type(current) is bool else "pending"
    with pytest.raises(CeremonyReadinessError, match="ceremony_approval_invalid"):
        validate_ceremony_readiness(_set(("approvals", field), wrong))


@pytest.mark.parametrize("field", list(_proposal()["authorization"]))
@pytest.mark.parametrize("value", [True, 0, None, "false"])
def test_all_live_and_mutation_authorizations_remain_exact_false(
    field: str, value: object
):
    with pytest.raises(CeremonyReadinessError, match="ceremony_authorization_invalid"):
        validate_ceremony_readiness(_set(("authorization", field), value))


@pytest.mark.parametrize("section", [None, "custody", "equipment", "transfer_media", "backup", "tpm_capability", "ceremony_sequence", "revocation", "rollback", "approvals", "authorization"])
def test_unknown_fields_are_rejected(section: str | None):
    proposal = _proposal()
    target = proposal if section is None else proposal[section]
    target["unexpected"] = "value"
    with pytest.raises(CeremonyReadinessError, match="ceremony_schema_invalid"):
        validate_ceremony_readiness(proposal)


def test_parser_is_bounded_and_rejects_ambiguous_json():
    invalid = [
        b'{"schema_version":"a","schema_version":"b"}',
        b'{"value":1.0}',
        b'{"value":NaN}',
        b"[]",
        b"\xff",
    ]
    for raw in invalid:
        with pytest.raises(CeremonyReadinessError):
            parse_ceremony_readiness(raw)
    with pytest.raises(CeremonyReadinessError, match="ceremony_input_invalid"):
        parse_ceremony_readiness(b" " * (MAX_INPUT_BYTES + 1))


def test_evaluator_returns_one_redacted_failure_record():
    result = evaluate_ceremony_readiness(
        b'{"custodian_private_name":"sensitive-canary"}'
    )
    assert result == {
        "authorization": "ceremony_readiness_review_only",
        "certification_status": "FAIL",
        "environment": "pilot",
        "error": "ceremony_readiness_invalid",
        "exit_code": EXIT_FAIL,
        "live_execution_status": "BLOCKED",
        "schema_version": "hc.r4f_exec_offline_ceremony_readiness_result.v1",
    }
    assert "sensitive-canary" not in json.dumps(result)


def test_validator_does_not_mutate_input_or_return_private_assertions():
    proposal = _proposal()
    original = copy.deepcopy(proposal)
    result = validate_ceremony_readiness(proposal)
    assert proposal == original
    rendered = json.dumps(result)
    assert "Microsoft Platform Crypto Provider" not in rendered
    assert "protected_offline" not in rendered


def test_module_has_no_host_network_filesystem_process_or_crypto_capability():
    tree = ast.parse(MODULE.read_text(encoding="utf-8-sig"))
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not imports & {
        "ctypes",
        "cryptography",
        "hashlib",
        "os",
        "pathlib",
        "requests",
        "socket",
        "ssl",
        "subprocess",
        "tempfile",
        "urllib",
        "winreg",
    }
