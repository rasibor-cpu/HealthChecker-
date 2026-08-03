from __future__ import annotations

import ctypes
from ctypes import wintypes
import inspect
import json
import os
from pathlib import Path
import random
import subprocess
import time
import winreg

import pytest

from backend.health_vault.companion_host.protected_runtime_policy import (
    ConfigurationError,
    evaluate_evidence,
)

ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "scripts/operator/Invoke-SyntheticProtectedRuntimeCollector.ps1"
SYSTEM_POWERSHELL = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
REGISTRY = (
    "repository_commit", "active_release", "release_manifest",
    "installer_provenance", "interpreter_provenance", "dependency_provenance",
    "task_host", "task_proxy", "health_8743_healthz", "health_8743_readyz",
    "health_8744_healthz", "health_8744_readyz",
)
ERROR_JSON = b'{"authentication_status":"unavailable","certification_status":"FAIL","environment":"synthetic","error":"synthetic_configuration_invalid","exit_code":22,"schema_version":"hc.protected_runtime.synthetic_envelope.v1"}'
INPUT_DEADLINE_SECONDS = 10
INPUT_DEADLINE_TOLERANCE = (8.0, 15.0)


def _fixture() -> dict:
    return {
        "schema_version": "hc.protected_runtime.synthetic_fixture.v1",
        "checks": [{"id": check, "status": "PASS", "reason": "ok"} for check in REGISTRY],
    }


def _encoded(value: object | None = None) -> bytes:
    return json.dumps(_fixture() if value is None else value, separators=(",", ":")).encode()


def _command(*extra: str) -> list[str]:
    return [
        str(SYSTEM_POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(COLLECTOR), *extra,
    ]


def _run(payload: bytes | None = None, *extra: str, env: dict[str, str] | None = None, timeout: float = 20) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        _command(*extra), input=_encoded() if payload is None else payload,
        capture_output=True, timeout=timeout, env=env or os.environ.copy(), check=False,
    )


def _assert_error(result: subprocess.CompletedProcess[bytes]) -> None:
    assert result.returncode == 22
    assert result.stderr == b""
    assert result.stdout.splitlines() == [ERROR_JSON]


def test_zero_argument_stdin_is_deterministic_blocked():
    first, second = _run(), _run()
    assert first.returncode == second.returncode == 20
    assert first.stderr == second.stderr == b"" and first.stdout == second.stdout
    assert len(first.stdout.splitlines()) == 1
    body = json.loads(first.stdout)
    assert body["environment"] == "synthetic"
    assert body["authentication_status"] == "unavailable"
    assert body["certification_status"] == "BLOCKED"
    assert [item["id"] for item in body["checks"]] == list(REGISTRY)
    assert "collection" not in body and "signature" not in body and "host_binding" not in body


def test_contradiction_is_fail_and_precedes_blocked():
    value = _fixture()
    value["checks"][0] = {"id": REGISTRY[0], "status": "BLOCKED", "reason": "synthetic_unavailable"}
    value["checks"][1] = {"id": REGISTRY[1], "status": "FAIL", "reason": REGISTRY[1] + "_mismatch"}
    result = _run(_encoded(value))
    assert result.returncode == 21 and result.stderr == b""
    assert json.loads(result.stdout)["certification_status"] == "FAIL"


@pytest.mark.parametrize("args", [("anything",), ("-Live",), ("-SyntheticFixturePath",), ("-X", "value"), ("-X", "a", "-X", "b"), ("--", "value")])
def test_every_argument_form_is_fixed_error(args: tuple[str, ...]):
    _assert_error(_run(_encoded(), *args))


def test_empty_stdin_is_fixed_error():
    _assert_error(_run(b""))


def test_exact_byte_boundary_and_large_stream_are_bounded():
    raw = _encoded()
    exact = raw + b" " * (65536 - len(raw))
    assert len(exact) == 65536 and _run(exact).returncode == 20
    _assert_error(_run(exact + b" "))
    started = time.monotonic(); result = _run(b" " * (8 * 1024 * 1024), timeout=15)
    assert time.monotonic() - started < 15
    _assert_error(result)


