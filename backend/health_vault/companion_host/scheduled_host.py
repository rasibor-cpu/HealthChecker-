"""
HC-306E-R2 — Windows Task Scheduler always-on host foundation (policy + packaging).

Architecture (approved): Microsoft Scheduled Tasks — NOT Windows services, NOT NSSM, NOT WinSW.

Privileged tasks must execute only from an immutable ProgramData release copy with a
SHA-256 manifest. Secrets load only from the fixed protected host.env via an allowlisted
parser. Never put secrets in task XML, process arguments, or logs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

# ---------------------------------------------------------------------------
# Rejected third-party service wrappers (historical mentions only)
# ---------------------------------------------------------------------------
REJECTED_SERVICE_WRAPPERS: tuple[str, ...] = ("NSSM", "WinSW")
ACTIVE_ALWAYS_ON_MECHANISM = "windows_task_scheduler"

# ---------------------------------------------------------------------------
# Exact scheduled-task identities (uninstall must target only these)
# ---------------------------------------------------------------------------
TASK_COMPANION_HOST = "HealthCheckerCompanionHost"
TASK_COMPANION_PROXY = "HealthCheckerCompanionProxy"
EXACT_HEALTHCHECKER_TASK_NAMES: frozenset[str] = frozenset(
    {TASK_COMPANION_HOST, TASK_COMPANION_PROXY}
)

# ---------------------------------------------------------------------------
# Fixed host paths (outside Git)
# ---------------------------------------------------------------------------
PROGRAMDATA_ROOT = Path(r"C:\ProgramData\HealthChecker")
RELEASES_ROOT = PROGRAMDATA_ROOT / "releases"
CURRENT_RELEASE_POINTER = RELEASES_ROOT / "CURRENT"
HOST_ENV_PATH = PROGRAMDATA_ROOT / "companion_host" / "host.env"
CADDYFILE_PATH = PROGRAMDATA_ROOT / "companion_host" / "Caddyfile"
LOG_DIR = PROGRAMDATA_ROOT / "logs"
TOOLS_ROOT = PROGRAMDATA_ROOT / "tools"
# Fixed versioned tool paths (must match config/companion_runtime.json; no env override).
FIXED_PYTHON_PATH = TOOLS_ROOT / "python" / "3.12.10" / "python.exe"
FIXED_CADDY_PATH = TOOLS_ROOT / "caddy" / "2.11.4" / "caddy.exe"
FIXED_CADDY_SHA256 = "5CB9AB71E5756CE72840B8234177A2F40C8B4AB47A806B8E841E2B784E9DF62B"

# Ports — never touch CSS / HC-303D reserved listeners from these tasks
COMPANION_PORT_DEFAULT = 8743
PROXY_PORT_DEFAULT = 8744
FORBIDDEN_PORTS: frozenset[int] = frozenset({8765, 8877})

# ---------------------------------------------------------------------------
# Bounded restart / startup policy (Task Scheduler settings contract)
# ---------------------------------------------------------------------------
MULTIPLE_INSTANCES_POLICY = "IgnoreNew"
RESTART_INTERVAL_MINUTES = 1
RESTART_COUNT = 3
PROXY_STARTUP_DELAY_SECONDS = 15
COMPANION_HEALTHZ_TIMEOUT_SECONDS = 60
COMPANION_HEALTHZ_POLL_SECONDS = 2
REBOOT_ON_FAILURE = False
AUTO_CONFIGURE_SERVE = False
AUTO_CONFIGURE_FUNNEL = False
FIREWALL_CHANGES_ALLOWED = False
UVICORN_WORKERS = 1  # companion_host.__main__ uses single-process uvicorn.run

# Approval flags for inert operator templates
APPROVAL_SCHEDULED_HOST = "HC_306E_ALLOW_SCHEDULED_HOST"
APPROVAL_VALUE = "I_UNDERSTAND"

# ---------------------------------------------------------------------------
# host.env allowlist (privileged bootstrap accepts ONLY these keys)
# ---------------------------------------------------------------------------
ALLOWED_HOST_ENV_KEYS: frozenset[str] = frozenset(
    {
        "HC_HOST_ACTIVATION",
        "HC_COMPANION_ADMIN_TOKEN",
        "HC_COMPANION_PEPPER",
        "HC_PROXY_SHARED_TOKEN",
        "HC_MONITORING_VAULT_ROOT",
        "HC_TRUSTED_PROXY_MODE",
        "HC_EXTERNAL_HTTPS_ORIGIN",
        "HC_EXTERNAL_HTTPS_HOST",
        "HC_BIND_HOST",
        "HC_BIND_PORT",
        "HC_PROXY_LISTEN_HOST",
        "HC_PROXY_LISTEN_PORT",
        "HC_TAILSCALE_SERVE_TARGET_PORT",
    }
)

_ENV_KEY_RE = re.compile(r"^HC_[A-Z0-9_]+$")
# Ban PowerShell/cmd injection markers; allow Windows path backslashes in values.
_INJECTION_RE = re.compile(
    r"[;`|&<>]|\$\(|\$env:|`|Invoke-Expression|\biex\b",
    re.IGNORECASE,
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# Packaging exclusions (path fragment / name checks; case-insensitive)
_EXCLUDE_NAME_FRAGMENTS: tuple[str, ...] = (
    ".git",
    "__pycache__",
    ".pyc",
    "tests",
    "test_",
    "docs",
    "android",
    ".apk",
    "vault_storage",
    "private_imports",
    "secrets",
    "logs",
    "node_modules",
    "frontend",
    ".egg-info",
    ".pytest_cache",
    ".mypy_cache",
    "coverage",
    "dist",
    "build",
)

# Relative paths / prefixes included from committed HEAD when packaging a release
RELEASE_INCLUDE_PREFIXES: tuple[str, ...] = (
    "backend/__init__.py",
    "backend/health_vault/",
    "config/companion_runtime.json",
    "requirements/production.in",
    "requirements/production.txt",
)

RELEASE_INCLUDE_SCRIPT_NAMES: tuple[str, ...] = (
    "host_env_loader.ps1.template",
    "bootstrap_companion_host.ps1.template",
    "bootstrap_companion_proxy.ps1.template",
    "Caddyfile.template",
)

MANIFEST_FILENAME = "RELEASE_MANIFEST.json"
SOURCE_COMMIT_FILENAME = "SOURCE_COMMIT.txt"
# Required after .template → .ps1 packaging (fail closed if omitted from manifest)
REQUIRED_RELEASE_REL_PATHS: frozenset[str] = frozenset(
    {
        "scripts/companion_host/bootstrap_companion_host.ps1",
        "scripts/companion_host/bootstrap_companion_proxy.ps1",
        "scripts/companion_host/host_env_loader.ps1",
        "backend/health_vault/companion_host/__main__.py",
        "backend/health_vault/companion_host/scheduled_host.py",
        "backend/health_vault/companion_host/runtime_contract.py",
        "backend/__init__.py",
        "config/companion_runtime.json",
        "requirements/production.txt",
        "requirements/production.in",
    }
)
REASON_CODES: frozenset[str] = frozenset(
    {
        "ok",
        "approval_required",
        "elevation_required",
        "manifest_missing",
        "manifest_mismatch",
        "release_file_missing",
        "release_file_modified",
        "env_path_forbidden",
        "env_parse_failed",
        "env_unknown_key",
        "env_duplicate_key",
        "env_malformed",
        "env_injection_rejected",
        "executable_override_rejected",
        "executable_privilege_risk",
        "healthz_timeout",
        "forbidden_port",
        "nssm_winsw_rejected",
        "git_tree_execution_forbidden",
        "serve_funnel_forbidden",
        "firewall_change_forbidden",
        "task_name_forbidden",
        "vault_delete_forbidden",
    }
)


class ScheduledHostError(ValueError):
    """Fail-closed scheduled-host error with privacy-safe reason code only."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code if code in REASON_CODES else "env_parse_failed"
        super().__init__(message or self.code)


