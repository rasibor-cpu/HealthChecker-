from __future__ import annotations

import json
import inspect
import os
from pathlib import Path
import subprocess

import pytest

from backend.health_vault.companion_host.protected_runtime_policy import (
    EXIT_BLOCKED, EXIT_FAIL, EXIT_INVOCATION, MANDATORY_CHECKS,
    MAX_ARRAY_ELEMENTS, MAX_NESTING_DEPTH, MAX_STRING_LENGTH,
    ConfigurationError, evaluate_evidence, load_evidence, main,
)

C, H = "a" * 40, "A" * 64


def _task(name):
    return {"reported_name": name, "principal": "SYSTEM", "enabled": True, "state": "Running", "action_count": 1, "executable": "trusted_system_powershell", "argument_grammar": "strict_file_only", "bootstrap": "companion_host" if name.endswith("Host") else "companion_proxy", "no_trailing_arguments": True, "no_environment_expansion": True, "working_directory": "active_immutable_release", "trigger_count": 1, "trigger": "AtStartup", "trigger_delay": "PT0S" if name.endswith("Host") else "PT15S", "multiple_instances": "IgnoreNew", "restart_count": 3, "restart_interval": "PT1M"}


def _evidence():
    pkg = lambda n, v: {"name": n, "version": v, "expected": True, "applicable": True, "version_matches": True, "protected_runtime_location": True, "editable": False, "direct_url": False}
    health = lambda s: {"http_status": 200, "ok": True, "status": s, "schema_exact": True, "response_within_limit": True, "bounded_timeout_used": True}
    return {"schema_version": "hc.protected_runtime_evidence.v1", "runtime_contract": {"schema_valid": True, "implementation": "CPython", "version": "3.12.10", "architecture": "win_amd64", "fixed_path_contract": True, "executable_sha256": H}, "protected_executable": {"exists": True, "fixed_path_valid": True, "no_reparse_points": True, "no_unsafe_resolution": True, "digest_matches": True, "digest_stable_across_probe": True, "executed_after_digest_validation": True, "implementation": "CPython", "version": "3.12.10", "architecture": "AMD64"}, "active_release": {"expected_commit": C, "repository_head": C, "current_commit": C, "directory_commit": C, "manifest_commit": C, "repository_identity_valid": True, "origin_valid": True, "authoritative_sources_clean": True, "canonical_location_valid": True, "no_reparse_points": True, "outside_git_and_user_profile": True, "manifest_schema_valid": True, "manifest_complete": True, "manifest_hashes_valid": True, "no_unmanifested_files": True}, "dependencies": {"repository_lock_sha256": H, "active_release_lock_sha256": H, "lock_schema_valid": True, "packages": [pkg("fastapi", "0.141.1"), pkg("uvicorn", "0.52.0"), pkg("tzdata", "2026.3")], "tooling_policy_valid": True, "artifact_provenance": "PASS"}, "scheduled_tasks": {"HealthCheckerCompanionHost": _task("HealthCheckerCompanionHost"), "HealthCheckerCompanionProxy": _task("HealthCheckerCompanionProxy")}, "runtime_health": {"8743/healthz": health("healthz"), "8743/readyz": health("ready"), "8744/healthz": health("healthz"), "8744/readyz": health("ready")}}


def test_valid_public_policy_is_blocked_and_has_no_authentication_flag():
    result = evaluate_evidence(_evidence())
    assert result["exit_code"] == EXIT_BLOCKED
    assert result["evidence_authenticated"] is False
    assert tuple(c["name"] for c in result["checks"]) == MANDATORY_CHECKS
    assert "authenticated" not in inspect.signature(evaluate_evidence).parameters


def test_evidence_cannot_self_authenticate():
    data = _evidence(); data["authenticated"] = True
    assert evaluate_evidence(data)["exit_code"] == EXIT_BLOCKED


@pytest.mark.parametrize("field", ["digest_matches", "digest_stable_across_probe", "no_reparse_points", "no_unsafe_resolution"])
def test_executable_attacks_fail(field):
    data = _evidence(); data["protected_executable"][field] = False
    assert evaluate_evidence(data)["exit_code"] == EXIT_FAIL


def test_access_denied_is_blocked():
    data = _evidence(); data["protected_executable"] = {"access": "BLOCKED"}
    assert evaluate_evidence(data)["exit_code"] == EXIT_BLOCKED


def test_release_identity_head_obsolete_dirty_and_reparse_fail():
    for field, value in (("current_commit", "b" * 40), ("repository_identity_valid", False), ("origin_valid", False), ("authoritative_sources_clean", False), ("no_reparse_points", False)):
        data = _evidence(); data["active_release"][field] = value
        assert evaluate_evidence(data)["exit_code"] == EXIT_FAIL


@pytest.mark.parametrize("field,value", [("executable", "path_python"), ("argument_grammar", "injected"), ("trigger_count", 2), ("principal", "operator"), ("multiple_instances", "Parallel")])
def test_task_attacks_fail(field, value):
    data = _evidence(); data["scheduled_tasks"]["HealthCheckerCompanionHost"][field] = value
    assert evaluate_evidence(data)["exit_code"] == EXIT_FAIL


def test_missing_extra_partial_and_unhealthy_health_fail():
    mutations = [lambda h: h.pop("8744/readyz"), lambda h: h.update({"9999/healthz": {}}), lambda h: h["8743/healthz"].update(schema_exact=False), lambda h: h["8744/readyz"].update(ok=False)]
    for mutate in mutations:
        data = _evidence(); mutate(data["runtime_health"])
        assert evaluate_evidence(data)["exit_code"] == EXIT_FAIL