def _wait_with_open_stdin(
    prefix: bytes,
    *,
    env: dict[str, str] | None = None,
    command: list[str] | None = None,
) -> tuple[subprocess.CompletedProcess[bytes], float, set[int]]:
    process = subprocess.Popen(
        _command() if command is None else command,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env or os.environ.copy(),
    )
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    process.stdin.write(prefix); process.stdin.flush()
    started = time.monotonic(); observed = set()
    while process.poll() is None and time.monotonic() - started < INPUT_DEADLINE_TOLERANCE[1] + 5:
        observed |= _children(process.pid); time.sleep(0.01)
    elapsed = time.monotonic() - started
    if process.poll() is None:
        process.kill(); pytest.fail("collector did not enforce its total stdin deadline")
    process.stdin.close()
    result = subprocess.CompletedProcess(process.args, process.returncode, process.stdout.read(), process.stderr.read())
    return result, elapsed, observed


def test_partial_open_stdin_has_total_deadline_no_child_or_write(tmp_path: Path):
    repo_before = _repo_snapshot(); state_before = _registry_environment_snapshot(); files_before = set(tmp_path.rglob("*"))
    hostile_env = os.environ.copy(); hostile_env["HC_INPUT_DEADLINE_SECONDS"] = "999999"
    result, elapsed, observed = _wait_with_open_stdin(b'{"schema_version":', env=hostile_env)
    _assert_error(result)
    assert INPUT_DEADLINE_TOLERANCE[0] <= elapsed <= INPUT_DEADLINE_TOLERANCE[1]
    assert observed == set()
    assert repo_before == _repo_snapshot() and state_before == _registry_environment_snapshot()
    assert files_before == set(tmp_path.rglob("*"))


