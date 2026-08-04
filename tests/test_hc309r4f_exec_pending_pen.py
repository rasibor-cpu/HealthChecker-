from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path

import pytest

from backend.health_vault.companion_host.r4f_preparation import (
    EXIT_BLOCKED,
    EXIT_FAIL,
    MAX_INPUT_BYTES,
    PENDING_PEN_SCHEMA,
    PreparationError,
    evaluate_pending_pen_readiness,
    parse_pending_pen_readiness,
    validate_pending_pen_readiness,
)
from tests.test_hc309r4f_preparation import _assert_validator_ast_safe


ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "config/hc309_r4f_exec_pending_pen_readiness.json"
MODULE = ROOT / "backend/health_vault/companion_host/r4f_preparation.py"


def _record() -> dict:
    return parse_pending_pen_readiness(RECORD.read_bytes())


def _set(path: tuple[str, ...], value: object) -> dict:
    record = copy.deepcopy(_record())
    target = record
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = value
    return record


def test_authoritative_record_is_exact_and_deterministically_blocked():
    record = _record()
    assert record["schema_version"] == PENDING_PEN_SCHEMA
    first = validate_pending_pen_readiness(record)
    second = validate_pending_pen_readiness(copy.deepcopy(record))
    assert first == second
    assert first == {
        "authorization": "readiness_only",
        "certification_status": "BLOCKED",
        "checks": [
            {"name": "operator_decisions", "status": "READY"},
            {"name": "toolchain_evidence", "status": "READY"},
            {"name": "pen_assignment", "status": "BLOCKED"},
            {"name": "oid_materialization", "status": "BLOCKED"},
            {"name": "pki_mutation", "status": "BLOCKED"},
            {"name": "signing", "status": "BLOCKED"},
            {"name": "runtime_reinstall", "status": "BLOCKED"},
            {"name": "certification_pass", "status": "BLOCKED"},
            {"name": "mandatory_check_registry", "status": "READY"},
        ],
        "environment": "pilot",
        "exit_code": EXIT_BLOCKED,
        "live_execution_status": "BLOCKED",
        "schema_version": "hc.r4f_exec_pending_pen_result.v1",
    }
    assert "PASS" not in json.dumps(first) and first["exit_code"] != 0


@pytest.mark.parametrize("attempt", (1, "123", -1, 0, 32473, 2**80))
def test_assigned_pen_attempts_are_rejected_while_pending(attempt: object):
    with pytest.raises(PreparationError, match="pending_pen_schema_invalid"):
        validate_pending_pen_readiness(_set(("pen", "assigned_pen"), attempt))


@pytest.mark.parametrize("attempt", (
    "1.3.6.1.4.1.32473.1",
    "2.25.329800735698586629295641978511506172918",
    "1.3.6.1.4.1.311.999",
    "1.3.6.1.4.1.99999.2",
))
@pytest.mark.parametrize("field", ("certificate_policy_oid", "evidence_eku_oid"))
def test_all_oid_materialization_is_rejected_while_pen_pending(field: str, attempt: str):
    with pytest.raises(PreparationError, match="pending_pen_schema_invalid"):
        validate_pending_pen_readiness(_set(("pen", field), attempt))


@pytest.mark.parametrize("field", (
    "pki_mutation_authorized", "key_creation_authorized",
    "certificate_creation_authorized", "signing_authorized",
    "runtime_reinstall_authorized", "certification_pass_reachable",
))
def test_every_authorization_attempt_fails_closed(field: str):
    result = validate_pending_pen_readiness(_set(("authorization", field), True))
    assert result["certification_status"] == "FAIL" and result["exit_code"] == EXIT_FAIL
    assert next(check for check in result["checks"] if check["name"] == "pki_mutation")["status"] == "FAIL"


