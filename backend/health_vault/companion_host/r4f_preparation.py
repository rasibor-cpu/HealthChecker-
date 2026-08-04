"""HC-309-R4F-PREP synthetic policy and plan validation only.

This module has no live, signing, installation, certificate-store, or
ProgramData capability. It validates bounded synthetic JSON received on stdin
and emits a deterministic preparation result that can never certify a runtime.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
from typing import Any


INPUT_SCHEMA = "hc.r4f_preparation_fixture.v1"
RESULT_SCHEMA = "hc.r4f_preparation_result.v1"
MAX_INPUT_BYTES = 131_072
INPUT_DEADLINE_SECONDS = 10.0
MAX_DEPTH = 12
MAX_CONTAINERS = 64
MAX_MEMBERS = 32
MAX_ARRAY_ELEMENTS = 32
MAX_SCALARS = 320
MAX_STRING_LENGTH = 512
MAX_INTEGER_DIGITS = 19
MIN_INTEGER, MAX_INTEGER = -(2**63), 2**63 - 1
EXIT_BLOCKED, EXIT_FAIL, EXIT_INVOCATION = 20, 21, 22
HEX64 = re.compile(r"[0-9a-f]{64}")
SEMVER = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")

CODE_POLICY = "hc-private-pilot-code-signing-v1"
EVIDENCE_POLICY = "hc-private-pilot-evidence-signing-v1"
PACKAGE_SCHEMA = "hc.collector_package_manifest.v1"
EXTERNAL_POLICY_SCHEMA = "hc.collector_external_trust_policy.v1"
EXPECTED_ASSETS = (
    "Invoke-ProtectedRuntimeCollector.ps1",
    "HC_PROTECTED_RUNTIME_ENVELOPE_SCHEMA.json",
    "PILOT_PUBLIC_TRUST.json",
)
EXPECTED_REINSTALL_GATES = (
    "authorization",
    "collector_approval",
    "pilot_chain_validation",
    "backup_and_continuity",
    "installer_acquisition",
    "independent_installer_verification",
    "controlled_installation",
    "installed_runtime_collection",
    "dependency_lock_validation",
    "independent_digest_review",
    "activation_separately_authorized",
)

PENDING_PEN_SCHEMA = "hc.r4f_exec_pending_pen_readiness.v1"
PENDING_PEN_RESULT_SCHEMA = "hc.r4f_exec_pending_pen_result.v1"
_PENDING_PEN_CHECKS = (
    ("operator_decisions", "READY"),
    ("toolchain_evidence", "READY"),
    ("pen_assignment", "BLOCKED"),
    ("oid_materialization", "BLOCKED"),
    ("pki_mutation", "BLOCKED"),
    ("signing", "BLOCKED"),
    ("runtime_reinstall", "BLOCKED"),
    ("certification_pass", "BLOCKED"),
)


def _pending_pen_expected() -> dict[str, Any]:
    """Build one fresh expected record from immutable source values."""

    return {
        "schema_version": PENDING_PEN_SCHEMA,
        "owner": "Robert Asibor",
        "environment": "pilot",
        "production_use": "prohibited",
        "pen": {
            "assignee": "Robert Asibor",
            "status": "pending_assignment",
            "iana_confirmation_status": "confirmed",
            "assigned_pen": None,
            "certificate_policy_oid": None,
            "evidence_eku_oid": None,
            "oid_materialization": "prohibited_until_assigned_pen_independently_verified",
        },
        "custodians": {
            "model": "two_custodian_approved",
            "owner_custodian": "designated",
            "independent_second_custodian": "designated",
            "personal_identities_location": "protected_offline_ceremony_record_outside_git",
        },
        "profiles": {
            "root": {"algorithm": "RSA", "key_size": 4096, "digest": "SHA-256", "validity_years": 15, "is_ca": True, "path_length": 1, "placement": "offline_only"},
            "issuing": {"algorithm": "RSA", "key_size": 3072, "digest": "SHA-256", "validity_years": 5, "is_ca": True, "path_length": 0, "placement": "offline_only"},
            "code_signer": {"algorithm": "RSA", "key_size": 3072, "digest": "SHA-256", "eku": "code_signing_only", "maximum_validity_months": 18, "exportable": False, "key_role": "code_signing"},
            "evidence_signer": {"algorithm": "ECDSA", "curve": "P-256", "digest": "SHA-256", "eku": "private_evidence_signing", "maximum_validity_months": 12, "exportable": False, "key_role": "evidence_signing"},
        },
        "provider_policy": {
            "tpm_provider": "Microsoft Platform Crypto Provider",
            "software_fallback": "prohibited",
            "code_key_access_class": "Local Administrators",
            "evidence_key_access_class": "SYSTEM",
            "private_keys_exist": False,
        },
        "timestamp_policy": {
            "provider": "DigiCert pilot",
            "endpoint": "http://timestamp.digicert.com",
            "endpoint_is_trust_anchor": False,
            "validate_full_returned_chain": True,
        },
        "revocation_policy": {
            "mode": "fail_closed",
            "stale_or_unavailable": "BLOCKED",
            "revoked_invalid_wrong_purpose_or_policy": "FAIL",
            "evidence_freshness_minutes": 10,
            "clock_skew_ceiling_seconds": 120,
        },
        "locations": {
            "trust_policy_root": "C:\\ProgramData\\HealthChecker\\trust\\policy\\v1\\",
            "crl_incoming": "C:\\ProgramData\\HealthChecker\\trust\\crl\\incoming\\",
            "crl_active": "C:\\ProgramData\\HealthChecker\\trust\\crl\\active\\",
            "evidence_retention_root": "C:\\ProgramData\\HealthChecker\\evidence\\protected-runtime\\",
        },
        "retention_and_rollback": {
            "pilot_evidence_retention_years": 2,
            "rollback_authority": "owner",
        },
        "runtime_state": {
            "existing_runtime_action": "retain_do_not_delete_or_overwrite",
            "health_status": "healthy",
            "certification_status": "BLOCKED",
            "certification_pass_reachable": False,
            "active_tasks_binding": "older_immutable_releases",
            "repository_head_is_active_release": False,
        },
        "toolchain_evidence": {
            "evidence_class": "operator_supplied_reviewed",
            "sdk_display_version": "10.1.26100.8876",
            "sdk_publisher": "Microsoft",
            "signtool_path": "C:\\Program Files (x86)\\Windows Kits\\10\\bin\\10.0.26100.0\\x64\\signtool.exe",
            "signtool_authenticode_status": "Valid",
            "signtool_microsoft_signer": True,
            "signtool_sha256": "0f82273275b175dc5e417aeedf96b749d49f71f8cd24c128334bfecb779e1231",
            "installer_source_class": "official_microsoft",
            "installer_authenticode_status": "Valid",
            "installer_sha256": "098d545e516b7e7745e07cb92c879c59bc35621fdc331dc631bc99d2aec5f8ee",
        },
        "authorization": {
            "pki_mutation_authorized": False,
            "key_creation_authorized": False,
            "certificate_creation_authorized": False,
            "signing_authorized": False,
            "runtime_reinstall_authorized": False,
            "certification_pass_reachable": False,
            "live_execution_status": "BLOCKED",
        },
        "mandatory_checks": dict(_PENDING_PEN_CHECKS),
    }


class PreparationError(ValueError):
    """Fixed-code error for untrusted synthetic preparation input."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _exact(value: Any, keys: set[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise PreparationError(code)
    return value


