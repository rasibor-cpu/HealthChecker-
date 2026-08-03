"""Unprivileged HC-309 protected-runtime evidence policy evaluation only."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA = "hc.protected_runtime_evidence.v1"
RESULT_SCHEMA = "hc.protected_runtime_policy_result.v1"
EXIT_BLOCKED, EXIT_FAIL, EXIT_INVOCATION = 20, 21, 22
MAX_EVIDENCE_BYTES = 1024 * 1024
MAX_NESTING_DEPTH = 12
MAX_CONTAINERS = 64
MAX_OBJECT_MEMBERS = 64
MAX_ARRAY_ELEMENTS = 64
MAX_STRING_LENGTH = 256
MAX_SCALARS = 512
HOST, PROXY = "HealthCheckerCompanionHost", "HealthCheckerCompanionProxy"
TASKS = (HOST, PROXY)
HEALTH = ("8743/healthz", "8743/readyz", "8744/healthz", "8744/readyz")
MANDATORY_CHECKS = (
    "evidence_authentication", "runtime_contract", "protected_executable",
    "interpreter_identity", "active_release_binding", "release_manifest",
    "dependency_lock", "dependency_versions", "dependency_artifact_provenance",
    f"task:{HOST}", f"task:{PROXY}", *(f"health:{name}" for name in HEALTH),
)
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_HASH = re.compile(r"^[0-9A-Fa-f]{64}$")
_NAME = re.compile(r"[-_.]+")


class ConfigurationError(ValueError):
    pass


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ConfigurationError("duplicate_json_key")
        out[key] = value
    return out


def _reject_non_finite(_: str) -> None:
    raise ConfigurationError("evidence_number_invalid")


def _validate_structure(value: Any) -> None:
    containers = 0
    scalars = 0

    def visit(item: Any, depth: int) -> None:
        nonlocal containers, scalars
        if depth > MAX_NESTING_DEPTH:
            raise ConfigurationError("evidence_structure_invalid")
        if isinstance(item, dict):
            containers += 1
            if containers > MAX_CONTAINERS or len(item) > MAX_OBJECT_MEMBERS:
                raise ConfigurationError("evidence_structure_invalid")
            for key, child in item.items():
                if len(key) > MAX_STRING_LENGTH:
                    raise ConfigurationError("evidence_structure_invalid")
                visit(child, depth + 1)
            return
        if isinstance(item, list):
            containers += 1
            if containers > MAX_CONTAINERS or len(item) > MAX_ARRAY_ELEMENTS:
                raise ConfigurationError("evidence_structure_invalid")
            for child in item:
                visit(child, depth + 1)
            return
        scalars += 1
        if scalars > MAX_SCALARS:
            raise ConfigurationError("evidence_structure_invalid")
        if isinstance(item, str) and len(item) > MAX_STRING_LENGTH:
            raise ConfigurationError("evidence_structure_invalid")
        if isinstance(item, float) or not isinstance(item, (str, int, bool, type(None))):
            raise ConfigurationError("evidence_number_invalid")

    visit(value, 1)


def load_evidence(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > MAX_EVIDENCE_BYTES:
            raise ConfigurationError("evidence_oversized")
        raw = path.read_bytes()
    except ConfigurationError:
        raise
    except MemoryError as exc:
        raise ConfigurationError("evidence_unreadable") from exc
    except OSError as exc:
        raise ConfigurationError("evidence_unreadable") from exc
    if len(raw) > MAX_EVIDENCE_BYTES:
        raise ConfigurationError("evidence_oversized")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates, parse_constant=_reject_non_finite)
        _validate_structure(value)
    except ConfigurationError as exc:
        raise ConfigurationError("evidence_json_invalid") from exc
    except (UnicodeError, json.JSONDecodeError, RecursionError, MemoryError) as exc:
        raise ConfigurationError("evidence_json_invalid") from exc
    if not isinstance(value, dict):
        raise ConfigurationError("evidence_root_invalid")
    return value


def _mapping(value: Any, reason: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationError(reason)
    return value


def _check(name: str, status: str, reason: str) -> dict[str, str]:
    if name not in MANDATORY_CHECKS or status not in {"PASS", "BLOCKED", "FAIL"}:
        raise ConfigurationError("check_invalid")
    return {"name": name, "status": status, "reason": reason}


def _runtime(data: dict[str, Any]) -> dict[str, str]:
    v = _mapping(data.get("runtime_contract"), "runtime_contract_invalid")
    ok = v.get("schema_valid") is True and v.get("implementation") == "CPython" and v.get("version") == "3.12.10" and v.get("architecture") == "win_amd64" and v.get("fixed_path_contract") is True and bool(_HASH.fullmatch(str(v.get("executable_sha256", ""))))
    return _check("runtime_contract", "PASS" if ok else "FAIL", "ok" if ok else "runtime_contract_mismatch")


def _executable(data: dict[str, Any]) -> list[dict[str, str]]:
    v = _mapping(data.get("protected_executable"), "protected_executable_invalid")
    if v.get("access") == "BLOCKED":
        return [_check("protected_executable", "BLOCKED", "protected_executable_inaccessible"), _check("interpreter_identity", "BLOCKED", "interpreter_identity_unavailable")]
    safe = all(v.get(k) is True for k in ("exists", "fixed_path_valid", "no_reparse_points", "no_unsafe_resolution", "digest_matches", "digest_stable_across_probe"))
    identity = v.get("executed_after_digest_validation") is True and v.get("implementation") == "CPython" and v.get("version") == "3.12.10" and v.get("architecture") in {"AMD64", "x86_64"}
    return [_check("protected_executable", "PASS" if safe else "FAIL", "ok" if safe else "protected_executable_mismatch"), _check("interpreter_identity", "PASS" if identity else "FAIL", "ok" if identity else "interpreter_identity_mismatch")]


def _release(data: dict[str, Any]) -> list[dict[str, str]]:
    v = _mapping(data.get("active_release"), "active_release_invalid")
    if v.get("access") == "BLOCKED":
        return [_check("active_release_binding", "BLOCKED", "active_release_inaccessible"), _check("release_manifest", "BLOCKED", "release_manifest_inaccessible")]
    commits = [str(v.get(k, "")).lower() for k in ("expected_commit", "repository_head", "current_commit", "directory_commit", "manifest_commit")]
    bound = all(_COMMIT.fullmatch(x) for x in commits) and len(set(commits)) == 1 and all(v.get(k) is True for k in ("repository_identity_valid", "origin_valid", "authoritative_sources_clean", "canonical_location_valid", "no_reparse_points", "outside_git_and_user_profile"))
    manifest = all(v.get(k) is True for k in ("manifest_schema_valid", "manifest_complete", "manifest_hashes_valid", "no_unmanifested_files"))
    return [_check("active_release_binding", "PASS" if bound else "FAIL", "ok" if bound else "active_release_binding_mismatch"), _check("release_manifest", "PASS" if manifest else "FAIL", "ok" if manifest else "release_manifest_mismatch")]


def _dependencies(data: dict[str, Any]) -> list[dict[str, str]]:
    v = _mapping(data.get("dependencies"), "dependencies_invalid")
    if v.get("access") == "BLOCKED":
        return [_check("dependency_lock", "BLOCKED", "dependency_evidence_inaccessible"), _check("dependency_versions", "BLOCKED", "dependency_evidence_inaccessible"), _check("dependency_artifact_provenance", "BLOCKED", "artifact_provenance_unavailable")]
    rh, ah = str(v.get("repository_lock_sha256", "")), str(v.get("active_release_lock_sha256", ""))
    lock_ok = bool(_HASH.fullmatch(rh) and rh.upper() == ah.upper() and v.get("lock_schema_valid") is True)
    packages = v.get("packages")
    if not isinstance(packages, list):
        raise ConfigurationError("dependency_packages_invalid")
    seen: set[str] = set(); versions_ok = True; tzdata_ok = False
    for p in packages:
        if not isinstance(p, dict) or not isinstance(p.get("name"), str):
            raise ConfigurationError("dependency_package_invalid")
        name = _NAME.sub("-", p["name"].strip().lower())
        if not name or name in seen: versions_ok = False
        seen.add(name)
        if p.get("applicable") is False: continue
        if p.get("expected") is not True and name not in {"pip", "setuptools", "wheel"}: versions_ok = False
        if p.get("version_matches") is not True or p.get("protected_runtime_location") is not True or p.get("editable") is True or p.get("direct_url") is True: versions_ok = False
        if name == "tzdata" and p.get("version") == "2026.3" and p.get("version_matches") is True: tzdata_ok = True
    versions_ok = versions_ok and tzdata_ok and v.get("tooling_policy_valid") is True
    provenance = v.get("artifact_provenance")
    ps = "PASS" if provenance == "PASS" else "BLOCKED" if provenance == "BLOCKED" else "FAIL"
    return [_check("dependency_lock", "PASS" if lock_ok else "FAIL", "ok" if lock_ok else "dependency_lock_mismatch"), _check("dependency_versions", "PASS" if versions_ok else "FAIL", "ok" if versions_ok else "dependency_environment_mismatch"), _check("dependency_artifact_provenance", ps, "ok" if ps == "PASS" else "artifact_provenance_unavailable" if ps == "BLOCKED" else "artifact_provenance_mismatch")]


def _tasks(data: dict[str, Any]) -> list[dict[str, str]]:
    values = _mapping(data.get("scheduled_tasks"), "scheduled_tasks_invalid")
    if set(values) != set(TASKS): return [_check(f"task:{n}", "FAIL", "scheduled_task_set_mismatch") for n in TASKS]
    out = []
    for name in TASKS:
        v = _mapping(values[name], "scheduled_task_invalid")
        if v.get("access") == "BLOCKED": out.append(_check(f"task:{name}", "BLOCKED", "scheduled_task_inaccessible")); continue
        bootstrap, delay = (("companion_host", "PT0S") if name == HOST else ("companion_proxy", "PT15S"))
        ok = v.get("reported_name") == name and v.get("principal") == "SYSTEM" and v.get("enabled") is True and v.get("state") in {"Ready", "Running"} and type(v.get("action_count")) is int and v.get("action_count") == 1 and v.get("executable") == "trusted_system_powershell" and v.get("argument_grammar") == "strict_file_only" and v.get("bootstrap") == bootstrap and v.get("no_trailing_arguments") is True and v.get("no_environment_expansion") is True and v.get("working_directory") == "active_immutable_release" and type(v.get("trigger_count")) is int and v.get("trigger_count") == 1 and v.get("trigger") == "AtStartup" and v.get("trigger_delay") == delay and v.get("multiple_instances") == "IgnoreNew" and type(v.get("restart_count")) is int and v.get("restart_count") == 3 and v.get("restart_interval") == "PT1M"
        out.append(_check(f"task:{name}", "PASS" if ok else "FAIL", "ok" if ok else "scheduled_task_contract_mismatch"))
    return out


def _health(data: dict[str, Any]) -> list[dict[str, str]]:
    values = _mapping(data.get("runtime_health"), "runtime_health_invalid"); exact = set(values) == set(HEALTH); out = []
    for name in HEALTH:
        v = values.get(name)
        if not exact or not isinstance(v, dict): out.append(_check(f"health:{name}", "FAIL", "runtime_health_set_mismatch")); continue
        if v.get("access") == "BLOCKED": out.append(_check(f"health:{name}", "BLOCKED", "runtime_health_inaccessible")); continue
        status = "ready" if name.endswith("readyz") else "healthz"
        ok = type(v.get("http_status")) is int and v.get("http_status") == 200 and v.get("ok") is True and v.get("status") == status and v.get("schema_exact") is True and v.get("response_within_limit") is True and v.get("bounded_timeout_used") is True
        out.append(_check(f"health:{name}", "PASS" if ok else "FAIL", "ok" if ok else "runtime_unhealthy"))
    return out


def evaluate_evidence(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("schema_version") != SCHEMA: raise ConfigurationError("evidence_schema_invalid")
    checks = [_check("evidence_authentication", "BLOCKED", "trusted_collector_unavailable"), _runtime(data), *_executable(data), *_release(data), *_dependencies(data), *_tasks(data), *_health(data)]
    names = tuple(c["name"] for c in checks)
    if names != MANDATORY_CHECKS or len(names) != len(set(names)): raise ConfigurationError("mandatory_check_registry_violation")
    statuses = {c["status"] for c in checks}
    overall = "FAIL" if "FAIL" in statuses else "BLOCKED"
    code = EXIT_FAIL if overall == "FAIL" else EXIT_BLOCKED
    return {"schema_version": RESULT_SCHEMA, "overall": overall, "exit_code": code, "evidence_authenticated": False, "checks": checks}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if len(args) != 1: raise ConfigurationError("evidence_path_required")
        result = evaluate_evidence(load_evidence(Path(args[0])))
    except ConfigurationError as exc:
        result = {"schema_version": RESULT_SCHEMA, "overall": "FAIL", "exit_code": EXIT_INVOCATION, "evidence_authenticated": False, "checks": [], "error": str(exc)}
    print(json.dumps(result, sort_keys=True, separators=(",", ":"))); return int(result["exit_code"])


if __name__ == "__main__": raise SystemExit(main())
