from __future__ import annotations

import ast
import copy
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time
import winreg

import pytest

from backend.health_vault.companion_host.r4f_preparation import (
    CODE_POLICY,
    EVIDENCE_POLICY,
    EXIT_BLOCKED,
    EXIT_INVOCATION,
    EXPECTED_REINSTALL_GATES,
    INPUT_DEADLINE_SECONDS,
    MAX_ARRAY_ELEMENTS,
    MAX_CONTAINERS,
    MAX_DEPTH,
    MAX_INTEGER_DIGITS,
    MAX_MEMBERS,
    MAX_SCALARS,
    MAX_STRING_LENGTH,
    PreparationError,
    _integer,
    _validate_structure,
    canonical_manifest_bytes,
    classify_certificate,
    validate_preparation,
)


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "backend/health_vault/companion_host/r4f_preparation.py"


def _fixture() -> dict:
    h = "a" * 64
    fixture = {
        "schema_version": "hc.r4f_preparation_fixture.v1",
        "pki_policy": {
            "environment": "synthetic",
            "authorization": "preparation_only",
            "root": {
                "algorithm": "RSA", "key_size": 4096, "digest": "SHA-256", "offline": True,
                "private_key_on_runtime_host": False, "is_ca": True, "path_length": 1, "validity_years": 15,
                "private_key_placement": "offline_authority",
            },
            "issuing_ca": {
                "required": True, "algorithm": "RSA", "key_size": 3072, "digest": "SHA-256",
                "is_ca": True, "path_length": 0, "validity_years": 5,
                "policies": [CODE_POLICY, EVIDENCE_POLICY],
                "private_key_placement": "isolated_ca_system",
            },
            "code_signing": {
                "algorithm": "RSA", "key_size": 3072, "digest": "SHA-256", "ekus": ["code_signing"],
                "policy": CODE_POLICY, "exportable": False, "store": "LocalMachine",
                "provider": "tpm_cng", "key_id": "synthetic-code-key", "private_key_placement": "signing_station_service",
                "is_ca": False, "basic_constraints": {"present": True, "critical": True},
                "key_usage": {"digital_signature": True, "certificate_signing": False, "crl_signing": False,
                              "key_encipherment": False, "data_encipherment": False, "key_agreement": False,
                              "content_commitment": False},
            },
            "evidence_signing": {
                "algorithm": "ECDSA", "curve": "P-256", "digest": "SHA-256",
                "ekus": ["private_evidence_signing"], "policy": EVIDENCE_POLICY,
                "exportable": False, "store": "LocalMachine", "provider": "tpm_cng",
                "key_id": "synthetic-evidence-key", "private_key_placement": "runtime_host_collector_only",
                "is_ca": False, "basic_constraints": {"present": True, "critical": True},
                "key_usage": {"digital_signature": True, "certificate_signing": False, "crl_signing": False,
                              "key_encipherment": False, "data_encipherment": False, "key_agreement": False,
                              "content_commitment": False},
            },
            "revocation_policy": {
                "chain_policy": "hc-private-pilot-chain-v1",
                "root_crl": {"required": True, "max_validity_days": 90, "offline_transfer": True, "independent_verification": True},
                "issuing_crl": {"required": True, "max_validity_days": 7, "independent_verification": True},
                "unavailable_result": "BLOCKED", "invalid_result": "FAIL",
            },
            "tpm_support": "AVAILABLE",
        },
        "external_trust_policy": {
            "schema_version": "hc.collector_external_trust_policy.v1", "policy_version": 1,
            "package_schema_version": "hc.collector_package_manifest.v1",
            "permitted_version": "1.0.0", "minimum_version": "1.0.0",
            "canonical_manifest_sha256": "0" * 64, "manifest_signer_policy": CODE_POLICY,
            "certificate_policies": [CODE_POLICY], "signature_algorithm": "CMS-PKCS7-RSA3072-SHA256",
            "installed_outside_package": True, "independently_reviewed": True,
            "lifecycle": "repository_review_and_separate_installation_approval",
        },
        "package_manifest": {
            "schema_version": "hc.collector_package_manifest.v1",
            "version": "1.0.0", "minimum_version": "1.0.0",
            "target_class": "immutable_programdata_versioned",
            "staging_class": "programdata_sibling_staging", "canonicalization": "RFC8785",
            "assets": [
                {"name": "Invoke-ProtectedRuntimeCollector.ps1", "sha256": h},
                {"name": "HC_PROTECTED_RUNTIME_ENVELOPE_SCHEMA.json", "sha256": "c" * 64},
                {"name": "PILOT_PUBLIC_TRUST.json", "sha256": "d" * 64},
            ],
            "collector_sha256": h, "collector_signed": True, "collector_signer_policy": CODE_POLICY,
            "reparse_path": False, "mutable": False, "atomic_activation": True,
            "rollback_pointer": True, "independent_review": True,
        },
        "detached_manifest_signature": {
            "format": "CMS-PKCS7", "detached": True, "covers": "exact_rfc8785_manifest_bytes",
            "signer_policy": CODE_POLICY, "certificate_policy": CODE_POLICY,
            "signature_algorithm": "CMS-PKCS7-RSA3072-SHA256", "status": "synthetic_valid",
        },
        "reinstall_plan": {
            "version": "3.12.10", "architecture": "AMD64",
            "installer_filename": "python-3.12.10-amd64.exe", "source": "python.org",
            "publisher": "Python Software Foundation", "digest_algorithm": "SHA-256",
            "installer_sha256": "e" * 64, "authenticode": "valid", "timestamp": "valid",
            "official_digest_authenticated": True,
            "revocation": "valid", "target_class": "fixed_programdata_versioned",
            "add_to_path": False, "user_profile": False, "launcher": False,
            "file_associations": False, "current_runtime_action": "retain_inactive",
            "delete_current_runtime": False, "collector_approved": True,
            "independent_acquisition_review": True, "rollback_plan": True,
            "digest_adoption": False, "activation": False,
            "gate_order": list(EXPECTED_REINSTALL_GATES), "rollback_on_failure": True,
        },
    }
    fixture["external_trust_policy"]["canonical_manifest_sha256"] = hashlib.sha256(
        canonical_manifest_bytes(fixture["package_manifest"])
    ).hexdigest()
    return fixture