def _yes(value: Any, code: str) -> None:
    if value is not True:
        raise PreparationError(code)


def _no(value: Any, code: str) -> None:
    if value is not False:
        raise PreparationError(code)


def _hash(value: Any, code: str) -> None:
    if type(value) is not str or HEX64.fullmatch(value) is None:
        raise PreparationError(code)


def _integer(text: str) -> int:
    digits = text[1:] if text.startswith("-") else text
    if len(digits) > MAX_INTEGER_DIGITS:
        raise PreparationError("integer_limit_exceeded")
    value = int(text)
    if value < MIN_INTEGER or value > MAX_INTEGER:
        raise PreparationError("integer_range_invalid")
    return value


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PreparationError("duplicate_json_key")
        result[key] = value
    return result


def _validate_structure(root: Any) -> None:
    containers = scalars = 0
    pending: list[tuple[Any, int]] = [(root, 1)]
    while pending:
        value, depth = pending.pop()
        if depth > MAX_DEPTH:
            raise PreparationError("json_depth_exceeded")
        if type(value) is dict:
            containers += 1
            if len(value) > MAX_MEMBERS:
                raise PreparationError("json_members_exceeded")
            for key, child in value.items():
                if len(key) > MAX_STRING_LENGTH:
                    raise PreparationError("json_string_exceeded")
                key.encode("utf-8", errors="strict")
                scalars += 1
                pending.append((child, depth + 1))
        elif type(value) is list:
            containers += 1
            if len(value) > MAX_ARRAY_ELEMENTS:
                raise PreparationError("json_array_exceeded")
            pending.extend((child, depth + 1) for child in value)
        elif type(value) is str:
            if len(value) > MAX_STRING_LENGTH:
                raise PreparationError("json_string_exceeded")
            value.encode("utf-8", errors="strict")
            scalars += 1
        elif type(value) is int:
            if value < MIN_INTEGER or value > MAX_INTEGER:
                raise PreparationError("integer_range_invalid")
            scalars += 1
        elif value is None or type(value) is bool:
            scalars += 1
        else:
            raise PreparationError("json_scalar_invalid")
        if containers > MAX_CONTAINERS:
            raise PreparationError("json_containers_exceeded")
        if scalars > MAX_SCALARS:
            raise PreparationError("json_scalars_exceeded")


def canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    """Canonical bytes for the restricted integer-free synthetic manifest."""
    return json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def classify_certificate(metadata: dict[str, Any], purpose: str) -> str:
    """Classify synthetic public certificate metadata without store access."""

    value = _exact(
        metadata,
        {"status", "revocation", "purpose", "policy", "issuer_allowed", "chain_policy", "root_crl", "issuing_crl"},
        "certificate_metadata_invalid",
    )
    if value["status"] in {"invalid", "expired", "revoked"}:
        return "FAIL"
    if value["status"] != "valid":
        return "BLOCKED"
    if value["root_crl"] in {"issuing_ca_revoked", "invalid_signature", "wrong_policy"}:
        return "FAIL"
    if value["issuing_crl"] in {"leaf_revoked", "invalid_signature", "wrong_policy"}:
        return "FAIL"
    if value["root_crl"] in {"absent", "stale", "unavailable"} or value["issuing_crl"] in {"absent", "stale", "unavailable"}:
        return "BLOCKED"
    if value["revocation"] == "indeterminate":
        return "BLOCKED"
    expected_policy = CODE_POLICY if purpose == "code" else EVIDENCE_POLICY
    if (
        value["revocation"] != "valid"
        or value["purpose"] != purpose
        or value["policy"] != expected_policy
        or value["issuer_allowed"] is not True
        or value["chain_policy"] != "hc-private-pilot-chain-v1"
        or value["root_crl"] != "valid"
        or value["issuing_crl"] != "valid"
    ):
        return "FAIL"
    return "VALID_FOR_PREPARATION"