@pytest.mark.parametrize(("path", "value"), (
    (("owner",), "wrong"),
    (("pen", "assignee"), "wrong"),
    (("pen", "status"), "assigned"),
    (("pen", "iana_confirmation_status"), "unconfirmed"),
    (("profiles", "root", "algorithm"), "ECDSA"),
    (("profiles", "root", "key_size"), 2048),
    (("profiles", "root", "validity_years"), 20),
    (("profiles", "issuing", "path_length"), 1),
    (("profiles", "code_signer", "eku"), "server_auth"),
    (("profiles", "code_signer", "exportable"), True),
    (("profiles", "evidence_signer", "curve"), "P-384"),
    (("profiles", "evidence_signer", "exportable"), True),
    (("profiles", "evidence_signer", "key_role"), "code_signing"),
    (("provider_policy", "tpm_provider"), "software"),
    (("provider_policy", "software_fallback"), "allowed"),
    (("provider_policy", "private_keys_exist"), True),
    (("timestamp_policy", "endpoint_is_trust_anchor"), True),
    (("timestamp_policy", "validate_full_returned_chain"), False),
    (("revocation_policy", "mode"), "best_effort"),
    (("revocation_policy", "stale_or_unavailable"), "READY"),
    (("runtime_state", "certification_status"), "PASS"),
    (("runtime_state", "certification_pass_reachable"), True),
    (("runtime_state", "repository_head_is_active_release"), True),
    (("authorization", "live_execution_status"), "PASS"),
    (("toolchain_evidence", "signtool_path"), "signtool.exe"),
    (("toolchain_evidence", "signtool_sha256"), "0" * 64),
    (("toolchain_evidence", "signtool_authenticode_status"), "Invalid"),
    (("toolchain_evidence", "sdk_publisher"), "wrong"),
    (("toolchain_evidence", "installer_source_class"), "mirror"),
    (("toolchain_evidence", "installer_authenticode_status"), "Invalid"),
    (("toolchain_evidence", "installer_sha256"), "0" * 64),
))
def test_semantic_substitution_is_fail_closed(path: tuple[str, ...], value: object):
    result = validate_pending_pen_readiness(_set(path, value))
    assert result["certification_status"] == "FAIL" and result["exit_code"] == EXIT_FAIL
    assert "PASS" not in json.dumps(result)


def test_fail_has_precedence_over_pending_blockers():
    result = validate_pending_pen_readiness(_set(("owner",), "wrong"))
    statuses = {check["status"] for check in result["checks"]}
    assert "FAIL" in statuses and "BLOCKED" in statuses
    assert result["certification_status"] == "FAIL"


@pytest.mark.parametrize("container", ("root", "pen", "authorization", "mandatory_checks"))
def test_missing_and_unknown_fields_are_configuration_errors(container: str):
    path = ("profiles", "root") if container == "root" else (container,)
    for operation in ("missing", "unknown"):
        record = _record()
        target = record
        for component in path:
            target = target[component]
        if operation == "missing":
            target.pop(next(iter(target)))
        else:
            target["unknown"] = False
        with pytest.raises(PreparationError, match="pending_pen_schema_invalid"):
            validate_pending_pen_readiness(record)


@pytest.mark.parametrize("field", ("authentication_valid", "trust_capability", "iana_request_reference", "email", "second_custodian_name"))
def test_trust_and_personal_data_injection_is_rejected(field: str):
    record = _record()
    record[field] = True
    with pytest.raises(PreparationError, match="pending_pen_schema_invalid"):
        validate_pending_pen_readiness(record)


def test_mandatory_check_registry_is_fixed_and_exact():
    expected_checks = {
        "operator_decisions": "READY",
        "toolchain_evidence": "READY",
        "pen_assignment": "BLOCKED",
        "oid_materialization": "BLOCKED",
        "pki_mutation": "BLOCKED",
        "signing": "BLOCKED",
        "runtime_reinstall": "BLOCKED",
        "certification_pass": "BLOCKED",
    }
    assert _record()["mandatory_checks"] == expected_checks
    for name in expected_checks:
        record = _record(); record["mandatory_checks"].pop(name)
        with pytest.raises(PreparationError, match="pending_pen_schema_invalid"):
            validate_pending_pen_readiness(record)
    record = _record(); record["mandatory_checks"]["unknown"] = "BLOCKED"
    with pytest.raises(PreparationError, match="pending_pen_schema_invalid"):
        validate_pending_pen_readiness(record)
    result = validate_pending_pen_readiness(_set(("mandatory_checks", "toolchain_evidence"), "BLOCKED"))
    assert result["certification_status"] == "FAIL"