def _run(value: object | None = None, *args: str) -> subprocess.CompletedProcess[bytes]:
    payload = json.dumps(_fixture() if value is None else value, separators=(",", ":")).encode()
    return subprocess.run(
        [sys.executable, "-B", "-m", "backend.health_vault.companion_host.r4f_preparation", *args],
        cwd=ROOT, input=payload, capture_output=True, timeout=15, check=False,
    )


def test_valid_policy_is_deterministic_preparation_only_blocked():
    first, second = _run(), _run()
    assert first.returncode == second.returncode == EXIT_BLOCKED
    assert first.stderr == second.stderr == b"" and first.stdout == second.stdout
    result = json.loads(first.stdout)
    assert result == validate_preparation(_fixture())
    assert result["environment"] == "synthetic"
    assert result["authorization"] == "preparation_only"
    assert result["certification_status"] == "BLOCKED"
    assert b'"PASS"' not in first.stdout and b'"exit_code":0' not in first.stdout


@pytest.mark.parametrize(
    ("path", "value", "code"),
    [
        (("pki_policy", "code_signing", "key_id"), "synthetic-evidence-key", "key_reuse_forbidden"),
        (("pki_policy", "code_signing", "exportable"), True, "exportable_key_forbidden"),
        (("pki_policy", "evidence_signing", "exportable"), True, "exportable_key_forbidden"),
        (("pki_policy", "code_signing", "key_size"), 2048, "code_signing_profile_invalid"),
        (("pki_policy", "evidence_signing", "curve"), "P-384", "evidence_signing_profile_invalid"),
        (("pki_policy", "code_signing", "ekus"), ["code_signing", "server_auth"], "code_signing_profile_invalid"),
        (("pki_policy", "root", "private_key_on_runtime_host"), True, "root_key_on_runtime_host"),
        (("pki_policy", "code_signing", "store"), "CurrentUser", "certificate_store_invalid"),
        (("package_manifest", "reparse_path"), True, "reparse_path_forbidden"),
        (("package_manifest", "mutable"), True, "mutable_package_forbidden"),
        (("package_manifest", "version"), "0.9.0", "collector_downgrade_forbidden"),
        (("package_manifest", "collector_signed"), False, "collector_signature_invalid"),
        (("package_manifest", "collector_signer_policy"), "wrong", "collector_signature_invalid"),
        (("reinstall_plan", "digest_algorithm"), "MD5", "installer_digest_algorithm_invalid"),
        (("reinstall_plan", "official_digest_authenticated"), False, "official_digest_provenance_missing"),
        (("reinstall_plan", "publisher"), "wrong", "installer_provenance_invalid"),
        (("reinstall_plan", "version"), "3.12.9", "installer_identity_invalid"),
        (("reinstall_plan", "architecture"), "x86", "installer_identity_invalid"),
        (("reinstall_plan", "user_profile"), True, "unauthorized_installation_action"),
        (("reinstall_plan", "add_to_path"), True, "unauthorized_installation_action"),
        (("reinstall_plan", "delete_current_runtime"), True, "unauthorized_installation_action"),
        (("reinstall_plan", "independent_acquisition_review"), False, "independent_review_missing"),
        (("reinstall_plan", "digest_adoption"), True, "unauthorized_installation_action"),
        (("reinstall_plan", "rollback_plan"), False, "rollback_plan_missing"),
        (("reinstall_plan", "rollback_on_failure"), False, "rollback_decision_invalid"),
    ],
)
def test_security_policy_mutations_are_rejected(path: tuple[str, ...], value: object, code: str):
    fixture = _fixture(); target = fixture
    for part in path[:-1]: target = target[part]
    target[path[-1]] = value
    with pytest.raises(PreparationError, match=code): validate_preparation(fixture)