def _validate_pki(value: Any) -> str:
    pki = _exact(
        value,
        {"environment", "authorization", "root", "issuing_ca", "code_signing", "evidence_signing", "revocation_policy", "tpm_support"},
        "pki_policy_invalid",
    )
    if pki["environment"] != "synthetic" or pki["authorization"] != "preparation_only":
        raise PreparationError("preparation_boundary_invalid")
    root = _exact(
        pki["root"],
        {"algorithm", "key_size", "digest", "offline", "private_key_on_runtime_host", "private_key_placement", "is_ca", "path_length", "validity_years"},
        "root_profile_invalid",
    )
    if any(type(root[field]) is not int for field in ("key_size", "path_length", "validity_years")):
        raise PreparationError("root_profile_invalid")
    if (root["algorithm"], root["key_size"], root["digest"], root["path_length"], root["validity_years"], root["private_key_placement"]) != ("RSA", 4096, "SHA-256", 1, 15, "offline_authority"):
        raise PreparationError("root_profile_invalid")
    _yes(root["offline"], "root_must_be_offline")
    _no(root["private_key_on_runtime_host"], "root_key_on_runtime_host")
    _yes(root["is_ca"], "root_constraints_invalid")
    issuing = _exact(
        pki["issuing_ca"],
        {"required", "algorithm", "key_size", "digest", "is_ca", "path_length", "validity_years", "policies", "private_key_placement"},
        "issuing_profile_invalid",
    )
    if any(type(issuing[field]) is not int for field in ("key_size", "path_length", "validity_years")):
        raise PreparationError("issuing_profile_invalid")
    if (
        issuing["required"] is not True
        or (issuing["algorithm"], issuing["key_size"], issuing["digest"]) != ("RSA", 3072, "SHA-256")
        or issuing["is_ca"] is not True
        or issuing["path_length"] != 0
        or issuing["validity_years"] != 5
        or issuing["policies"] != [CODE_POLICY, EVIDENCE_POLICY]
    ):
        raise PreparationError("issuing_profile_invalid")
    if issuing["private_key_placement"] != "isolated_ca_system":
        raise PreparationError("issuing_key_placement_invalid")
    code = _exact(
        pki["code_signing"],
        {"algorithm", "key_size", "digest", "ekus", "policy", "exportable", "store", "provider", "key_id", "private_key_placement", "is_ca", "basic_constraints", "key_usage"},
        "code_signing_profile_invalid",
    )
    if type(code["key_size"]) is not int:
        raise PreparationError("code_signing_profile_invalid")
    evidence = _exact(
        pki["evidence_signing"],
        {"algorithm", "curve", "digest", "ekus", "policy", "exportable", "store", "provider", "key_id", "private_key_placement", "is_ca", "basic_constraints", "key_usage"},
        "evidence_signing_profile_invalid",
    )
    if (
        (code["algorithm"], code["key_size"], code["digest"]) != ("RSA", 3072, "SHA-256")
        or code["ekus"] != ["code_signing"]
        or code["policy"] != CODE_POLICY
    ):
        raise PreparationError("code_signing_profile_invalid")
    if code["private_key_placement"] != "signing_station_service":
        raise PreparationError("code_key_placement_invalid")
    if (
        (evidence["algorithm"], evidence["curve"], evidence["digest"]) != ("ECDSA", "P-256", "SHA-256")
        or evidence["ekus"] != ["private_evidence_signing"]
        or evidence["policy"] != EVIDENCE_POLICY
    ):
        raise PreparationError("evidence_signing_profile_invalid")
    if evidence["private_key_placement"] != "runtime_host_collector_only":
        raise PreparationError("evidence_key_placement_invalid")
    expected_usage = {
        "digital_signature": True,
        "certificate_signing": False,
        "crl_signing": False,
        "key_encipherment": False,
        "data_encipherment": False,
        "key_agreement": False,
        "content_commitment": False,
    }
    for leaf, error in (
        (code, "code_signing_profile_invalid"),
        (evidence, "evidence_signing_profile_invalid"),
    ):
        constraints = _exact(leaf["basic_constraints"], {"present", "critical"}, error)
        usage = _exact(leaf["key_usage"], set(expected_usage), error)
        if leaf["is_ca"] is not False or constraints != {"present": True, "critical": True}:
            raise PreparationError(error)
        if any(type(value) is not bool for value in usage.values()) or usage != expected_usage:
            raise PreparationError(error)
    if code["exportable"] is not False or evidence["exportable"] is not False:
        raise PreparationError("exportable_key_forbidden")
    if code["store"] != "LocalMachine" or evidence["store"] != "LocalMachine":
        raise PreparationError("certificate_store_invalid")
    if code["provider"] != "tpm_cng" or evidence["provider"] != "tpm_cng":
        raise PreparationError("software_key_fallback_forbidden")
    if code["key_id"] == evidence["key_id"]:
        raise PreparationError("key_reuse_forbidden")
    revocation = _exact(
        pki["revocation_policy"],
        {"chain_policy", "root_crl", "issuing_crl", "unavailable_result", "invalid_result"},
        "revocation_policy_invalid",
    )
    if (
        type(revocation["root_crl"]) is not dict
        or type(revocation["issuing_crl"]) is not dict
        or type(revocation["root_crl"].get("max_validity_days")) is not int
        or type(revocation["issuing_crl"].get("max_validity_days")) is not int
    ):
        raise PreparationError("revocation_policy_invalid")
    if (
        revocation["chain_policy"] != "hc-private-pilot-chain-v1"
        or revocation["root_crl"] != {"required": True, "max_validity_days": 90, "offline_transfer": True, "independent_verification": True}
        or revocation["issuing_crl"] != {"required": True, "max_validity_days": 7, "independent_verification": True}
        or revocation["unavailable_result"] != "BLOCKED"
        or revocation["invalid_result"] != "FAIL"
    ):
        raise PreparationError("revocation_policy_invalid")
    if pki["tpm_support"] not in {"AVAILABLE", "BLOCKED"}:
        raise PreparationError("tpm_status_invalid")
    return "BLOCKED" if pki["tpm_support"] == "BLOCKED" else "READY_FOR_PROVISIONING_REVIEW"