def test_boolean_integer_confusion_and_duplicate_json_are_rejected():
    with pytest.raises(PreparationError, match="pending_pen_schema_invalid"):
        validate_pending_pen_readiness(_set(("authorization", "signing_authorized"), 0))
    with pytest.raises(PreparationError, match="pending_pen_schema_invalid"):
        validate_pending_pen_readiness(_set(("profiles", "root", "key_size"), True))
    with pytest.raises(PreparationError, match="duplicate_json_key"):
        parse_pending_pen_readiness(b'{"schema_version":"a","schema_version":"b"}')


def test_parser_byte_and_structure_limits_remain_fail_closed():
    raw = RECORD.read_bytes()
    assert len(raw) <= MAX_INPUT_BYTES and parse_pending_pen_readiness(raw)
    with pytest.raises(PreparationError, match="pending_pen_input_invalid"):
        parse_pending_pen_readiness(b" " * (MAX_INPUT_BYTES + 1))
    with pytest.raises(PreparationError, match="json_array_exceeded"):
        parse_pending_pen_readiness(json.dumps([0] * 33).encode())
    value = [["x"] * 10 for _ in range(32)]
    with pytest.raises(PreparationError, match="json_scalars_exceeded"):
        parse_pending_pen_readiness(json.dumps(value[:-1] + [value[-1] + ["x"]]).encode())


def test_errors_are_fixed_redacted_and_do_not_echo_input():
    messages = []
    for raw in (b"not-json-sensitive", b"\xff", b"[]"):
        with pytest.raises(PreparationError) as caught:
            validate_pending_pen_readiness(parse_pending_pen_readiness(raw))
        messages.append(str(caught.value))
    assert messages == ["pending_pen_input_invalid", "pending_pen_input_invalid", "pending_pen_schema_invalid"]
    assert all("sensitive" not in message for message in messages)


def test_evaluator_has_no_host_or_mutation_surface_and_existing_ast_guard_accepts_source():
    source = MODULE.read_text(encoding="utf-8")
    _assert_validator_ast_safe(source, require_deadline_exit=True)
    forbidden = (
        "subprocess", "winreg", "ctypes", "socket", "urllib", "requests",
        "cryptography", "import certifi", "SignTool", "Get-ChildItem", "ProgramData",
    )
    evaluator = source[source.index("def _require_pending_pen_shape"):source.index("def validate_preparation")]
    assert all(token not in evaluator for token in forbidden)


def test_validation_performs_no_write_or_child_process():
    watched = (RECORD, MODULE)
    before = {path: (path.stat().st_size, path.stat().st_mtime_ns) for path in watched}
    for _ in range(10):
        assert validate_pending_pen_readiness(_record())["certification_status"] == "BLOCKED"
    assert before == {path: (path.stat().st_size, path.stat().st_mtime_ns) for path in watched}


@pytest.mark.parametrize("schema", (
    "",
    " ",
    "HC.R4F_EXEC_PENDING_PEN_READINESS.V1",
    "hc.r4f_exec_pending_pen_readiness.v0",
    "hc.r4f_exec_pending_pen_readiness.v2",
    "attacker.schema.v999",
    " hc.r4f_exec_pending_pen_readiness.v1",
    "hc.r4f_exec_pending_pen_readiness.v1 ",
    "hc.r4f_exec_pending_pen_readiness.v１",
    None,
    1,
    False,
))
def test_wrong_schema_is_one_fixed_redacted_invocation_result(schema: object):
    record = _record(); record["schema_version"] = schema
    first = evaluate_pending_pen_readiness(json.dumps(record).encode())
    second = evaluate_pending_pen_readiness(json.dumps(record).encode())
    assert first == second == {
        "authorization": "readiness_only",
        "certification_status": "FAIL",
        "environment": "pilot",
        "error": "readiness_configuration_invalid",
        "exit_code": 22,
        "live_execution_status": "BLOCKED",
        "schema_version": "hc.r4f_exec_pending_pen_result.v1",
    }
    serialized = json.dumps(first)
    assert "attacker.schema.v999" not in serialized
    assert "Robert Asibor" not in serialized and "ProgramData" not in serialized
    assert "PASS" not in serialized and '"exit_code": 0' not in serialized