def test_missing_or_extra_package_assets_and_hash_mismatch_are_rejected():
    variants = []
    value = _fixture(); value["package_manifest"]["assets"].pop(); variants.append((value, "package_asset_set_invalid"))
    value = _fixture(); value["package_manifest"]["assets"].append({"name": "extra", "sha256": "f" * 64}); variants.append((value, "package_asset_set_invalid"))
    value = _fixture(); value["package_manifest"]["collector_sha256"] = "f" * 64; variants.append((value, "collector_hash_mismatch"))
    for value, code in variants:
        with pytest.raises(PreparationError, match=code): validate_preparation(value)


def test_reinstall_acceptance_gate_order_is_exact():
    fixture = _fixture(); fixture["reinstall_plan"]["gate_order"][5:7] = reversed(fixture["reinstall_plan"]["gate_order"][5:7])
    with pytest.raises(PreparationError, match="acceptance_gate_order_invalid"):
        validate_preparation(fixture)


def test_manifest_canonicalization_digest_round_trip_has_no_self_reference():
    fixture = _fixture(); manifest = fixture["package_manifest"]
    assert "PACKAGE_MANIFEST.json" not in {asset["name"] for asset in manifest["assets"]}
    first = canonical_manifest_bytes(manifest)
    reparsed = json.loads(first)
    second = canonical_manifest_bytes(reparsed)
    assert first == second
    assert hashlib.sha256(second).hexdigest() == fixture["external_trust_policy"]["canonical_manifest_sha256"]


def test_replaced_package_cannot_replace_external_trust():
    fixture = _fixture(); unchanged_external = copy.deepcopy(fixture["external_trust_policy"])
    fixture["package_manifest"]["assets"][0]["sha256"] = "f" * 64
    fixture["package_manifest"]["collector_sha256"] = "f" * 64
    fixture["detached_manifest_signature"]["signer_policy"] = "attacker-policy"
    fixture["external_trust_policy"] = unchanged_external
    with pytest.raises(PreparationError, match="external_manifest_hash_mismatch"):
        validate_preparation(fixture)


@pytest.mark.parametrize(
    ("path", "value", "code"),
    [
        (("external_trust_policy", "permitted_version"), "2.0.0", "external_policy_version_mismatch"),
        (("external_trust_policy", "canonical_manifest_sha256"), "f" * 64, "external_manifest_hash_mismatch"),
        (("external_trust_policy", "installed_outside_package"), False, "external_policy_location_invalid"),
        (("pki_policy", "issuing_ca", "private_key_placement"), "runtime_host", "issuing_key_placement_invalid"),
        (("pki_policy", "code_signing", "private_key_placement"), "runtime_host", "code_key_placement_invalid"),
        (("pki_policy", "evidence_signing", "private_key_placement"), "signing_station_service", "evidence_key_placement_invalid"),
        (("pki_policy", "code_signing", "provider"), "software_cng", "software_key_fallback_forbidden"),
        (("pki_policy", "evidence_signing", "ekus"), ["code_signing"], "evidence_signing_profile_invalid"),
        (("pki_policy", "evidence_signing", "ekus"), ["private_evidence_signing", "client_auth"], "evidence_signing_profile_invalid"),
        (("package_manifest", "target_class"), "user_profile", "package_target_invalid"),
        (("reinstall_plan", "source"), "mirror.invalid", "installer_provenance_invalid"),
        (("reinstall_plan", "installer_filename"), "python.exe", "installer_identity_invalid"),
    ],
)
def test_r1_independent_rejection_coverage(path: tuple[str, ...], value: object, code: str):
    fixture = _fixture(); target = fixture
    for part in path[:-1]: target = target[part]
    target[path[-1]] = value
    with pytest.raises(PreparationError, match=code):
        validate_preparation(fixture)


@pytest.mark.parametrize(
    ("root_crl", "issuing_crl", "expected"),
    [
        ("issuing_ca_revoked", "valid", "FAIL"),
        ("stale", "valid", "BLOCKED"),
        ("absent", "valid", "BLOCKED"),
        ("invalid_signature", "valid", "FAIL"),
        ("valid", "stale", "BLOCKED"),
        ("valid", "leaf_revoked", "FAIL"),
    ],
)
def test_two_level_crl_verdicts(root_crl: str, issuing_crl: str, expected: str):
    metadata = {
        "status": "valid", "revocation": "valid", "purpose": "code", "policy": CODE_POLICY,
        "issuer_allowed": True, "chain_policy": "hc-private-pilot-chain-v1",
        "root_crl": root_crl, "issuing_crl": issuing_crl,
    }
    assert classify_certificate(metadata, "code") == expected