def _validate_external_policy(value: Any) -> dict[str, Any]:
    policy = _exact(
        value,
        {"schema_version", "policy_version", "package_schema_version", "permitted_version", "minimum_version", "canonical_manifest_sha256", "manifest_signer_policy", "certificate_policies", "signature_algorithm", "installed_outside_package", "independently_reviewed", "lifecycle"},
        "external_trust_policy_invalid",
    )
    if policy["schema_version"] != EXTERNAL_POLICY_SCHEMA or type(policy["policy_version"]) is not int or policy["policy_version"] != 1 or policy["package_schema_version"] != PACKAGE_SCHEMA:
        raise PreparationError("external_trust_policy_invalid")
    _hash(policy["canonical_manifest_sha256"], "external_manifest_hash_invalid")
    if policy["manifest_signer_policy"] != CODE_POLICY or policy["certificate_policies"] != [CODE_POLICY] or policy["signature_algorithm"] != "CMS-PKCS7-RSA3072-SHA256":
        raise PreparationError("external_signer_policy_invalid")
    _yes(policy["installed_outside_package"], "external_policy_location_invalid")
    _yes(policy["independently_reviewed"], "external_policy_review_missing")
    if policy["lifecycle"] != "repository_review_and_separate_installation_approval":
        raise PreparationError("external_policy_lifecycle_invalid")
    return policy


