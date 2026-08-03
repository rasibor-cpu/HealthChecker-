"""
HC-306F-R1 — Production Python/runtime dependency contract (adversarial tests).

TEMP-only. Never installs into user site-packages or ProgramData.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.health_vault.companion_host.runtime_contract import (  # noqa: E402
    RuntimeContractError,
    load_runtime_contract,
    parse_production_lock,
    validate_production_lock,
    validate_runtime_contract_schema,
    validate_runtime_paths,
)
from backend.health_vault.companion_host.scheduled_host import (  # noqa: E402
    FIXED_CADDY_PATH,
    FIXED_CADDY_SHA256,
    FIXED_PYTHON_PATH,
    assert_fixed_paths_match_runtime_contract,
)

SCRIPTS = ROOT / "scripts" / "companion_host"
CONTRACT_PATH = ROOT / "config" / "companion_runtime.json"
LOCK_PATH = ROOT / "requirements" / "production.txt"
IN_PATH = ROOT / "requirements" / "production.in"


def _protected_interpreter_is_file(path: Path) -> bool:
    """Return staged-interpreter availability or block on protected-path I/O."""
    try:
        return path.is_file()
    except OSError:
        pytest.skip(
            "clean-environment install BLOCKED: protected staged interpreter "
            "availability cannot be determined"
        )


def _ps_parse(path: Path) -> None:
    cmd = (
        "$e=$null; $t=$null; "
        "[void][System.Management.Automation.Language.Parser]::ParseFile("
        f"'{path.as_posix()}', [ref]$t, [ref]$e); "
        "if ($e) { $e | ForEach-Object { $_.ToString() }; exit 1 }; exit 0"
    )
    proc = subprocess.run(
        [
            os.environ.get("SystemRoot", r"C:\Windows")
            + r"\System32\WindowsPowerShell\v1.0\powershell.exe",
            "-NoProfile",
            "-Command",
            cmd,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"{path.name}: {proc.stdout}\n{proc.stderr}"


def test_runtime_contract_schema_and_path_agreement():
    contract = load_runtime_contract(ROOT)
    assert contract.python_version == "3.12.10"
    assert contract.raw["python"]["arch"] == "win_amd64"
    assert str(contract.python_exe) == str(FIXED_PYTHON_PATH)
    assert str(contract.caddy_exe) == str(FIXED_CADDY_PATH)
    assert contract.caddy_sha256 == FIXED_CADDY_SHA256
    assert FIXED_PYTHON_PATH.parts[-2] == "3.12.10"
    assert FIXED_CADDY_PATH.parts[-2] == "2.11.4"
    assert_fixed_paths_match_runtime_contract(ROOT)
    # Templates agree
    for name in (
        "bootstrap_companion_host.ps1.template",
        "install_scheduled_tasks.ps1.template",
        "package_verified_release.ps1.template",
    ):
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        assert r"tools\python\3.12.10\python.exe" in text
        assert r"\tools\python\python.exe" not in text
    proxy = (SCRIPTS / "bootstrap_companion_proxy.ps1.template").read_text(encoding="utf-8")
    assert r"tools\caddy\2.11.4\caddy.exe" in proxy
    install = (SCRIPTS / "install_scheduled_tasks.ps1.template").read_text(encoding="utf-8")
    assert r"tools\caddy\2.11.4\caddy.exe" in install


def test_production_lock_complete_pins_hashes_and_tzdata():
    contract = load_runtime_contract(ROOT)
    text = LOCK_PATH.read_text(encoding="utf-8")
    packages = parse_production_lock(text)
    assert "fastapi" in packages and packages["fastapi"]
    assert "uvicorn" in packages and packages["uvicorn"]
    assert "tzdata" in packages and packages["tzdata"]
    assert "pytest" not in packages
    assert "httpx" not in packages
    assert "pip-tools" not in packages
    for name, hashes in packages.items():
        assert hashes, name
        assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes)
    in_text = IN_PATH.read_text(encoding="utf-8")
    for pin in contract.direct_pins:
        assert pin in in_text
    assert "--require-hashes" in contract.pip_install_args
    assert "--only-binary=:all:" in contract.pip_install_args
    assert "tzdata==2026.3" in in_text


def test_rejects_floating_and_unsafe_requirements(tmp_path: Path):
    raw = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    (tmp_path / "requirements").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "requirements" / "production.in").write_text(
        IN_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "requirements" / "production.txt").write_text(
        "fastapi @ git+https://example.com/fastapi.git\n",
        encoding="utf-8",
    )
    raw["dependencies"]["requirements_lock"] = "requirements/production.txt"
    raw["dependencies"]["requirements_in"] = "requirements/production.in"
    from backend.health_vault.companion_host.runtime_contract import CompanionRuntimeContract

    c = CompanionRuntimeContract(raw=raw, repo_root=tmp_path)
    with pytest.raises(RuntimeContractError) as ei:
        validate_production_lock(c)
    assert ei.value.code == "requirements_unsafe"


def test_rejects_test_packages_and_unhashed(tmp_path: Path):
    raw = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    (tmp_path / "requirements").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "requirements" / "production.in").write_text(
        "fastapi==0.141.1\nuvicorn==0.52.0\ntzdata==2026.3\n", encoding="utf-8"
    )
    (tmp_path / "requirements" / "production.txt").write_text(
        "pytest==8.0.0 \\\n    --hash=sha256:" + ("ab" * 32) + "\n"
        "fastapi==0.141.1 \\\n    --hash=sha256:" + ("cd" * 32) + "\n"
        "uvicorn==0.52.0 \\\n    --hash=sha256:" + ("ef" * 32) + "\n"
        "tzdata==2026.3 \\\n    --hash=sha256:" + ("11" * 32) + "\n"
        "--only-binary :all:\n",
        encoding="utf-8",
    )
    from backend.health_vault.companion_host.runtime_contract import CompanionRuntimeContract

    c = CompanionRuntimeContract(raw=raw, repo_root=tmp_path)
    with pytest.raises(RuntimeContractError) as ei:
        validate_production_lock(c)
    assert ei.value.code == "test_package_in_production_lock"

    (tmp_path / "requirements" / "production.txt").write_text(
        "fastapi==0.141.1\n"
        "uvicorn==0.52.0 \\\n    --hash=sha256:" + ("ef" * 32) + "\n"
        "tzdata==2026.3 \\\n    --hash=sha256:" + ("11" * 32) + "\n"
        "--only-binary :all:\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeContractError) as ei2:
        validate_production_lock(c)
    assert ei2.value.code == "requirements_unhashed"


def test_rejects_unversioned_paths_and_env_override(monkeypatch: pytest.MonkeyPatch):
    raw = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    raw["python"]["programdata_exe"] = r"C:\ProgramData\HealthChecker\tools\python\python.exe"
    from backend.health_vault.companion_host.runtime_contract import CompanionRuntimeContract

    with pytest.raises(RuntimeContractError) as ei2:
        validate_runtime_paths(CompanionRuntimeContract(raw=raw, repo_root=ROOT))
    assert ei2.value.code == "python_path_unversioned"

    raw = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    monkeypatch.setenv("HC_PYTHON_EXE", r"C:\evil\python.exe")
    with pytest.raises(RuntimeContractError) as ei3:
        validate_runtime_paths(CompanionRuntimeContract(raw=raw, repo_root=ROOT))
    assert ei3.value.code == "path_env_override_forbidden"


def test_rejects_floating_installer_url():
    raw = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    raw["python"]["installer"]["url"] = "https://www.python.org/ftp/python/latest/python-amd64.exe"
    with pytest.raises(RuntimeContractError) as ei:
        validate_runtime_contract_schema(raw)
    assert ei.value.code == "python_installer_url_floating"


def test_win_amd64_wheel_hashes_present_for_native_deps():
    text = LOCK_PATH.read_text(encoding="utf-8")
    packages = parse_production_lock(text)
    # pydantic-core ships many platform wheels; lock must include multiple hashes
    assert len(packages["pydantic-core"]) >= 2
    # Pure/common wheels still hashed
    assert len(packages["fastapi"]) >= 1
    assert len(packages["tzdata"]) >= 1
    contract = load_runtime_contract(ROOT)
    assert contract.raw["python"]["arch"] == "win_amd64"
    assert contract.raw["python"]["installer"]["filename"] == "python-3.12.10-amd64.exe"
    assert re.fullmatch(r"[a-f0-9]{32}", contract.raw["python"]["installer"]["md5"])


def test_powershell_templates_parse_cleanly():
    for path in SCRIPTS.glob("*.ps1.template"):
        _ps_parse(path)


def test_protected_interpreter_probe_blocks_on_inaccessible_path():
    class InaccessiblePath:
        def is_file(self) -> bool:
            raise PermissionError("protected path")

    with pytest.raises(pytest.skip.Exception, match="BLOCKED"):
        _protected_interpreter_is_file(InaccessiblePath())  # type: ignore[arg-type]


def test_temp_clean_venv_hash_install_blocked_or_passes():
    """
    Prefer TEMP install with --require-hashes when a matching interpreter exists.
    Do not install into user/ProgramData. If selected 3.12.10 is unavailable, mark blocked.
    """
    staged = Path(r"C:\ProgramData\HealthChecker\tools\python\3.12.10\python.exe")
    if _protected_interpreter_is_file(staged):
        pytest.skip("ProgramData staging must not be used in this repo-only gate")
    # Use current 3.12 only to validate the lock installs cleanly in TEMP (same major.minor).
    ver = sys.version_info
    if (ver.major, ver.minor) != (3, 12):
        pytest.skip("clean-environment install blocked: host interpreter is not CPython 3.12")
    tmp = Path(os.environ["TEMP"]) / f"hc306f_r1_venv_{os.getpid()}"
    if tmp.exists():
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run([sys.executable, "-m", "venv", str(tmp / "v")], check=True)
        py = tmp / "v" / "Scripts" / "python.exe"
        subprocess.run(
            [
                str(py),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--upgrade",
                "pip",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        proc = subprocess.run(
            [
                str(py),
                "-m",
                "pip",
                "install",
                "--require-hashes",
                "--only-binary=:all:",
                "--disable-pip-version-check",
                "-r",
                str(LOCK_PATH),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
        probe = subprocess.run(
            [
                str(py),
                "-c",
                "import fastapi, uvicorn, tzdata; from zoneinfo import ZoneInfo; "
                "print(ZoneInfo('America/New_York').key); print('ok')",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert probe.returncode == 0, probe.stdout + probe.stderr
        assert "America/New_York" in probe.stdout
        assert "ok" in probe.stdout
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