def test_missing_chain_policy_representation_is_rejected():
    metadata = {
        "status": "valid", "revocation": "valid", "purpose": "code", "policy": CODE_POLICY,
        "issuer_allowed": True, "root_crl": "valid", "issuing_crl": "valid",
    }
    with pytest.raises(PreparationError, match="certificate_metadata_invalid"):
        classify_certificate(metadata, "code")


def _nested(depth: int) -> object:
    value: object = "x"
    for _ in range(depth - 1): value = [value]
    return value


def test_structural_limits_at_boundary_and_one_over():
    _validate_structure(_nested(MAX_DEPTH))
    with pytest.raises(PreparationError, match="json_depth_exceeded"): _validate_structure(_nested(MAX_DEPTH + 1))
    _validate_structure({str(index): 0 for index in range(MAX_MEMBERS)})
    with pytest.raises(PreparationError, match="json_members_exceeded"): _validate_structure({str(index): 0 for index in range(MAX_MEMBERS + 1)})
    _validate_structure([0] * MAX_ARRAY_ELEMENTS)
    with pytest.raises(PreparationError, match="json_array_exceeded"): _validate_structure([0] * (MAX_ARRAY_ELEMENTS + 1))
    at_containers = [[{}] for _ in range(31)] + [{}]
    assert 1 + 31 + 32 == MAX_CONTAINERS; _validate_structure(at_containers)
    with pytest.raises(PreparationError, match="json_containers_exceeded"): _validate_structure([[{}] for _ in range(32)])
    at_scalars = [["x"] * 10 for _ in range(32)]
    assert 32 * 10 == MAX_SCALARS; _validate_structure(at_scalars)
    at_scalars[-1].append("x")
    with pytest.raises(PreparationError, match="json_scalars_exceeded"): _validate_structure(at_scalars)
    _validate_structure("x" * MAX_STRING_LENGTH)
    with pytest.raises(PreparationError, match="json_string_exceeded"): _validate_structure("x" * (MAX_STRING_LENGTH + 1))
    assert _integer("8" * MAX_INTEGER_DIGITS) == int("8" * MAX_INTEGER_DIGITS)
    with pytest.raises(PreparationError, match="integer_limit_exceeded"): _integer("9" * (MAX_INTEGER_DIGITS + 1))
    assert _integer(str(2**63 - 1)) == 2**63 - 1
    with pytest.raises(PreparationError, match="integer_range_invalid"): _integer(str(2**63))


def test_float_boolean_integer_and_unicode_are_fail_closed():
    fixture = _fixture(); fixture["external_trust_policy"]["policy_version"] = True
    with pytest.raises(PreparationError): validate_preparation(fixture)
    for payload in (b'{"x":1.0}', b'{"x":"\\uD800"}', b"\xff"):
        result = subprocess.run(
            [sys.executable, "-B", "-m", "backend.health_vault.companion_host.r4f_preparation"],
            cwd=ROOT, input=payload, capture_output=True, timeout=15, check=False,
        )
        assert result.returncode == EXIT_INVOCATION and result.stderr == b"" and len(result.stdout.splitlines()) == 1


@pytest.mark.parametrize(
    ("status", "revocation", "purpose", "policy", "issuer", "expected"),
    [
        ("valid", "valid", "code", CODE_POLICY, True, "VALID_FOR_PREPARATION"),
        ("invalid", "valid", "code", CODE_POLICY, True, "FAIL"),
        ("expired", "valid", "code", CODE_POLICY, True, "FAIL"),
        ("revoked", "valid", "code", CODE_POLICY, True, "FAIL"),
        ("valid", "indeterminate", "code", CODE_POLICY, True, "BLOCKED"),
        ("valid", "valid", "evidence", CODE_POLICY, True, "FAIL"),
        ("valid", "valid", "code", CODE_POLICY, False, "FAIL"),
    ],
)
def test_certificate_verdicts(status: str, revocation: str, purpose: str, policy: str, issuer: bool, expected: str):
    metadata = {
        "status": status, "revocation": revocation, "purpose": purpose, "policy": policy,
        "issuer_allowed": issuer, "chain_policy": "hc-private-pilot-chain-v1",
        "root_crl": "valid", "issuing_crl": "valid",
    }
    assert classify_certificate(metadata, "code") == expected


def test_missing_tpm_and_indeterminate_installer_revocation_are_blocked():
    fixture = _fixture(); fixture["pki_policy"]["tpm_support"] = "BLOCKED"
    result = validate_preparation(fixture)
    assert result["checks"][0]["status"] == "BLOCKED"
    fixture = _fixture(); fixture["reinstall_plan"]["revocation"] = "indeterminate"
    result = validate_preparation(fixture)
    assert result["checks"][2]["status"] == "BLOCKED"