def test_total_deadline_is_not_reset_by_reads_or_hostile_session():
    marker = "deadline-shadow-canary"
    hostile = ";".join(
        f"function global:{name} {{ [Console]::Error.WriteLine('{marker}') }}"
        for name in ("Start-Sleep", "Wait-Process", "Read-Host", "Get-Content", "Write-Output")
    )
    command_text = f"{hostile};& '{str(COLLECTOR).replace("'", "''")}'"
    command = [
        str(SYSTEM_POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-Command", command_text,
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    process.stdin.write(b'{"schema_version":'); process.stdin.flush()
    started = time.monotonic()
    while process.poll() is None and time.monotonic() - started < INPUT_DEADLINE_TOLERANCE[1] + 5:
        try:
            process.stdin.write(b" "); process.stdin.flush()
        except BrokenPipeError:
            break
        time.sleep(0.5)
    elapsed = time.monotonic() - started
    if process.poll() is None:
        process.kill(); pytest.fail("partial reads reset or defeated the total deadline")
    process.stdin.close()
    result = subprocess.CompletedProcess(command, process.returncode, process.stdout.read(), process.stderr.read())
    _assert_error(result)
    assert INPUT_DEADLINE_TOLERANCE[0] <= elapsed <= INPUT_DEADLINE_TOLERANCE[1]
    assert marker.encode() not in result.stdout + result.stderr


def test_slow_complete_input_before_deadline_is_unchanged():
    process = subprocess.Popen(_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdin is not None
    payload = _encoded()
    for offset in range(0, len(payload), 128):
        process.stdin.write(payload[offset:offset + 128]); process.stdin.flush(); time.sleep(0.02)
    stdout, stderr = process.communicate(timeout=INPUT_DEADLINE_SECONDS)
    assert process.returncode == 20 and stderr == b""
    assert json.loads(stdout)["certification_status"] == "BLOCKED"


def test_eof_after_partial_json_is_immediate_redacted_failure():
    started = time.monotonic(); result = _run(b'{"schema_version":')
    assert time.monotonic() - started < INPUT_DEADLINE_TOLERANCE[0]
    _assert_error(result)


def test_fixed_seed_bounded_stdin_fuzz_is_redacted_and_terminates():
    rng = random.Random(30942)
    valid = _encoded()
    payloads = [valid[:rng.randrange(1, len(valid))] for _ in range(4)]
    payloads.extend(b"\xff" + rng.randbytes(rng.randrange(1, 512)) for _ in range(4))
    for payload in payloads:
        started = time.monotonic(); result = _run(payload)
        assert time.monotonic() - started < INPUT_DEADLINE_TOLERANCE[0]
        _assert_error(result)


@pytest.mark.parametrize("raw", [b"{bad", b"\xff\xfe", b'{"x":"line\x01break"}', b"/*x*/{}", _encoded() + b" garbage"])
def test_malformed_utf8_control_comments_and_trailing_data(raw: bytes):
    _assert_error(_run(raw))


def test_one_optional_bom_is_accepted_and_repeated_bom_is_rejected():
    assert _run(b"\xef\xbb\xbf" + _encoded()).returncode == 20
    _assert_error(_run(b"\xef\xbb\xbf\xef\xbb\xbf" + _encoded()))


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema_version":"a","schema_\\u0076ersion":"b"}',
        b'{"x":{"id":"a","\\u0069d":"b"}}',
        b'{"x":"\\uD800"}', b'{"x":"\\uDC00"}', b'{"x":"\\uDC00\\uD800"}',
        b'{"x":"\\uD800\\u0041"}', b'{"x":"\\uD800\\u"}',
    ],
)
def test_escaped_duplicates_and_invalid_surrogates_are_rejected(raw: bytes):
    _assert_error(_run(raw))


def test_valid_surrogate_pair_is_parsed_without_crash_or_leak():
    result = _run(b'{"x":"canary-\\uD83D\\uDE00"}')
    _assert_error(result)
    assert b"canary" not in result.stdout + result.stderr


@pytest.mark.parametrize("raw", [b'{"sequence":1.0}', b'{"sequence":true}', b'{"sequence":1e2}', b'{"sequence":01}', b'{"sequence":9223372036854775808}'])
def test_number_grammar_is_exact(raw: bytes):
    _assert_error(_run(raw))


def test_unknown_missing_duplicate_and_unknown_checks():
    variants = []
    value = _fixture(); value["unexpected"] = True; variants.append(value)
    value = _fixture(); value.pop("checks"); variants.append(value)
    value = _fixture(); value["checks"][-1] = dict(value["checks"][0]); variants.append(value)
    value = _fixture(); value["checks"][-1]["id"] = "unknown_check"; variants.append(value)
    value = _fixture(); value["checks"].pop(); variants.append(value)
    for value in variants: _assert_error(_run(_encoded(value)))


@pytest.mark.parametrize("field", ["authentication_status", "authenticated", "signature", "signer_id", "certificate_id", "certification_status", "timestamp", "nonce", "sequence"])
def test_authentication_signature_pass_and_metadata_fields_are_forbidden(field: str):
    value = _fixture(); value[field] = "secretcanary0123456789"
    result = _run(_encoded(value)); _assert_error(result)
    assert b"secretcanary" not in result.stdout + result.stderr


@pytest.mark.parametrize("canary", ["usernamecanary", "tokenvaluecanary", r"c:\sensitive\path", r"\\server\share", r"\\?\device", "taskargumentcanary", "deviceidentifiercanary", "certificatecanary", "rawhttpbodycanary"])
def test_canaries_in_every_allowed_value_position_never_emit(canary: str):
    for location in ("schema", "id", "status", "reason"):
        value = _fixture()
        if location == "schema": value["schema_version"] = canary
        else: value["checks"][0][location] = canary
        result = _run(_encoded(value)); _assert_error(result)
        assert canary.encode().lower() not in (result.stdout + result.stderr).lower()


def test_depth_member_array_string_container_and_scalar_bounds():
    values = [
        {"x": {"x": {"x": {"x": {"x": {"x": {"x": {"x": {"x": 1}}}}}}}}},
        {str(i): i for i in range(17)}, list(range(17)), {"x": "a" * 257},
        [[[{str(i): i} for i in range(16)] for _ in range(2)] for _ in range(2)],
    ]
    for value in values: _assert_error(_run(_encoded(value)))


def _repo_snapshot() -> dict[str, tuple[int, int]]:
    paths = (COLLECTOR, ROOT / "tests/test_hc309r4d_synthetic_collector.py", ROOT / "docs/governance/HC-309-R4D_SYNTHETIC_COLLECTOR_IMPLEMENTATION.md")
    return {str(path): (path.stat().st_size, path.stat().st_mtime_ns) for path in paths}


def _registry_environment_snapshot() -> tuple[tuple[tuple[str, object, int], ...], dict[str, str]]:
    values = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            index = 0
            while True:
                try: values.append(winreg.EnumValue(key, index)); index += 1
                except OSError: break
    except FileNotFoundError: pass
    return tuple(values), dict(os.environ)


def test_no_repository_testdir_registry_or_persistent_environment_write(tmp_path: Path):
    repo_before = _repo_snapshot(); state_before = _registry_environment_snapshot(); files_before = set(tmp_path.rglob("*"))
    result = _run()
    assert result.returncode == 20
    assert repo_before == _repo_snapshot() and state_before == _registry_environment_snapshot()
    assert files_before == set(tmp_path.rglob("*"))


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]