def test_caller_record_and_result_mutations_do_not_cross_calls():
    baseline = validate_pending_pen_readiness(_record())
    assert baseline["certification_status"] == "BLOCKED" and baseline["exit_code"] == EXIT_BLOCKED
    for section in (
        "pen", "custodians", "profiles", "provider_policy", "timestamp_policy",
        "revocation_policy", "locations", "retention_and_rollback", "runtime_state",
        "toolchain_evidence", "authorization", "mandatory_checks",
    ):
        caller_record = _record()
        validate_pending_pen_readiness(caller_record)
        caller_record[section].clear()
        assert validate_pending_pen_readiness(_record()) == baseline
    returned = validate_pending_pen_readiness(_record())
    returned["certification_status"] = "PASS"
    returned["checks"].clear()
    assert validate_pending_pen_readiness(_record()) == baseline
    caller_copy = copy.deepcopy(_record())
    caller_copy["authorization"]["signing_authorized"] = True
    assert validate_pending_pen_readiness(caller_copy)["certification_status"] == "FAIL"
    assert validate_pending_pen_readiness(_record()) == baseline


@pytest.mark.parametrize("field", (
    "signing_authorized", "pki_mutation_authorized", "key_creation_authorized",
    "certificate_creation_authorized", "runtime_reinstall_authorized",
    "certification_pass_reachable",
))
def test_forbidden_authorization_is_fail_after_cross_call_mutation(field: str):
    first = _record(); first["profiles"]["root"].clear()
    with pytest.raises(PreparationError):
        validate_pending_pen_readiness(first)
    forbidden = _record(); forbidden["authorization"][field] = True
    result = validate_pending_pen_readiness(forbidden)
    assert result["certification_status"] == "FAIL" and result["exit_code"] == EXIT_FAIL
    assert validate_pending_pen_readiness(_record())["certification_status"] == "BLOCKED"


def test_no_public_mutable_compatibility_baseline_can_be_monkeypatched(monkeypatch: pytest.MonkeyPatch):
    import backend.health_vault.companion_host.r4f_preparation as module

    assert not hasattr(module, "PENDING_PEN_EXPECTED")
    assert not hasattr(module, "PENDING_PEN_CHECKS")
    malicious = _record(); malicious["authorization"]["signing_authorized"] = True
    monkeypatch.setattr(module, "PENDING_PEN_EXPECTED", malicious, raising=False)
    monkeypatch.setattr(module, "PENDING_PEN_CHECKS", {"signing": "READY"}, raising=False)
    result = module.validate_pending_pen_readiness(malicious)
    assert result["certification_status"] == "FAIL" and result["exit_code"] == EXIT_FAIL
    assert module.validate_pending_pen_readiness(_record())["certification_status"] == "BLOCKED"


def test_trust_baseline_has_no_mutable_defaults_or_exported_policy_containers():
    import backend.health_vault.companion_host.r4f_preparation as module

    signature = inspect.signature(module.validate_pending_pen_readiness)
    assert all(parameter.default is inspect.Parameter.empty for parameter in signature.parameters.values())
    assert isinstance(module._PENDING_PEN_CHECKS, tuple)
    assert all(isinstance(item, tuple) for item in module._PENDING_PEN_CHECKS)
    first = module._pending_pen_expected()
    second = module._pending_pen_expected()
    assert first == second and first is not second
    for name in first:
        if isinstance(first[name], dict):
            assert first[name] is not second[name]


def test_public_record_and_phase_report_preserve_privacy_and_authorization_boundary():
    report = (ROOT / "docs/governance/HC-309-R4F-EXEC-P1_PENDING_PEN_READINESS.md").read_text(encoding="utf-8")
    record_text = RECORD.read_text(encoding="utf-8")
    combined = " ".join((report + record_text).lower().split())
    for statement in (
        "iana request is confirmed",
        "no iana request reference",
        "no keys, certificates, signatures",
        "signtool readiness does not authorize signing",
        "personal custodian identities belong only",
        "current runtime remains healthy but uncertified",
        "active tasks remain bound to older immutable releases",
        "r4f pki mutation and r4g reinstall remain blocked",
        "independent registry verification",
        "separate commit and explicit approval",
        "offline ceremony-readiness review",
    ):
        assert statement in combined
    assert "@" not in record_text
    assert "iana_request_reference" not in record_text
    assert "second_custodian_name" not in record_text