@pytest.mark.parametrize("arg", ["--live", "--apply", "--install", "--sign", "--force", "-Apply"])
def test_live_apply_install_and_sign_arguments_fail_closed(arg: str):
    result = _run(None, arg)
    assert result.returncode == EXIT_INVOCATION and result.stderr == b""
    assert json.loads(result.stdout)["error"] == "preparation_fixture_invalid"
    assert b"live" not in result.stdout and b"apply" not in result.stdout


def test_output_redacts_canaries_and_oversized_input():
    fixture = _fixture(); fixture["secret-canary"] = r"C:\sensitive\private\path"
    result = _run(fixture)
    assert result.returncode == EXIT_INVOCATION and result.stderr == b""
    assert b"secret-canary" not in result.stdout and b"sensitive" not in result.stdout
    result = subprocess.run(
        [sys.executable, "-B", "-m", "backend.health_vault.companion_host.r4f_preparation"],
        cwd=ROOT, input=b"x" * 131_073, capture_output=True, timeout=15, check=False,
    )
    assert result.returncode == EXIT_INVOCATION and len(result.stdout.splitlines()) == 1 and result.stderr == b""


def test_duplicate_json_keys_and_nonfinite_numbers_are_rejected():
    for payload in (b'{"schema_version":"a","schema_version":"b"}', b'{"x":NaN}'):
        result = subprocess.run(
            [sys.executable, "-B", "-m", "backend.health_vault.companion_host.r4f_preparation"],
            cwd=ROOT, input=payload, capture_output=True, timeout=15, check=False,
        )
        assert result.returncode == EXIT_INVOCATION and result.stderr == b""
        assert json.loads(result.stdout)["error"] == "preparation_fixture_invalid"


def test_fixed_seed_bounded_adversarial_matrix_is_redacted():
    rng = random.Random(30941)
    payloads = [b"", b"{", b"{} trailing", b"\xff\xfe", b'{"x":1.0}', b'{"x":"\\uD800"}']
    payloads.extend(b"\xff" + rng.randbytes(rng.randrange(1, 2048)) for _ in range(12))
    for payload in payloads:
        result = subprocess.run(
            [sys.executable, "-B", "-m", "backend.health_vault.companion_host.r4f_preparation"],
            cwd=ROOT, input=payload, capture_output=True, timeout=15, check=False,
        )
        assert result.returncode == EXIT_INVOCATION and result.stderr == b""
        assert len(result.stdout.splitlines()) == 1
        assert json.loads(result.stdout)["error"] == "preparation_fixture_invalid"


