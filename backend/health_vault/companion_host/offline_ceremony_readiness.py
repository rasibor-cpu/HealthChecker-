"""Fail-closed HC-309 offline ceremony-readiness proposal validation.

The validator is pure policy evaluation. It does not inspect equipment, media,
TPM state, identities, keys, certificates, CRLs, or the host, and it exposes no
ceremony, signing, installation, or mutation operation.
"""

from __future__ import annotations

import json
from typing import Any


INPUT_SCHEMA = "hc.r4f_exec_offline_ceremony_readiness.v1"
RESULT_SCHEMA = "hc.r4f_exec_offline_ceremony_readiness_result.v1"
EXIT_BLOCKED = 20
EXIT_FAIL = 22
MAX_INPUT_BYTES = 32_768

CEREMONY_ORDER = (
    "authorization_and_scope_review",
    "custodian_presence_confirmation",
    "equipment_isolation_verification",
    "trusted_time_verification",
    "media_inventory_verification",
    "root_ca_profile_review",
    "issuing_ca_profile_review",
    "root_crl_procedure_review",
    "leaf_crl_procedure_review",
    "backup_and_restore_review",
    "rollback_and_abort_review",
    "independent_record_review",
    "closeout_and_seal",
)
ABORT_CONDITIONS = (
    "custodian_absence_or_role_conflict",
    "unexpected_network_connectivity",
    "equipment_or_media_identity_mismatch",
    "tpm_or_provider_indeterminate",
    "trusted_time_invalid",
    "evidence_or_procedure_deviation",
)


class CeremonyReadinessError(ValueError):
    """A redacted ceremony-readiness validation failure."""


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise CeremonyReadinessError("ceremony_json_invalid")
        result[key] = value
    return result


def _integer(value: str) -> int:
    if len(value.lstrip("-")) > 10:
        raise CeremonyReadinessError("ceremony_json_invalid")
    return int(value)


def parse_ceremony_readiness(raw: bytes) -> dict[str, Any]:
    """Parse one bounded exact JSON object without floats or duplicate keys."""

    if type(raw) is not bytes or not raw or len(raw) > MAX_INPUT_BYTES:
        raise CeremonyReadinessError("ceremony_input_invalid")
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs,
            parse_int=_integer,
            parse_float=lambda _value: (_ for _ in ()).throw(
                CeremonyReadinessError("ceremony_json_invalid")
            ),
            parse_constant=lambda _value: (_ for _ in ()).throw(
                CeremonyReadinessError("ceremony_json_invalid")
            ),
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError, MemoryError):
        raise CeremonyReadinessError("ceremony_input_invalid") from None
    if type(value) is not dict:
        raise CeremonyReadinessError("ceremony_schema_invalid")
    return value