def _validate_package(manifest_value: Any, signature_value: Any, external: dict[str, Any]) -> str:
    package = _exact(
        manifest_value,
        {"schema_version", "version", "minimum_version", "target_class", "staging_class", "canonicalization", "assets", "collector_sha256", "collector_signed", "collector_signer_policy", "reparse_path", "mutable", "atomic_activation", "rollback_pointer", "independent_review"},
        "package_manifest_invalid",
    )
    if package["schema_version"] != PACKAGE_SCHEMA or package["canonicalization"] != "RFC8785":
        raise PreparationError("manifest_canonicalization_invalid")
    for field in ("version", "minimum_version"):
        if type(package[field]) is not str or SEMVER.fullmatch(package[field]) is None:
            raise PreparationError("collector_version_invalid")
    version = tuple(map(int, package["version"].split(".")))
    minimum = tuple(map(int, package["minimum_version"].split(".")))
    if version < minimum:
        raise PreparationError("collector_downgrade_forbidden")
    if package["version"] != external["permitted_version"] or package["minimum_version"] != external["minimum_version"]:
        raise PreparationError("external_policy_version_mismatch")
    if package["target_class"] != "immutable_programdata_versioned" or package["staging_class"] != "programdata_sibling_staging":
        raise PreparationError("package_target_invalid")
    if package["reparse_path"] is not False:
        raise PreparationError("reparse_path_forbidden")
    if package["mutable"] is not False:
        raise PreparationError("mutable_package_forbidden")
    assets = package["assets"]
    if type(assets) is not list or len(assets) != len(EXPECTED_ASSETS):
        raise PreparationError("package_asset_set_invalid")
    found: dict[str, str] = {}
    for asset in assets:
        item = _exact(asset, {"name", "sha256"}, "package_asset_invalid")
        _hash(item["sha256"], "package_asset_hash_invalid")
        if item["name"] in found:
            raise PreparationError("package_asset_set_invalid")
        found[item["name"]] = item["sha256"]
    if set(found) != set(EXPECTED_ASSETS):
        raise PreparationError("package_asset_set_invalid")
    _hash(package["collector_sha256"], "collector_hash_invalid")
    if found[EXPECTED_ASSETS[0]] != package["collector_sha256"]:
        raise PreparationError("collector_hash_mismatch")
    if package["collector_signed"] is not True or package["collector_signer_policy"] != CODE_POLICY:
        raise PreparationError("collector_signature_invalid")
    for field, code in (("atomic_activation", "atomic_activation_missing"), ("rollback_pointer", "rollback_pointer_missing"), ("independent_review", "independent_review_missing")):
        _yes(package[field], code)
    canonical = canonical_manifest_bytes(package)
    if hashlib.sha256(canonical).hexdigest() != external["canonical_manifest_sha256"]:
        raise PreparationError("external_manifest_hash_mismatch")
    signature = _exact(signature_value, {"format", "detached", "covers", "signer_policy", "certificate_policy", "signature_algorithm", "status"}, "detached_signature_invalid")
    expected_signature = {"format": "CMS-PKCS7", "detached": True, "covers": "exact_rfc8785_manifest_bytes", "signer_policy": CODE_POLICY, "certificate_policy": CODE_POLICY, "signature_algorithm": "CMS-PKCS7-RSA3072-SHA256", "status": "synthetic_valid"}
    if signature != expected_signature:
        raise PreparationError("detached_signature_invalid")
    if signature["signer_policy"] != external["manifest_signer_policy"] or signature["certificate_policy"] not in external["certificate_policies"] or signature["signature_algorithm"] != external["signature_algorithm"]:
        raise PreparationError("external_signer_policy_mismatch")
    return "READY_FOR_PACKAGE_REVIEW"


def _validate_reinstall(value: Any) -> str:
    plan = _exact(
        value,
        {
            "version", "architecture", "installer_filename", "source", "publisher", "digest_algorithm",
            "installer_sha256", "authenticode", "timestamp", "revocation", "target_class", "add_to_path",
            "user_profile", "launcher", "file_associations", "current_runtime_action", "delete_current_runtime",
            "collector_approved", "independent_acquisition_review", "rollback_plan", "digest_adoption",
            "activation", "official_digest_authenticated", "gate_order", "rollback_on_failure",
        },
        "reinstall_plan_invalid",
    )
    if (plan["version"], plan["architecture"], plan["installer_filename"]) != (
        "3.12.10", "AMD64", "python-3.12.10-amd64.exe"
    ):
        raise PreparationError("installer_identity_invalid")
    if plan["source"] != "python.org" or plan["publisher"] != "Python Software Foundation":
        raise PreparationError("installer_provenance_invalid")
    if plan["digest_algorithm"] != "SHA-256":
        raise PreparationError("installer_digest_algorithm_invalid")
    _hash(plan["installer_sha256"], "installer_digest_invalid")
    _yes(plan["official_digest_authenticated"], "official_digest_provenance_missing")
    if plan["authenticode"] != "valid" or plan["timestamp"] != "valid":
        raise PreparationError("installer_signature_invalid")
    if plan["revocation"] == "indeterminate":
        return "BLOCKED"
    if plan["revocation"] != "valid":
        raise PreparationError("installer_revocation_invalid")
    if plan["target_class"] != "fixed_programdata_versioned":
        raise PreparationError("installation_target_invalid")
    for field in ("add_to_path", "user_profile", "launcher", "file_associations", "delete_current_runtime", "digest_adoption", "activation"):
        _no(plan[field], "unauthorized_installation_action")
    if plan["current_runtime_action"] != "retain_inactive":
        raise PreparationError("current_runtime_disposition_invalid")
    for field, code in (
        ("collector_approved", "collector_approval_missing"),
        ("independent_acquisition_review", "independent_review_missing"),
        ("rollback_plan", "rollback_plan_missing"),
    ):
        _yes(plan[field], code)
    if plan["gate_order"] != list(EXPECTED_REINSTALL_GATES):
        raise PreparationError("acceptance_gate_order_invalid")
    _yes(plan["rollback_on_failure"], "rollback_decision_invalid")
    return "READY_FOR_REINSTALL_REVIEW"