@dataclass(frozen=True)
class HostEnvParseResult:
    values: dict[str, str]
    keys_loaded: tuple[str, ...]


@dataclass(frozen=True)
class ExecutablePrivilegeAssessment:
    path: Path
    acceptable_for_system_task: bool
    reason_code: str
    detail: str


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def parse_host_env_text(text: str) -> HostEnvParseResult:
    """
    Smallest safe host.env parser (Python authority for tests + packaging helpers).

    - Allowlisted HC_* keys only
    - Reject duplicates, unknown keys, blank names, malformed lines
    - Reject controls and command-injection syntax
    - Values are literal strings (no expansion)
    """
    out: dict[str, str] = {}
    order: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip("\r\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if _CONTROL_RE.search(line):
            raise ScheduledHostError("env_malformed", f"control_char_line_{lineno}")
        if "=" not in line:
            raise ScheduledHostError("env_malformed", f"missing_equals_line_{lineno}")
        key, _, value = line.partition("=")
        key = key.strip()
        # Do not strip value interior; only strip a single surrounding quote pair.
        value = value.strip("\r\n")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if not key:
            raise ScheduledHostError("env_malformed", f"blank_name_line_{lineno}")
        if not _ENV_KEY_RE.match(key):
            raise ScheduledHostError("env_malformed", f"bad_key_line_{lineno}")
        if key not in ALLOWED_HOST_ENV_KEYS:
            raise ScheduledHostError("env_unknown_key", key)
        if key in out:
            raise ScheduledHostError("env_duplicate_key", key)
        if _CONTROL_RE.search(value) or "\n" in value or "\r" in value:
            raise ScheduledHostError("env_malformed", f"control_value_line_{lineno}")
        if _INJECTION_RE.search(key) or _INJECTION_RE.search(value):
            raise ScheduledHostError("env_injection_rejected", f"line_{lineno}")
        out[key] = value
        order.append(key)
    return HostEnvParseResult(values=out, keys_loaded=tuple(order))


def parse_host_env_file(path: Path, *, expected_path: Path | None = None) -> HostEnvParseResult:
    expected = (expected_path or HOST_ENV_PATH).resolve()
    resolved = path.resolve()
    if resolved != expected:
        # Tests may pass expected_path=temp; production bootstraps use HOST_ENV_PATH only.
        raise ScheduledHostError("env_path_forbidden")
    try:
        text = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise ScheduledHostError("env_parse_failed") from exc
    return parse_host_env_text(text)


def apply_host_env_to_mapping(
    parsed: HostEnvParseResult, target: dict[str, str] | None = None
) -> dict[str, str]:
    """Apply allowlisted values as literals. Never logs values."""
    dest = target if target is not None else {}
    for key, value in parsed.values.items():
        dest[key] = value
    return dest


def _normalize_rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def path_is_excluded(rel_posix: str) -> bool:
    low = rel_posix.lower()
    parts = low.split("/")
    for frag in _EXCLUDE_NAME_FRAGMENTS:
        f = frag.lower()
        if f in parts:
            return True
        if f.startswith(".") and any(p.endswith(f) or p == f.lstrip(".") for p in parts):
            # handle .pyc file suffix
            if f.startswith(".") and any(p.endswith(f) for p in parts):
                return True
    if low.endswith(".pyc") or low.endswith(".apk"):
        return True
    # Exclude test modules by filename
    name = parts[-1] if parts else ""
    if name.startswith("test_") and name.endswith(".py"):
        return True
    return False


def iter_release_source_files(repo_root: Path) -> list[Path]:
    """Return committed runtime files approved for immutable release packaging."""
    root = repo_root.resolve()
    selected: list[Path] = []
    for prefix in RELEASE_INCLUDE_PREFIXES:
        candidate = root / prefix
        if candidate.is_file():
            rel = _normalize_rel(candidate, root)
            if not path_is_excluded(rel):
                selected.append(candidate)
            continue
        if candidate.is_dir():
            for path in sorted(candidate.rglob("*")):
                if not path.is_file():
                    continue
                if path.suffix.lower() not in {".py", ".pyi"} and "health_vault" in prefix:
                    # Only Python runtime modules under health_vault
                    if path.suffix.lower() != ".py":
                        continue
                rel = _normalize_rel(path, root)
                if path_is_excluded(rel):
                    continue
                if not rel.endswith(".py"):
                    continue
                selected.append(path)
    scripts_dir = root / "scripts" / "companion_host"
    for name in RELEASE_INCLUDE_SCRIPT_NAMES:
        path = scripts_dir / name
        if path.is_file():
            selected.append(path)
    # Deduplicate while preserving order
    seen: set[str] = set()
    out: list[Path] = []
    for path in selected:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def build_release_manifest(
    *,
    repo_root: Path,
    source_commit: str,
    files: Iterable[Path] | None = None,
) -> dict[str, Any]:
    root = repo_root.resolve()
    commit = (source_commit or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ScheduledHostError("manifest_mismatch", "source_commit_invalid")
    entries: dict[str, str] = {}
    for path in files if files is not None else iter_release_source_files(root):
        rel = _normalize_rel(path, root)
        if path_is_excluded(rel):
            continue
        entries[rel] = sha256_file(path)
    return {
        "schema_version": "hc.scheduled_host.release.v1",
        "source_commit": commit,
        "mechanism": ACTIVE_ALWAYS_ON_MECHANISM,
        "rejected_wrappers": list(REJECTED_SERVICE_WRAPPERS),
        "files": dict(sorted(entries.items())),
    }


def write_release_copy(
    *,
    repo_root: Path,
    source_commit: str,
    dest_root: Path,
    files: Iterable[Path] | None = None,
    require_clean_commit: bool = False,
) -> Path:
    """
    Copy approved runtime files into dest_root/<commit>/ and write manifest.

    Does not touch ProgramData unless dest_root points there. Tests use TEMP.
    Strips .template suffix when copying bootstrap scripts into release.
    When require_clean_commit=True, refuse dirty/mismatched HEAD packaging.
    """
    root = repo_root.resolve()
    commit = (source_commit or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ScheduledHostError("manifest_mismatch", "source_commit_invalid")
    if require_clean_commit:
        assert_packaging_matches_commit(root, commit)
    release_dir = dest_root.resolve() / commit
    if release_dir.exists():
        shutil.rmtree(release_dir)
    release_dir.mkdir(parents=True, exist_ok=True)

    source_files = list(files) if files is not None else iter_release_source_files(root)
    copied: dict[str, str] = {}
    for src in source_files:
        rel = _normalize_rel(src, root)
        if path_is_excluded(rel):
            continue
        # Place scripts without .template suffix for fixed bootstrap execution
        dest_rel = rel
        if dest_rel.endswith(".ps1.template"):
            dest_rel = dest_rel[: -len(".template")]
        elif dest_rel.endswith("Caddyfile.template"):
            dest_rel = dest_rel[: -len(".template")]
        dest = release_dir / dest_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        copied[dest_rel.replace("\\", "/")] = sha256_file(dest)

    manifest = {
        "schema_version": "hc.scheduled_host.release.v1",
        "source_commit": commit,
        "mechanism": ACTIVE_ALWAYS_ON_MECHANISM,
        "rejected_wrappers": list(REJECTED_SERVICE_WRAPPERS),
        "files": dict(sorted(copied.items())),
    }
    (release_dir / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (release_dir / SOURCE_COMMIT_FILENAME).write_text(commit + "\n", encoding="utf-8")
    return release_dir


def _safe_join_under_release(release: Path, rel: str) -> Path:
    """Join a relative manifest path; reject absolute / traversal / escape."""
    if not isinstance(rel, str) or not rel.strip():
        raise ScheduledHostError("manifest_mismatch")
    norm = rel.replace("\\", "/").strip()
    if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm):
        raise ScheduledHostError("manifest_mismatch")
    parts = [p for p in norm.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise ScheduledHostError("manifest_mismatch")
    release_res = release.resolve()
    candidate = release_res.joinpath(*parts)
    resolved = candidate.resolve()
    try:
        resolved.relative_to(release_res)
    except ValueError as exc:
        raise ScheduledHostError("manifest_mismatch") from exc
    return resolved


def _path_or_ancestor_is_symlink(path: Path, *, stop_at: Path | None = None) -> bool:
    """True if path or any ancestor (down to stop_at) is a symlink/reparse."""
    cur = Path(os.path.abspath(str(path)))
    stop = Path(os.path.abspath(str(stop_at))) if stop_at is not None else None
    while True:
        try:
            if cur.is_symlink():
                return True
        except OSError:
            return True
        if stop is not None and cur == stop:
            break
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return False


def verify_release_manifest(release_dir: Path) -> dict[str, Any]:
    """Fail closed if manifest missing, file missing, hash mismatch, or incomplete."""
    release = release_dir.resolve()
    manifest_path = release / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise ScheduledHostError("manifest_missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScheduledHostError("manifest_mismatch") from exc
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ScheduledHostError("manifest_mismatch")
    present = {str(k).replace("\\", "/") for k in files.keys()}
    missing_required = REQUIRED_RELEASE_REL_PATHS - present
    if missing_required:
        raise ScheduledHostError("manifest_mismatch")
    for rel, expected_hash in files.items():
        if not isinstance(rel, str) or not isinstance(expected_hash, str):
            raise ScheduledHostError("manifest_mismatch")
        path = _safe_join_under_release(release, rel)
        if not path.is_file():
            raise ScheduledHostError("release_file_missing")
        if path.is_symlink() or _path_or_ancestor_is_symlink(path, stop_at=release):
            raise ScheduledHostError("release_file_modified")
        actual = sha256_file(path)
        if actual.upper() != expected_hash.strip().upper():
            raise ScheduledHostError("release_file_modified")
    commit_file = release / SOURCE_COMMIT_FILENAME
    if commit_file.is_file():
        recorded = commit_file.read_text(encoding="utf-8").strip().lower()
        expected_commit = str(manifest.get("source_commit", "")).strip().lower()
        if recorded != expected_commit:
            raise ScheduledHostError("manifest_mismatch")
    return manifest


def assert_release_dir_location(release_dir: Path) -> Path:
    """Privileged runtime release dirs must live under ProgramData releases root."""
    release = release_dir.resolve()
    root = RELEASES_ROOT.resolve()
    try:
        release.relative_to(root)
    except ValueError as exc:
        raise ScheduledHostError("git_tree_execution_forbidden") from exc
    if release == root:
        raise ScheduledHostError("git_tree_execution_forbidden")
    return release


def assert_packaging_matches_commit(repo_root: Path, source_commit: str) -> str:
    """
    Fail closed if packaging commit is not HEAD, or release-tracked paths are dirty.

    Prevents packaging mutable working-tree edits as a 'verified' commit release.
    """
    import subprocess

    commit = (source_commit or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ScheduledHostError("manifest_mismatch", "source_commit_invalid")
    root = repo_root.resolve()
    head = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        text=True,
    ).strip().lower()
    if head != commit:
        raise ScheduledHostError("manifest_mismatch", "head_mismatch")
    # Dirty check limited to paths that would be packaged
    rels = [_normalize_rel(p, root) for p in iter_release_source_files(root)]
    if not rels:
        raise ScheduledHostError("manifest_mismatch", "empty_release_set")
    porcelain = subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain", "--"] + rels,
        text=True,
    ).strip()
    if porcelain:
        raise ScheduledHostError("manifest_mismatch", "dirty_release_sources")
    return commit


def assert_not_git_working_tree(execution_root: Path, repo_root: Path) -> None:
    """Privileged tasks must not execute from the mutable Git working tree."""
    exe = execution_root.resolve()
    repo = repo_root.resolve()
    try:
        exe.relative_to(repo)
    except ValueError:
        return
    # Allow only if release copy lives outside repo; any path under repo is forbidden.
    raise ScheduledHostError("git_tree_execution_forbidden")


def assert_fixed_executable(path: Path, *, allowed: Path) -> Path:
    """
    Launch only the fixed approved executable under ProgramData tools.

    Rejects path overrides, missing files, symlinks/reparse points, and
    resolutions that escape TOOLS_ROOT (symlink-to-user-profile attack).
    """
    literal = Path(os.path.abspath(str(path)))
    allowed_literal = Path(os.path.abspath(str(allowed)))
    if literal != allowed_literal:
        raise ScheduledHostError("executable_override_rejected")
    tools = Path(os.path.abspath(str(TOOLS_ROOT)))
    try:
        literal.relative_to(tools)
        allowed_literal.relative_to(tools)
    except ValueError as exc:
        raise ScheduledHostError("executable_privilege_risk") from exc
    if _path_or_ancestor_is_symlink(literal, stop_at=tools):
        raise ScheduledHostError("executable_privilege_risk")
    if not literal.is_file() or literal.is_symlink():
        raise ScheduledHostError("executable_override_rejected")
    resolved = literal.resolve()
    try:
        resolved.relative_to(TOOLS_ROOT.resolve())
    except ValueError as exc:
        raise ScheduledHostError("executable_privilege_risk") from exc
    if path_looks_user_profile_writable(resolved):
        raise ScheduledHostError("executable_privilege_risk")
    assessment = assess_executable_for_system_task(resolved)
    if not assessment.acceptable_for_system_task:
        raise ScheduledHostError("executable_privilege_risk")
    return resolved


def path_looks_user_profile_writable(path: Path) -> bool:
    low = str(path.resolve()).lower().replace("/", "\\")
    markers = (
        "\\users\\",
        "\\appdata\\local\\",
        "\\appdata\\roaming\\",
        "\\localappdata\\",
    )
    return any(m in low for m in markers)


def assess_executable_for_system_task(path: Path) -> ExecutablePrivilegeAssessment:
    """
    If interpreter/deps are user-writable while tasks run as SYSTEM, refuse install.

    Acceptable: under C:\\ProgramData\\HealthChecker\\tools\\ (operator-staged, ACL-locked).
    Symlinks/reparse points that escape tools are rejected.
    """
    tools = Path(os.path.abspath(str(TOOLS_ROOT)))
    literal = Path(os.path.abspath(str(path)))
    try:
        literal.relative_to(tools)
        under_tools_literal = True
    except ValueError:
        under_tools_literal = False
    if not under_tools_literal or _path_or_ancestor_is_symlink(literal, stop_at=tools):
        return ExecutablePrivilegeAssessment(
            path=literal,
            acceptable_for_system_task=False,
            reason_code="executable_privilege_risk",
            detail="symlink_or_outside_tools",
        )
    if not literal.is_file() or literal.is_symlink():
        return ExecutablePrivilegeAssessment(
            path=literal,
            acceptable_for_system_task=False,
            reason_code="executable_privilege_risk",
            detail="executable_missing_or_symlink",
        )
    resolved = literal.resolve()
    try:
        resolved.relative_to(TOOLS_ROOT.resolve())
        under_tools = True
    except ValueError:
        under_tools = False
    if path_looks_user_profile_writable(resolved) or not under_tools:
        return ExecutablePrivilegeAssessment(
            path=resolved,
            acceptable_for_system_task=False,
            reason_code="executable_privilege_risk",
            detail="user_profile_or_non_programdata_tools",
        )
    return ExecutablePrivilegeAssessment(
        path=resolved,
        acceptable_for_system_task=True,
        reason_code="ok",
        detail="under_programdata_tools",
    )


def task_action_arguments_are_secret_free(arguments: str) -> bool:
    """Reject argument strings that appear to embed secret-like material."""
    low = (arguments or "").lower()
    banned = (
        "hc_companion_admin_token",
        "hc_companion_pepper",
        "hc_proxy_shared_token",
        "token=",
        "-token ",
        "pepper=",
        "password",
        "begin private key",
    )
    return not any(b in low for b in banned)


def scheduled_task_settings_contract() -> dict[str, Any]:
    """Documented Task Scheduler settings for inert install templates + tests."""
    return {
        "multiple_instances": MULTIPLE_INSTANCES_POLICY,
        "restart_interval_minutes": RESTART_INTERVAL_MINUTES,
        "restart_count": RESTART_COUNT,
        "reboot_on_failure": REBOOT_ON_FAILURE,
        "proxy_startup_delay_seconds": PROXY_STARTUP_DELAY_SECONDS,
        "companion_healthz_timeout_seconds": COMPANION_HEALTHZ_TIMEOUT_SECONDS,
        "companion_healthz_poll_seconds": COMPANION_HEALTHZ_POLL_SECONDS,
        "auto_configure_serve": AUTO_CONFIGURE_SERVE,
        "auto_configure_funnel": AUTO_CONFIGURE_FUNNEL,
        "firewall_changes_allowed": FIREWALL_CHANGES_ALLOWED,
        "forbidden_ports": sorted(FORBIDDEN_PORTS),
        "companion_bind": f"127.0.0.1:{COMPANION_PORT_DEFAULT}",
        "proxy_bind": f"127.0.0.1:{PROXY_PORT_DEFAULT}",
        "uvicorn_workers": UVICORN_WORKERS,
        "startup_order": [TASK_COMPANION_HOST, TASK_COMPANION_PROXY],
        "shutdown_order": [TASK_COMPANION_PROXY, TASK_COMPANION_HOST],
        "exact_task_names": sorted(EXACT_HEALTHCHECKER_TASK_NAMES),
        "mechanism": ACTIVE_ALWAYS_ON_MECHANISM,
        "rejected_wrappers": list(REJECTED_SERVICE_WRAPPERS),
        "host_env_path": str(HOST_ENV_PATH),
        "fixed_python_path": str(FIXED_PYTHON_PATH),
        "fixed_caddy_path": str(FIXED_CADDY_PATH),
        "releases_root": str(RELEASES_ROOT),
    }


def assert_uninstall_task_name_allowed(name: str) -> str:
    n = (name or "").strip()
    if n not in EXACT_HEALTHCHECKER_TASK_NAMES:
        raise ScheduledHostError("task_name_forbidden")
    return n


def public_policy_dict() -> dict[str, Any]:
    """Non-secret policy summary for docs/tests."""
    return {
        **scheduled_task_settings_contract(),
        "allowed_host_env_keys": sorted(ALLOWED_HOST_ENV_KEYS),
        "approval_env": APPROVAL_SCHEDULED_HOST,
        "approval_value": APPROVAL_VALUE,
        "python_version": "3.12.10",
        "caddy_version": "2.11.4",
        "caddy_sha256": FIXED_CADDY_SHA256,
    }


def assert_fixed_paths_match_runtime_contract(repo_root: Path | None = None) -> None:
    """Fail closed if scheduled_host fixed paths diverge from companion_runtime.json."""
    from backend.health_vault.companion_host.runtime_contract import load_runtime_contract

    contract = load_runtime_contract(repo_root)
    if Path(contract.python_exe) != FIXED_PYTHON_PATH:
        raise ScheduledHostError("manifest_mismatch")
    if Path(contract.caddy_exe) != FIXED_CADDY_PATH:
        raise ScheduledHostError("manifest_mismatch")
    if contract.caddy_sha256.upper() != FIXED_CADDY_SHA256:
        raise ScheduledHostError("manifest_mismatch")


def filter_os_environ_allowlist(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return only allowlisted HC_* keys from an environ mapping (literal copy)."""
    src = environ if environ is not None else os.environ
    out: dict[str, str] = {}
    for key in ALLOWED_HOST_ENV_KEYS:
        if key in src:
            out[key] = str(src[key])
    return out