def _children(parent: int) -> set[int]:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True); snap = kernel.CreateToolhelp32Snapshot(2, 0)
    entry = PROCESSENTRY32W(); entry.dwSize = ctypes.sizeof(entry); found = set()
    try:
        ok = kernel.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.th32ParentProcessID == parent: found.add(entry.th32ProcessID)
            ok = kernel.Process32NextW(snap, ctypes.byref(entry))
    finally: kernel.CloseHandle(snap)
    return found


def test_no_child_process_is_created():
    process = subprocess.Popen(_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdin is not None; process.stdin.write(_encoded()); process.stdin.close()
    observed = set()
    while process.poll() is None:
        observed |= _children(process.pid); time.sleep(0.01)
    stdout, stderr = process.stdout.read(), process.stderr.read()
    assert process.returncode == 20 and stdout and stderr == b"" and observed == set()


def test_hostile_session_does_not_invoke_shadowed_commands():
    marker = "shadow-marker-canary"
    names = ("ConvertFrom-Json", "ConvertTo-Json", "Get-Content", "Set-Content", "Out-File", "Write-Output", "Write-Host", "python", "git", "powershell", "pwsh")
    hostile = ";".join(f"function global:{name} {{ [Console]::Error.WriteLine('{marker}') }}" for name in names)
    command = f"{hostile};& '{str(COLLECTOR).replace("'", "''")}'"
    result = subprocess.run([str(SYSTEM_POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command], input=_encoded(), capture_output=True, timeout=20, check=False)
    assert result.returncode == 20 and result.stderr == b"" and marker.encode() not in result.stdout


def test_non_elevated_execution_and_elevated_gate_status():
    is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    if is_admin:
        _assert_error(_run())
    else:
        assert _run().returncode == 20


def test_exact_future_supported_invocation_status():
    command = [str(SYSTEM_POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(COLLECTOR)]
    result = subprocess.run(command, input=_encoded(), capture_output=True, timeout=20, check=False)
    if result.returncode != 20:
        pytest.skip("BLOCKED: unsigned R4D script cannot run under the host Restricted policy; R4F signing required")
    assert result.stderr == b"" and json.loads(result.stdout)["exit_code"] == 20


@pytest.mark.skipif(not bool(ctypes.windll.shell32.IsUserAnAdmin()), reason="BLOCKED: actual elevated test identity unavailable")
def test_actual_elevated_identity_is_refused():
    _assert_error(_run())


def test_source_has_no_file_live_or_child_process_surface():
    lowered = COLLECTOR.read_text(encoding="utf-8").lower()
    forbidden = (
        "param(", "readallbytes", "[io.file]", "[system.io.file]", "programdata", "device path",
        "get-scheduledtask", "get-process", "get-service", "get-authenticodesignature", "invoke-webrequest",
        "start-process", "system.diagnostics.process", "http://", "https://", "cert:\\", " python", " git ",
        "pwsh", "convertfrom-json", "convertto-json", "get-content", "set-content", "out-file", "write-output",
        "write-host", "$env:", "setenvironmentvariable", "exit(0)", 'certification_status":"pass',
    )
    assert not any(item in lowered for item in forbidden)
    assert "[console]::openstandardinput()" in lowered
    assert "$args.count -ne 0" in lowered


def test_existing_policy_cannot_authenticate_synthetic_fixture():
    with pytest.raises(ConfigurationError): evaluate_evidence(_fixture())
    assert "authenticated" not in inspect.signature(evaluate_evidence).parameters