def _require_pending_pen_shape(value: Any, expected: Any) -> None:
    """Require the exact bounded readiness shape without accepting bools as ints."""

    if type(value) is not type(expected):
        raise PreparationError("pending_pen_schema_invalid")
    if type(expected) is dict:
        if set(value) != set(expected):
            raise PreparationError("pending_pen_schema_invalid")
        for key in expected:
            _require_pending_pen_shape(value[key], expected[key])
    elif type(expected) is list:
        if len(value) != len(expected):
            raise PreparationError("pending_pen_schema_invalid")
        for item, expected_item in zip(value, expected):
            _require_pending_pen_shape(item, expected_item)


def parse_pending_pen_readiness(raw: bytes) -> dict[str, Any]:
    """Parse one bounded repository readiness record without host inspection."""

    if type(raw) is not bytes or len(raw) > MAX_INPUT_BYTES:
        raise PreparationError("pending_pen_input_invalid")
    try:
        value = json.loads(
            raw.decode("utf-8-sig", errors="strict"),
            object_pairs_hook=_object,
            parse_int=_integer,
            parse_float=lambda _value: (_ for _ in ()).throw(PreparationError("json_float_invalid")),
            parse_constant=lambda _value: (_ for _ in ()).throw(PreparationError("json_constant_invalid")),
        )
        _validate_structure(value)
    except (UnicodeError, json.JSONDecodeError, RecursionError, MemoryError) as error:
        raise PreparationError("pending_pen_input_invalid") from None
    if type(value) is not dict:
        raise PreparationError("pending_pen_schema_invalid")
    return value


def validate_pending_pen_readiness(value: Any) -> dict[str, Any]:
    """Evaluate the pending-PEN record; PASS and mutation are unreachable."""

    expected = _pending_pen_expected()
    _require_pending_pen_shape(value, expected)
    if value["schema_version"] != PENDING_PEN_SCHEMA:
        raise PreparationError("pending_pen_schema_invalid")
    section_names = (
        "owner", "environment", "production_use", "custodians", "profiles",
        "provider_policy", "timestamp_policy", "revocation_policy", "locations",
        "retention_and_rollback", "runtime_state",
    )
    decisions_match = all(value[name] == expected[name] for name in section_names)
    pen_match = value["pen"] == expected["pen"]
    toolchain_match = value["toolchain_evidence"] == expected["toolchain_evidence"]
    authorization_match = value["authorization"] == expected["authorization"]
    registry_match = value["mandatory_checks"] == dict(_PENDING_PEN_CHECKS)
    checks = (
        ("operator_decisions", "READY" if decisions_match else "FAIL"),
        ("toolchain_evidence", "READY" if toolchain_match else "FAIL"),
        ("pen_assignment", "BLOCKED" if pen_match else "FAIL"),
        ("oid_materialization", "BLOCKED" if pen_match else "FAIL"),
        ("pki_mutation", "BLOCKED" if authorization_match else "FAIL"),
        ("signing", "BLOCKED" if authorization_match else "FAIL"),
        ("runtime_reinstall", "BLOCKED" if authorization_match else "FAIL"),
        ("certification_pass", "BLOCKED" if authorization_match else "FAIL"),
        ("mandatory_check_registry", "READY" if registry_match else "FAIL"),
    )
    status = "FAIL" if any(check_status == "FAIL" for _, check_status in checks) else "BLOCKED"
    return {
        "authorization": "readiness_only",
        "certification_status": status,
        "checks": [{"name": name, "status": check_status} for name, check_status in checks],
        "environment": "pilot",
        "exit_code": EXIT_FAIL if status == "FAIL" else EXIT_BLOCKED,
        "live_execution_status": "BLOCKED",
        "schema_version": PENDING_PEN_RESULT_SCHEMA,
    }


