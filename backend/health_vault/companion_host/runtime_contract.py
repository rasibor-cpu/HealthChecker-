"""
HC-306F-R1 — Companion Host production runtime contract (fail-closed).

Loads config/companion_runtime.json and validates Python/Caddy fixed paths,
hashed production lock integrity, and dependency policy. Environment variables
must never override trusted executable paths.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONTRACT_REL = Path("config") / "companion_runtime.json"
PRODUCTION_IN_REL = Path("requirements") / "production.in"
PRODUCTION_LOCK_REL = Path("requirements") / "production.txt"

_SCHEMA = "hc.companion_runtime.v1"
_PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\\\s#]+)\s*(?:\\)?\s*$")
_HASH_RE = re.compile(r"--hash=sha256:([0-9a-f]{64})", re.IGNORECASE)
_UNSAFE_REQ_RE = re.compile(
    r"""(?ix)
    (^|\s)(-e|--editable)\b
    | \bgit\+
    | \bhg\+
    | \bsvn\+
    | \bfile:
    | https?://
    | @[^\s]*#
    | \[[^\]]*\]\s*@
    """
)


class RuntimeContractError(ValueError):
    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class CompanionRuntimeContract:
    raw: dict[str, Any]
    repo_root: Path

    @property
    def python_version(self) -> str:
        return str(self.raw["python"]["version"])

    @property
    def python_exe(self) -> Path:
        return Path(str(self.raw["python"]["programdata_exe"]))

    @property
    def caddy_exe(self) -> Path:
        return Path(str(self.raw["caddy"]["programdata_exe"]))

    @property
    def caddy_sha256(self) -> str:
        return str(self.raw["caddy"]["sha256"]).upper()

    @property
    def tools_root(self) -> Path:
        return Path(str(self.raw["tools_root"]))

    @property
    def lock_path(self) -> Path:
        return self.repo_root / str(self.raw["dependencies"]["requirements_lock"])

    @property
    def requirements_in_path(self) -> Path:
        return self.repo_root / str(self.raw["dependencies"]["requirements_in"])

    @property
    def direct_pins(self) -> tuple[str, ...]:
        return tuple(str(x) for x in self.raw["dependencies"]["direct_pins"])

    @property
    def forbidden_packages(self) -> frozenset[str]:
        return frozenset(str(x).lower() for x in self.raw["dependencies"]["forbidden_packages"])

    @property
    def pip_install_args(self) -> tuple[str, ...]:
        return tuple(str(x) for x in self.raw["dependencies"]["pip_install_args"])


def default_repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_runtime_contract(repo_root: Path | None = None) -> CompanionRuntimeContract:
    root = (repo_root or default_repo_root()).resolve()
    path = root / CONTRACT_REL
    if not path.is_file():
        raise RuntimeContractError("runtime_contract_missing")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeContractError("runtime_contract_invalid") from exc
    validate_runtime_contract_schema(raw)
    contract = CompanionRuntimeContract(raw=raw, repo_root=root)
    validate_runtime_paths(contract)
    validate_production_lock(contract)
    return contract


def validate_runtime_contract_schema(raw: dict[str, Any]) -> None:
    if raw.get("schema_version") != _SCHEMA:
        raise RuntimeContractError("runtime_contract_schema_invalid")
    for key in ("python", "caddy", "dependencies", "tools_root", "path_policy"):
        if key not in raw:
            raise RuntimeContractError("runtime_contract_schema_invalid")
    py = raw["python"]
    if py.get("implementation") != "CPython":
        raise RuntimeContractError("python_implementation_unsupported")
    if py.get("arch") != "win_amd64":
        raise RuntimeContractError("python_arch_unsupported")
    ver = str(py.get("version", ""))
    if not re.fullmatch(r"3\.12\.\d+", ver):
        raise RuntimeContractError("python_version_unsupported")
    installer = py.get("installer") or {}
    if not installer.get("filename") or not installer.get("url") or not installer.get("md5"):
        raise RuntimeContractError("python_installer_provenance_incomplete")
    if "latest" in str(installer.get("url", "")).lower():
        raise RuntimeContractError("python_installer_url_floating")
    if str(installer.get("filename")) != f"python-{ver}-amd64.exe":
        raise RuntimeContractError("python_installer_filename_mismatch")
    deps = raw["dependencies"]
    if not deps.get("requires_tzdata"):
        raise RuntimeContractError("tzdata_required")
    args = [str(a) for a in deps.get("pip_install_args") or []]
    if "--require-hashes" not in args or "--only-binary=:all:" not in args:
        raise RuntimeContractError("pip_hash_binary_required")
    policy = raw["path_policy"]
    if policy.get("allow_env_override") is not False:
        raise RuntimeContractError("path_env_override_forbidden")
    if policy.get("allow_symlink_or_reparse") is not False:
        raise RuntimeContractError("path_reparse_forbidden")
    if policy.get("require_versioned_tool_paths") is not True:
        raise RuntimeContractError("versioned_paths_required")


def _assert_versioned_programdata_exe(path: Path, *, kind: str, version: str) -> None:
    text = str(path)
    low = text.lower().replace("/", "\\")
    prefix = "c:\\programdata\\healthchecker\\tools\\"
    if not low.startswith(prefix):
        raise RuntimeContractError(f"{kind}_path_outside_tools")
    parts = Path(text).parts
    if kind == "python":
        # ...\tools\python\<version>\python.exe
        if len(parts) < 6 or parts[-1].lower() != "python.exe" or parts[-2] != version:
            raise RuntimeContractError("python_path_unversioned")
        if parts[-3].lower() != "python":
            raise RuntimeContractError("python_path_unversioned")
    elif kind == "caddy":
        if len(parts) < 6 or parts[-1].lower() != "caddy.exe" or parts[-2] != version:
            raise RuntimeContractError("caddy_path_unversioned")
        if parts[-3].lower() != "caddy":
            raise RuntimeContractError("caddy_path_unversioned")


def validate_runtime_paths(contract: CompanionRuntimeContract) -> None:
    _assert_versioned_programdata_exe(
        contract.python_exe, kind="python", version=contract.python_version
    )
    caddy_ver = str(contract.raw["caddy"]["version"])
    _assert_versioned_programdata_exe(contract.caddy_exe, kind="caddy", version=caddy_ver)
    if not re.fullmatch(r"[0-9A-F]{64}", contract.caddy_sha256):
        raise RuntimeContractError("caddy_hash_invalid")
    # Env must not override trusted paths
    for env_name in (
        "HC_PYTHON_EXE",
        "HC_CADDY_EXE",
        "HC_FIXED_PYTHON_PATH",
        "HC_FIXED_CADDY_PATH",
    ):
        if os.environ.get(env_name):
            raise RuntimeContractError("path_env_override_forbidden")


def parse_production_lock(text: str) -> dict[str, list[str]]:
    """Return {package_lower: [sha256,...]} for exact == pins with hashes."""
    packages: dict[str, list[str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.strip().rstrip("\\").strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("--only-binary") or line.startswith("--no-binary"):
            continue
        if line.startswith("--hash="):
            if not current:
                raise RuntimeContractError("requirements_unhashed")
            hashes = _HASH_RE.findall(line)
            if not hashes:
                raise RuntimeContractError("requirements_unhashed")
            packages.setdefault(current, []).extend(h.lower() for h in hashes)
            continue
        if line.startswith("--"):
            continue
        pin = _PIN_RE.match(line)
        if pin:
            name = pin.group(1).lower().replace("_", "-")
            packages.setdefault(name, [])
            current = name
            hashes = _HASH_RE.findall(line)
            packages[name].extend(h.lower() for h in hashes)
            continue
        if _UNSAFE_REQ_RE.search(line):
            raise RuntimeContractError("requirements_unsafe")
    return packages


def validate_production_lock(contract: CompanionRuntimeContract) -> None:
    in_path = contract.requirements_in_path
    lock_path = contract.lock_path
    if not in_path.is_file():
        raise RuntimeContractError("requirements_in_missing")
    if not lock_path.is_file():
        raise RuntimeContractError("requirements_lock_missing")
    in_text = in_path.read_text(encoding="utf-8")
    lock_text = lock_path.read_text(encoding="utf-8")
    if _UNSAFE_REQ_RE.search(in_text) or _UNSAFE_REQ_RE.search(lock_text):
        raise RuntimeContractError("requirements_unsafe")
    # Direct pins must appear exactly in .in
    for pin in contract.direct_pins:
        if pin not in in_text:
            raise RuntimeContractError("direct_pin_missing")
    if "tzdata==" not in in_text and "tzdata==" not in lock_text:
        raise RuntimeContractError("tzdata_missing")
    packages = parse_production_lock(lock_text)
    if not packages:
        raise RuntimeContractError("requirements_lock_empty")
    for name, hashes in packages.items():
        if name in contract.forbidden_packages:
            raise RuntimeContractError("test_package_in_production_lock")
        if not hashes:
            raise RuntimeContractError("requirements_unhashed")
        if any(len(h) != 64 for h in hashes):
            raise RuntimeContractError("requirements_unhashed")
    # Every direct pin name must be present and exact version
    for pin in contract.direct_pins:
        name, _, ver = pin.partition("==")
        key = name.lower().replace("_", "-")
        if key not in packages:
            raise RuntimeContractError("direct_pin_missing")
        # Ensure lock line version matches
        if f"{key}=={ver}" not in lock_text.lower().replace("_", "-"):
            # fastapi== stays as fastapi
            if not re.search(rf"(?im)^{re.escape(name)}=={re.escape(ver)}\b", lock_text):
                raise RuntimeContractError("direct_pin_version_mismatch")
    if "--only-binary" not in lock_text and ":all:" not in lock_text:
        # Header may include only-binary directive from pip-compile
        raise RuntimeContractError("only_binary_required")


def assert_interpreter_matches_contract(
    contract: CompanionRuntimeContract,
    *,
    executable: Path | None = None,
) -> None:
    """Fail closed if running interpreter is wrong version/arch (when staged)."""
    exe = Path(executable) if executable is not None else Path(os.path.abspath(sys_executable()))
    if exe != contract.python_exe.resolve() and str(exe).lower() != str(contract.python_exe).lower():
        # During repo tests the staged exe may not exist; callers decide.
        raise RuntimeContractError("interpreter_path_mismatch")
    if platform.system() == "Windows" and platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeContractError("python_arch_unsupported")


def sys_executable() -> str:
    import sys

    return sys.executable


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def public_contract_summary(contract: CompanionRuntimeContract) -> dict[str, Any]:
    return {
        "schema_version": contract.raw["schema_version"],
        "python_version": contract.python_version,
        "python_exe": str(contract.python_exe),
        "caddy_version": contract.raw["caddy"]["version"],
        "caddy_exe": str(contract.caddy_exe),
        "direct_pins": list(contract.direct_pins),
        "lock": str(PRODUCTION_LOCK_REL).replace("\\", "/"),
        "requires_tzdata": True,
        "allow_env_override": False,
    }