def _open_stdin_result(*, trickle: bool = False, env: dict[str, str] | None = None) -> tuple[subprocess.CompletedProcess[bytes], float, set[int]]:
    process = subprocess.Popen(
        [sys.executable, "-B", "-m", "backend.health_vault.companion_host.r4f_preparation"],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    process.stdin.write(b'{"schema_version":'); process.stdin.flush()
    started = time.monotonic(); observed = set()
    while process.poll() is None and time.monotonic() - started < INPUT_DEADLINE_SECONDS + 7:
        observed |= _children(process.pid)
        if trickle:
            try: process.stdin.write(b" "); process.stdin.flush()
            except (BrokenPipeError, OSError): break
        time.sleep(0.25)
    elapsed = time.monotonic() - started
    if process.poll() is None:
        process.kill(); pytest.fail("preparation stdin deadline was not enforced")
    try: process.stdin.close()
    except OSError: pass
    result = subprocess.CompletedProcess(process.args, process.returncode, process.stdout.read(), process.stderr.read())
    return result, elapsed, observed


def test_total_stdin_deadline_is_fixed_redacted_and_has_no_child():
    hostile_env = os.environ.copy(); hostile_env["HC_R4F_INPUT_DEADLINE"] = "999999"
    result, elapsed, observed = _open_stdin_result(env=hostile_env)
    assert 8.0 <= elapsed <= 15.0 and observed == set()
    assert result.returncode == EXIT_INVOCATION and result.stderr == b""
    assert len(result.stdout.splitlines()) == 1
    assert json.loads(result.stdout)["error"] == "preparation_fixture_invalid"


def test_total_deadline_is_not_reset_by_slow_partial_writes():
    result, elapsed, observed = _open_stdin_result(trickle=True)
    assert 8.0 <= elapsed <= 15.0 and observed == set()
    assert result.returncode == EXIT_INVOCATION and result.stderr == b"" and len(result.stdout.splitlines()) == 1


def test_complete_input_before_deadline_and_transition_are_stable():
    assert _run().returncode == EXIT_BLOCKED
    payload = json.dumps(_fixture(), separators=(",", ":")).encode()
    process = subprocess.Popen(
        [sys.executable, "-B", "-m", "backend.health_vault.companion_host.r4f_preparation"],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert process.stdin is not None
    for offset in range(0, len(payload), 256):
        process.stdin.write(payload[offset:offset + 256]); process.stdin.flush(); time.sleep(0.01)
    stdout, stderr = process.communicate(timeout=INPUT_DEADLINE_SECONDS)
    assert process.returncode == EXIT_BLOCKED and stderr == b"" and json.loads(stdout)["certification_status"] == "BLOCKED"


@pytest.mark.parametrize("delay", [9.0, 10.5])
def test_deadline_transition_race_is_single_record_and_fail_closed(delay: float):
    payload = json.dumps(_fixture(), separators=(",", ":")).encode()
    process = subprocess.Popen(
        [sys.executable, "-B", "-m", "backend.health_vault.companion_host.r4f_preparation"],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    time.sleep(delay)
    try: process.stdin.write(payload); process.stdin.close()
    except (BrokenPipeError, OSError):
        try: process.stdin.close()
        except OSError: pass
    process.wait(timeout=5)
    stdout, stderr = process.stdout.read(), process.stderr.read()
    assert process.returncode in {EXIT_BLOCKED, EXIT_INVOCATION}
    assert stderr == b"" and len(stdout.splitlines()) == 1
    assert json.loads(stdout)["certification_status"] in {"BLOCKED", "FAIL"}


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]


def _children(parent: int) -> set[int]:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True); snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    entry = PROCESSENTRY32W(); entry.dwSize = ctypes.sizeof(entry); found = set()
    try:
        ok = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if entry.th32ParentProcessID == parent: found.add(entry.th32ProcessID)
            ok = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally: kernel.CloseHandle(snapshot)
    return found


def test_cli_creates_no_file_and_no_child_process(tmp_path: Path):
    watched = (
        MODULE, Path(__file__),
        ROOT / "docs/governance/HC-309-R4F_PREPARATION_AND_READINESS.md",
        ROOT / "docs/governance/HC-309-R4F_PILOT_PKI_PROFILE.md",
        ROOT / "docs/governance/HC-309-R4F_IMMUTABLE_COLLECTOR_PACKAGE.md",
        ROOT / "docs/governance/HC-309-R4G_CONTROLLED_CPYTHON_REINSTALL_RUNBOOK.md",
    )
    before = {path: (path.stat().st_size, path.stat().st_mtime_ns) for path in watched}
    temp_before = set(tmp_path.rglob("*"))
    environment_before = dict(os.environ)
    registry_before = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            index = 0
            while True:
                try: registry_before.append(winreg.EnumValue(key, index)); index += 1
                except OSError: break
    except FileNotFoundError: pass
    process = subprocess.Popen(
        [sys.executable, "-B", "-m", "backend.health_vault.companion_host.r4f_preparation"],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert process.stdin is not None; process.stdin.write(json.dumps(_fixture()).encode()); process.stdin.close()
    observed = set()
    while process.poll() is None:
        observed |= _children(process.pid); time.sleep(0.01)
    stdout, stderr = process.stdout.read(), process.stderr.read()
    assert process.returncode == EXIT_BLOCKED and stdout and stderr == b"" and observed == set()
    assert before == {path: (path.stat().st_size, path.stat().st_mtime_ns) for path in watched}
    assert temp_before == set(tmp_path.rglob("*"))
    assert environment_before == dict(os.environ)
    registry_after = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            index = 0
            while True:
                try: registry_after.append(winreg.EnumValue(key, index)); index += 1
                except OSError: break
    except FileNotFoundError: pass
    assert registry_before == registry_after


def _assert_validator_ast_safe(source: str, *, require_deadline_exit: bool = False) -> None:
    tree = ast.parse(source)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    allowed_imports = {"hashlib", "json", "os", "re", "sys", "threading"}
    allowed_from = {"__future__", "typing"}
    forbidden_names = {
        "getattr", "setattr", "vars", "globals", "locals", "__import__",
        "eval", "exec", "compile", "open", "input", "help", "breakpoint",
        "builtins", "__builtins__",
    }
    forbidden_attributes = {
        "Popen", "run", "system", "spawn", "FileIO", "urlopen", "connect",
        "CreateProcess", "OpenKey", "SetValue", "getenv", "environ",
        "__dict__", "__class__", "__globals__", "__getattribute__",
        "__subclasses__", "__mro__", "__bases__", "__code__", "__closure__",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert {alias.name for alias in node.names} <= allowed_imports
            assert all(alias.asname not in forbidden_names for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module in allowed_from
            assert all(alias.asname not in forbidden_names for alias in node.names)
        elif isinstance(node, ast.Name):
            assert node.id not in forbidden_names
        elif isinstance(node, ast.arg):
            assert node.arg not in forbidden_names
        elif isinstance(node, ast.Attribute):
            assert node.attr not in forbidden_attributes
            if isinstance(node.value, ast.Name) and node.value.id == "os":
                assert node.attr == "_exit"
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            assert node.name not in forbidden_names
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            assert set(node.names).isdisjoint(forbidden_names)
        elif isinstance(node, ast.keyword):
            assert node.arg not in forbidden_names
        elif isinstance(node, ast.ExceptHandler):
            assert node.name not in forbidden_names

    exit_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "os" and node.func.attr == "_exit"
    ]
    for attribute in (
        node for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
        and node.value.id == "os" and node.attr == "_exit"
    ):
        assert isinstance(parents.get(attribute), ast.Call) and parents[attribute].func is attribute
    if require_deadline_exit:
        assert len(exit_calls) == 1
        call = exit_calls[0]
        assert len(call.args) == 1 and isinstance(call.args[0], ast.Name) and call.args[0].id == "EXIT_INVOCATION"
        assert not call.keywords
        statement = parents.get(call)
        assert isinstance(statement, ast.Expr)
        container = parents.get(statement)
        assert isinstance(container, (ast.If, ast.For, ast.While, ast.With, ast.Try))
        candidate_bodies = [
            body for body in (getattr(container, "body", []), getattr(container, "orelse", []), getattr(container, "finalbody", []))
            if statement in body
        ]
        assert len(candidate_bodies) == 1
        body = candidate_bodies[0]
        position = body.index(statement)
        assert position > 0
        write_statement = body[position - 1]
        assert isinstance(write_statement, ast.Expr) and isinstance(write_statement.value, ast.Call)
        write_call = write_statement.value
        assert isinstance(write_call.func, ast.Name) and write_call.func.id == "_write_result"
        assert len(write_call.args) == 1 and isinstance(write_call.args[0], ast.Call)
        assert isinstance(write_call.args[0].func, ast.Name) and write_call.args[0].func.id == "_error"
        ancestor = container
        while ancestor is not None and not isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef)):
            ancestor = parents.get(ancestor)
        assert isinstance(ancestor, ast.FunctionDef) and ancestor.name == "_read_bounded_with_deadline"
        writers = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_write_result"]
        assert len(writers) == 1
        operations = [
            node for node in ast.walk(writers[0]) if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Attribute)
            and isinstance(node.func.value.value, ast.Name) and node.func.value.value.id == "sys"
            and node.func.value.attr == "stdout" and node.func.attr in {"write", "flush"}
        ]
        assert [operation.func.attr for operation in operations] == ["write", "flush"]