def evaluate_pending_pen_readiness(raw: bytes) -> dict[str, Any]:
    """Return one fixed readiness result for bounded bytes; never raise details."""

    try:
        return validate_pending_pen_readiness(parse_pending_pen_readiness(raw))
    except PreparationError:
        return {
            "authorization": "readiness_only",
            "certification_status": "FAIL",
            "environment": "pilot",
            "error": "readiness_configuration_invalid",
            "exit_code": EXIT_INVOCATION,
            "live_execution_status": "BLOCKED",
            "schema_version": PENDING_PEN_RESULT_SCHEMA,
        }


def validate_preparation(value: Any) -> dict[str, Any]:
    fixture = _exact(
        value,
        {"schema_version", "pki_policy", "external_trust_policy", "package_manifest", "detached_manifest_signature", "reinstall_plan"},
        "fixture_invalid",
    )
    if fixture["schema_version"] != INPUT_SCHEMA:
        raise PreparationError("fixture_schema_invalid")
    external = _validate_external_policy(fixture["external_trust_policy"])
    checks = (
        ("pki_policy", _validate_pki(fixture["pki_policy"])),
        ("collector_package", _validate_package(fixture["package_manifest"], fixture["detached_manifest_signature"], external)),
        ("reinstall_plan", _validate_reinstall(fixture["reinstall_plan"])),
    )
    return {
        "authorization": "preparation_only",
        "certification_status": "BLOCKED",
        "checks": [{"name": name, "status": status} for name, status in checks],
        "environment": "synthetic",
        "exit_code": EXIT_BLOCKED,
        "schema_version": RESULT_SCHEMA,
    }


def _error() -> dict[str, Any]:
    return {
        "authorization": "preparation_only",
        "certification_status": "FAIL",
        "environment": "synthetic",
        "error": "preparation_fixture_invalid",
        "exit_code": EXIT_INVOCATION,
        "schema_version": RESULT_SCHEMA,
    }


def _write_result(result: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _read_bounded_with_deadline() -> bytes:
    completed = threading.Event()
    state: dict[str, Any] = {}

    def reader() -> None:
        try:
            state["raw"] = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        except (OSError, ValueError):
            state["error"] = True
        finally:
            completed.set()

    threading.Thread(target=reader, name="hc-r4f-prep-stdin", daemon=True).start()
    if not completed.wait(INPUT_DEADLINE_SECONDS):
        _write_result(_error())
        os._exit(EXIT_INVOCATION)
    if state.get("error") is True or "raw" not in state:
        raise PreparationError("stdin_read_invalid")
    return state["raw"]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        if args:
            raise PreparationError("arguments_forbidden")
        raw = _read_bounded_with_deadline()
        if len(raw) > MAX_INPUT_BYTES:
            raise PreparationError("fixture_too_large")
        text = raw.decode("utf-8-sig", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_object,
            parse_int=_integer,
            parse_float=lambda _value: (_ for _ in ()).throw(PreparationError("json_float_invalid")),
            parse_constant=lambda _value: (_ for _ in ()).throw(PreparationError("json_constant_invalid")),
        )
        _validate_structure(value)
        result = validate_preparation(value)
        code = EXIT_BLOCKED
    except (PreparationError, UnicodeError, json.JSONDecodeError, RecursionError, MemoryError):
        result, code = _error(), EXIT_INVOCATION
    _write_result(result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