def test_dependency_duplicate_unexpected_direct_url_lock_and_provenance():
    variants = []
    d = _evidence(); d["dependencies"]["packages"].append(dict(d["dependencies"]["packages"][0])); variants.append((d, EXIT_FAIL))
    d = _evidence(); d["dependencies"]["packages"].append({"name": "surprise", "expected": False, "applicable": True, "version_matches": True, "protected_runtime_location": True}); variants.append((d, EXIT_FAIL))
    d = _evidence(); d["dependencies"]["packages"][0]["direct_url"] = True; variants.append((d, EXIT_FAIL))
    d = _evidence(); d["dependencies"]["active_release_lock_sha256"] = "B" * 64; variants.append((d, EXIT_FAIL))
    d = _evidence(); d["dependencies"]["artifact_provenance"] = "BLOCKED"; variants.append((d, EXIT_BLOCKED))
    assert all(evaluate_evidence(d)["exit_code"] == code for d, code in variants)


def test_duplicate_json_and_redacted_errors(tmp_path: Path, capsys):
    path = tmp_path / "dup.json"; path.write_text('{"schema_version":"a","schema_version":"b"}', encoding="utf-8")
    with pytest.raises(ConfigurationError): load_evidence(path)
    assert main([str(tmp_path / "private-user-token.json")]) == EXIT_INVOCATION
    assert "private-user-token" not in capsys.readouterr().out


def test_public_main_one_json_document(tmp_path: Path, capsys):
    path = tmp_path / "evidence.json"; path.write_text(json.dumps(_evidence()), encoding="utf-8")
    assert main([str(path)]) == EXIT_BLOCKED
    lines = capsys.readouterr().out.splitlines(); assert len(lines) == 1 and json.loads(lines[0])["overall"] == "BLOCKED"


def test_environment_cannot_enable_pass(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setenv("HC_PROTECTED_RUNTIME_AUTHENTICATED", "true")
    path = tmp_path / "evidence.json"; path.write_text(json.dumps(_evidence()), encoding="utf-8")
    assert main([str(path)]) == EXIT_BLOCKED
    assert json.loads(capsys.readouterr().out)["evidence_authenticated"] is False


@pytest.mark.parametrize(
    "value",
    [
        {"x": "x" * (MAX_STRING_LENGTH + 1)},
        {"x": [0] * (MAX_ARRAY_ELEMENTS + 1)},
        {"x": float("nan")},
    ],
)
def test_untrusted_json_limits_are_redacted(tmp_path: Path, capsys, value):
    path = tmp_path / "sensitive-token.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    assert main([str(path)]) == EXIT_INVOCATION
    result = json.loads(capsys.readouterr().out)
    assert result["error"] == "evidence_json_invalid"
    assert "sensitive-token" not in json.dumps(result)


def test_excessive_json_depth_is_rejected(tmp_path: Path):
    value = 0
    for _ in range(MAX_NESTING_DEPTH + 1):
        value = {"x": value}
    path = tmp_path / "deep.json"; path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="evidence_json_invalid"):
        load_evidence(path)


@pytest.mark.parametrize("field", ["action_count", "trigger_count", "restart_count"])
@pytest.mark.parametrize("wrong", [1.0, True])
def test_task_integer_fields_require_exact_int(field, wrong):
    data = _evidence(); data["scheduled_tasks"]["HealthCheckerCompanionHost"][field] = wrong
    assert evaluate_evidence(data)["exit_code"] == EXIT_FAIL


@pytest.mark.parametrize("wrong", [200.0, True])
def test_http_status_requires_exact_int(wrong):
    data = _evidence(); data["runtime_health"]["8743/healthz"]["http_status"] = wrong
    assert evaluate_evidence(data)["exit_code"] == EXIT_FAIL


def test_privileged_boundary_has_no_python_repo_or_environment_bootstrap():
    text = (Path(__file__).resolve().parents[1] / "scripts/operator/Test-ProtectedRuntimeCertification.ps1").read_text(encoding="utf-8").lower()
    assert "python" not in text and "sys.path" not in text and "reporoot" not in text and "$env:" not in text
    assert "convertto-json" not in text and "convertto-json" not in text.replace("-", "")
    assert "write-output" not in text and "write-host" not in text and "|" not in text
    assert "[console]::out.writeline(" in text
    assert "trusted_collector_unavailable" in text and "[environment]::exit(20)" in text


def test_privileged_wrapper_resists_hostile_command_shadowing(tmp_path: Path):
    wrapper = Path(__file__).resolve().parents[1] / "scripts/operator/Test-ProtectedRuntimeCertification.ps1"
    marker = tmp_path / "shadow-invoked.txt"
    escaped_marker = str(marker).replace("'", "''")
    escaped_wrapper = str(wrapper).replace("'", "''")
    hostile = ";".join(
        f"function global:{name} {{ [IO.File]::WriteAllText('{escaped_marker}','invoked') }}"
        for name in ("ConvertTo-Json", "Write-Output", "Write-Host", "python", "powershell")
    )
    command = f"{hostile};& '{escaped_wrapper}'"
    completed = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True,
        text=True,
        timeout=15,
        env=os.environ.copy(),
        check=False,
    )
    expected = '{"checks":[],"error":"trusted_collector_unavailable","evidence_authenticated":false,"exit_code":20,"overall":"BLOCKED","schema_version":"hc.protected_runtime_policy_result.v1"}'
    assert completed.returncode == EXIT_BLOCKED
    assert completed.stdout.splitlines() == [expected]
    assert completed.stderr == ""
    assert not marker.exists()


def test_legacy_verifier_is_a_non_injectable_blocked_shim():
    from backend.health_vault.companion_host import protected_runtime_verifier as legacy

    assert tuple(inspect.signature(legacy.main).parameters) == ("argv",)
    assert not hasattr(legacy, "evaluate_fixture")
    assert not hasattr(legacy, "collect_live")