def test_source_has_ast_enforced_import_and_mutation_allowlist():
    _assert_validator_ast_safe(MODULE.read_text(encoding="utf-8"), require_deadline_exit=True)
    malicious = (
        "import subprocess\nsubprocess.run(['x'])",
        "import socket\nsocket.socket()",
        "import http.client",
        "import urllib.request",
        "import ctypes",
        "import importlib",
        "import builtins\nbuiltins.open('x')",
        "import io\nio.FileIO('x')",
        "import os\nos.getenv('X')",
        "eval('1')",
    )
    for source in malicious:
        with pytest.raises(AssertionError):
            _assert_validator_ast_safe(source)


@pytest.mark.parametrize("source", (
    'import os\ngetattr(os, "system")',
    'getattr(__builtins__, "open")',
    'import os\nvars(os)["system"]',
    'globals()["__builtins__"]',
    'locals().get("x")',
    'import os\nsetattr(os, "x", 1)',
))
def test_ast_rejects_indirect_reflection(source: str):
    with pytest.raises(AssertionError):
        _assert_validator_ast_safe(source)


@pytest.mark.parametrize("source", (
    "g = getattr",
    "getattr = safe_value",
    "del getattr",
    "g: object = getattr",
    "lambda g=getattr: g(None, 'x')",
    "[getattr]",
    "(getattr,)",
    "{'g': getattr}",
    "{getattr}",
    "[x for x in [getattr]]",
    "def f():\n    return getattr",
    "def f():\n    yield getattr",
    "f'{getattr}'",
    "(g := getattr)",
    "fn(getattr)",
    "fn(x=getattr)",
    "imp = __import__",
    "def f(getattr):\n    pass",
    "def f(globals):\n    pass",
    "lambda __import__: None",
    "import os as getattr",
    "import builtins\nbuiltins.open('x')",
    "value.__dict__",
    "value.__class__",
    "value.__globals__",
    "value.__getattribute__",
    "value.__subclasses__",
    "value.__mro__",
    "value.__bases__",
    "value.__code__",
    "value.__closure__",
))
def test_ast_rejects_every_forbidden_reference(source: str):
    with pytest.raises(AssertionError):
        _assert_validator_ast_safe(source)


@pytest.mark.parametrize("source", (
    "value = 1",
    "value: object = None",
    "def f(value=1):\n    return value",
    "lambda value=1: value",
    "[value for value in [1, 2]]",
    "(first, second) = (1, 2)",
    "{'value': 1}",
    "fn(value=1)",
))
def test_ast_accepts_ordinary_safe_references(source: str):
    _assert_validator_ast_safe(source)