def _exact(value: Any, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise CeremonyReadinessError("ceremony_schema_invalid")
    return value


def _require(value: Any, expected: Any, error: str) -> None:
    if type(value) is not type(expected) or value != expected:
        raise CeremonyReadinessError(error)


def validate_ceremony_readiness(value: Any) -> dict[str, Any]:
    """Validate readiness assertions while keeping actual ceremony unreachable."""

    root = _exact(
        value,
        {
            "schema_version",
            "environment",
            "scope",
            "custody",
            "equipment",
            "transfer_media",
            "backup",
            "tpm_capability",
            "ceremony_sequence",
            "revocation",
            "rollback",
            "approvals",
            "authorization",
        },
    )
    _require(root["schema_version"], INPUT_SCHEMA, "ceremony_policy_invalid")
    _require(root["environment"], "pilot", "ceremony_policy_invalid")
    _require(root["scope"], "offline_ceremony_readiness_review_only", "ceremony_policy_invalid")

    custody = _exact(
        root["custody"],
        {
            "model",
            "owner_custodian_role",
            "independent_custodian_role",
            "roles_distinct",
            "simultaneous_control_required",
            "personal_identities_location",
        },
    )
    expected_custody = {
        "model": "two_custodian",
        "owner_custodian_role": "owner_custodian",
        "independent_custodian_role": "independent_second_custodian",
        "roles_distinct": True,
        "simultaneous_control_required": True,
        "personal_identities_location": "protected_offline_ceremony_record_outside_git",
    }
    _require(custody, expected_custody, "ceremony_custody_invalid")

    equipment = _exact(
        root["equipment"],
        {
            "root_ca_station",
            "issuing_ca_station",
            "network_state",
            "equipment_inventory_status",
            "trusted_time_status",
            "evidence_location",
        },
    )
    expected_equipment = {
        "root_ca_station": "dedicated_offline",
        "issuing_ca_station": "offline_or_dedicated_isolated",
        "network_state": "disabled_and_independently_verified",
        "equipment_inventory_status": "independently_verified",
        "trusted_time_status": "independently_verified_with_120_second_ceiling",
        "evidence_location": "protected_offline_equipment_record_outside_git",
    }
    _require(equipment, expected_equipment, "ceremony_equipment_invalid")

    media = _exact(
        root["transfer_media"],
        {
            "class",
            "dedicated",
            "encrypted",
            "tamper_evident",
            "inventory_verified",
            "offline_malware_scan_before_and_after",
            "write_protected_when_supported",
        },
    )
    expected_media = {
        "class": "ceremony_only_removable_media",
        "dedicated": True,
        "encrypted": True,
        "tamper_evident": True,
        "inventory_verified": True,
        "offline_malware_scan_before_and_after": True,
        "write_protected_when_supported": True,
    }
    _require(media, expected_media, "ceremony_media_invalid")

    backup = _exact(
        root["backup"],
        {
            "encrypted_copy_count",
            "geographically_separated",
            "tamper_evident_storage",
            "restore_test_status",
            "private_material_in_git",
        },
    )
    expected_backup = {
        "encrypted_copy_count": 2,
        "geographically_separated": True,
        "tamper_evident_storage": True,
        "restore_test_status": "independently_verified_without_live_activation",
        "private_material_in_git": False,
    }
    _require(backup, expected_backup, "ceremony_backup_invalid")

    tpm = _exact(
        root["tpm_capability"],
        {
            "provider",
            "readiness_status",
            "algorithms_verified",
            "non_exportability_verified",
            "acl_enforcement_verified",
            "software_fallback",
            "evidence_location",
        },
    )
    expected_tpm = {
        "provider": "Microsoft Platform Crypto Provider",
        "readiness_status": "independently_verified_non_production",
        "algorithms_verified": ["RSA-3072", "ECDSA-P256"],
        "non_exportability_verified": True,
        "acl_enforcement_verified": True,
        "software_fallback": "prohibited",
        "evidence_location": "protected_offline_tpm_capability_record_outside_git",
    }
    _require(tpm, expected_tpm, "ceremony_tpm_invalid")

    sequence = _exact(
        root["ceremony_sequence"], {"steps", "order_locked", "resume_after_abort"}
    )
    _require(sequence["steps"], list(CEREMONY_ORDER), "ceremony_sequence_invalid")
    _require(sequence["order_locked"], True, "ceremony_sequence_invalid")
    _require(sequence["resume_after_abort"], "prohibited_start_new_ceremony", "ceremony_sequence_invalid")

    revocation = _exact(
        root["revocation"],
        {
            "root_crl_max_validity_days",
            "leaf_crl_max_freshness_days",
            "explicit_next_update_required",
            "independent_signature_verification",
            "independent_transfer_verification",
            "missing_stale_or_unavailable",
            "invalid_or_revoked",
        },
    )
    expected_revocation = {
        "root_crl_max_validity_days": 90,
        "leaf_crl_max_freshness_days": 7,
        "explicit_next_update_required": True,
        "independent_signature_verification": True,
        "independent_transfer_verification": True,
        "missing_stale_or_unavailable": "BLOCKED",
        "invalid_or_revoked": "FAIL",
    }
    _require(revocation, expected_revocation, "ceremony_revocation_invalid")

    rollback = _exact(
        root["rollback"],
        {
            "abort_conditions",
            "partial_state_retention",
            "owner_rollback_authority",
            "current_runtime_action",
            "incident_record_location",
        },
    )
    _require(rollback["abort_conditions"], list(ABORT_CONDITIONS), "ceremony_rollback_invalid")
    _require(rollback["partial_state_retention"], "prohibited", "ceremony_rollback_invalid")
    _require(rollback["owner_rollback_authority"], True, "ceremony_rollback_invalid")
    _require(rollback["current_runtime_action"], "retain_do_not_delete_or_overwrite", "ceremony_rollback_invalid")
    _require(rollback["incident_record_location"], "protected_offline_incident_record_outside_git", "ceremony_rollback_invalid")

    approvals = _exact(
        root["approvals"],
        {
            "owner_readiness_review",
            "independent_readiness_review",
            "reviewers_distinct",
            "assigned_pen_transition_approved",
            "actual_ceremony_authorized",
        },
    )
    expected_approvals = {
        "owner_readiness_review": "approved",
        "independent_readiness_review": "approved",
        "reviewers_distinct": True,
        "assigned_pen_transition_approved": False,
        "actual_ceremony_authorized": False,
    }
    _require(approvals, expected_approvals, "ceremony_approval_invalid")

    authorization = _exact(
        root["authorization"],
        {
            "equipment_mutation_authorized",
            "key_creation_authorized",
            "certificate_creation_authorized",
            "crl_creation_authorized",
            "signing_authorized",
            "runtime_reinstall_authorized",
            "activation_authorized",
            "certification_pass_reachable",
        },
    )
    if any(value is not False for value in authorization.values()):
        raise CeremonyReadinessError("ceremony_authorization_invalid")

    return {
        "authorization": "ceremony_readiness_review_only",
        "certification_status": "BLOCKED",
        "checks": [
            {"name": "two_custodian_model", "status": "READY_FOR_SEPARATE_APPROVAL"},
            {"name": "offline_equipment", "status": "READY_FOR_SEPARATE_APPROVAL"},
            {"name": "transfer_media", "status": "READY_FOR_SEPARATE_APPROVAL"},
            {"name": "backup_and_restore", "status": "READY_FOR_SEPARATE_APPROVAL"},
            {"name": "tpm_capability", "status": "READY_FOR_SEPARATE_APPROVAL"},
            {"name": "ceremony_sequence", "status": "READY_FOR_SEPARATE_APPROVAL"},
            {"name": "revocation_procedure", "status": "READY_FOR_SEPARATE_APPROVAL"},
            {"name": "rollback_and_abort", "status": "READY_FOR_SEPARATE_APPROVAL"},
            {"name": "assigned_pen_transition", "status": "BLOCKED"},
            {"name": "actual_ceremony", "status": "BLOCKED"},
            {"name": "pki_mutation", "status": "BLOCKED"},
            {"name": "certification_pass", "status": "BLOCKED"},
        ],
        "environment": "pilot",
        "exit_code": EXIT_BLOCKED,
        "live_execution_status": "BLOCKED",
        "schema_version": RESULT_SCHEMA,
    }


def evaluate_ceremony_readiness(raw: bytes) -> dict[str, Any]:
    """Return one deterministic redacted result for untrusted proposal bytes."""

    try:
        return validate_ceremony_readiness(parse_ceremony_readiness(raw))
    except CeremonyReadinessError:
        return {
            "authorization": "ceremony_readiness_review_only",
            "certification_status": "FAIL",
            "environment": "pilot",
            "error": "ceremony_readiness_invalid",
            "exit_code": EXIT_FAIL,
            "live_execution_status": "BLOCKED",
            "schema_version": RESULT_SCHEMA,
        }