@pytest.mark.parametrize("profile", ("code_signing", "evidence_signing"))
def test_leaf_profiles_require_exact_basic_constraints_and_key_usage(profile: str):
    assert validate_preparation(_fixture())["certification_status"] == "BLOCKED"
    error = f"{profile}_profile_invalid"
    for path, value in (
        (("is_ca",), True),
        (("basic_constraints", "present"), False),
        (("basic_constraints", "critical"), False),
        (("key_usage", "digital_signature"), False),
        (("key_usage", "certificate_signing"), True),
        (("key_usage", "crl_signing"), True),
        (("key_usage", "key_encipherment"), True),
        (("key_usage", "data_encipherment"), True),
        (("key_usage", "key_agreement"), True),
        (("key_usage", "content_commitment"), True),
        (("key_usage", "digital_signature"), 1),
    ):
        fixture = _fixture()
        target = fixture["pki_policy"][profile]
        for component in path[:-1]:
            target = target[component]
        target[path[-1]] = value
        with pytest.raises(PreparationError, match=error):
            validate_preparation(fixture)


@pytest.mark.parametrize("profile", ("code_signing", "evidence_signing"))
@pytest.mark.parametrize("container,field", (
    (None, "is_ca"),
    ("basic_constraints", "present"),
    ("basic_constraints", "critical"),
    ("key_usage", "digital_signature"),
    ("key_usage", "content_commitment"),
))
def test_leaf_profiles_reject_missing_and_unknown_fields(profile: str, container: str | None, field: str):
    for operation in ("missing", "unknown"):
        fixture = _fixture()
        target = fixture["pki_policy"][profile] if container is None else fixture["pki_policy"][profile][container]
        if operation == "missing":
            del target[field]
        else:
            target["unknown"] = False
        with pytest.raises(PreparationError, match=f"{profile}_profile_invalid"):
            validate_preparation(fixture)


VALID_EXIT_PATTERN = '''import os
import sys
EXIT_INVOCATION = 22
def _error(): return {}
def _write_result(value):
    sys.stdout.write("fixed")
    sys.stdout.flush()
def _read_bounded_with_deadline():
    if True:
        _write_result(_error())
        os._exit(EXIT_INVOCATION)
'''


def test_ast_accepts_only_reviewed_deadline_exit_pattern():
    _assert_validator_ast_safe(VALID_EXIT_PATTERN, require_deadline_exit=True)


@pytest.mark.parametrize("source", (
    VALID_EXIT_PATTERN.replace("_read_bounded_with_deadline", "another_function"),
    VALID_EXIT_PATTERN + "\nos._exit(EXIT_INVOCATION)\n",
    VALID_EXIT_PATTERN.replace("os._exit(EXIT_INVOCATION)", "os._exit(1)"),
    VALID_EXIT_PATTERN.replace("        _write_result(_error())\n        os._exit(EXIT_INVOCATION)", "        os._exit(EXIT_INVOCATION)\n        _write_result(_error())"),
    VALID_EXIT_PATTERN.replace("    sys.stdout.flush()\n", ""),
    VALID_EXIT_PATTERN.replace("        os._exit(EXIT_INVOCATION)", "        quit = os._exit\n        quit(EXIT_INVOCATION)"),
))
def test_ast_rejects_prohibited_exit_forms(source: str):
    with pytest.raises(AssertionError):
        _assert_validator_ast_safe(source, require_deadline_exit=True)


def test_governance_references_and_non_authorization_are_complete():
    documents = (
        ROOT / "docs/governance/HC-309-R4F_PREPARATION_AND_READINESS.md",
        ROOT / "docs/governance/HC-309-R4F_PILOT_PKI_PROFILE.md",
        ROOT / "docs/governance/HC-309-R4F_IMMUTABLE_COLLECTOR_PACKAGE.md",
        ROOT / "docs/governance/HC-309-R4G_CONTROLLED_CPYTHON_REINSTALL_RUNBOOK.md",
    )
    combined = "\n".join(path.read_text(encoding="utf-8") for path in documents)
    normalized = " ".join(combined.split()).lower()
    assert all(path.is_file() for path in documents)
    for reference in (
        "HC-309-R4B_TRUSTED_COLLECTOR_AND_RUNTIME_PROVENANCE_SPEC.md",
        "HC-309-R4C_OPERATOR_SIGNING_AND_PROVENANCE_DECISIONS.md",
        "HC-309-R4D",
        "R4F-EXEC",
        "R4G",
    ):
        assert reference in combined
    for statement in (
        "current runtime remains uncertified",
        "digest remains absent",
        "Certification PASS remains unreachable",
        "not clinical certification",
        "PACKAGE_MANIFEST.p7s",
        "HC_COLLECTOR_TRUST_POLICY.json",
        "never lists or hashes itself",
        "issuing CA private key",
        "root CRL",
        "10-second total deadline",
        "depth 12",
    ):
        assert statement.lower() in normalized
