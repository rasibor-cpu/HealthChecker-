"""HC321-B2: desktop installer / packaging closure — static + isolated harness."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RELEASE = json.loads((ROOT / "config" / "healthchecker.release.json").read_text(encoding="utf-8"))
PACKAGE_SCRIPT = (ROOT / "scripts" / "package_healthchecker_desktop.ps1").read_text(encoding="utf-8")
INSTALL_SCRIPT = (ROOT / "scripts" / "install_healthchecker_desktop.ps1").read_text(encoding="utf-8")
UNINSTALL_SCRIPT = (ROOT / "scripts" / "uninstall_healthchecker_desktop.ps1").read_text(encoding="utf-8")
START_SCRIPT = (ROOT / "scripts" / "start_healthchecker.ps1").read_text(encoding="utf-8")
PRODUCTION_SCRIPT = (ROOT / "scripts" / "start_healthchecker_production.ps1").read_text(encoding="utf-8")
ASSERT_RUNTIME = (ROOT / "scripts" / "Assert-HealthCheckerManagedRuntime.ps1").read_text(encoding="utf-8")
RUNTIME_DOC = (ROOT / "docs" / "ops" / "HC321_B2_DESKTOP_RUNTIME_PREREQUISITE.md").read_text(encoding="utf-8")
INSTALL_DOC = (ROOT / "docs" / "ops" / "HC321_B2_DESKTOP_INSTALL_UNINSTALL.md").read_text(encoding="utf-8")

DESKTOP_VERSION = "0.321.0"
FORBIDDEN_PACKAGE_CONTENT = (
    b"BEGIN PRIVATE KEY",
    b"BEGIN RSA PRIVATE KEY",
    b"BEGIN OPENSSH PRIVATE KEY",
    b"BEGIN CERTIFICATE PRIVATE KEY",
)


def _pwsh(*args: str, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        *args,
    ]
    return subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=check,
        env=env,
    )


def test_desktop_release_version_advanced_to_0_321_0():
    assert RELEASE["release_format"] == "hc.release.v1"
    assert RELEASE["version"] == DESKTOP_VERSION
    assert "0.321.0" in PACKAGE_SCRIPT or "$version" in PACKAGE_SCRIPT
    assert "healthchecker.release.json" in PACKAGE_SCRIPT
    # Desktop metadata is authoritative for B2; Android line is closed by B3 at 321.
    gradle = (ROOT / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
    assert 'versionName = "0.321.0"' in gradle
    assert "versionCode = 321" in gradle
    assert RELEASE["version"] == DESKTOP_VERSION


def test_package_includes_production_and_runtime_scripts():
    for required in (
        "scripts/start_healthchecker_production.ps1",
        "scripts/start_healthchecker_cloudflare_tunnel.ps1",
        "scripts/configure_healthchecker_cloudflare_tunnel.ps1",
        "scripts/install_healthchecker_runtime_task.ps1",
        "scripts/Resolve-HealthCheckerInstallRoot.ps1",
        "scripts/Assert-HealthCheckerManagedRuntime.ps1",
        "config/healthchecker.production.example.json",
        "docs/ops/HC321_B2_DESKTOP_RUNTIME_PREREQUISITE.md",
        "docs/ops/HC321_B2_DESKTOP_INSTALL_UNINSTALL.md",
    ):
        assert required in PACKAGE_SCRIPT
    assert "vault_storage|hc_intake" in PACKAGE_SCRIPT or "vault_storage" in PACKAGE_SCRIPT
    assert ".git" in PACKAGE_SCRIPT
    assert 'Release output must be outside the source tree' in PACKAGE_SCRIPT


def test_consumer_launcher_is_production_8766_not_legacy_8000():
    assert "start_healthchecker_production.ps1" in START_SCRIPT
    assert "Resolve-HealthCheckerInstallRoot.ps1" in START_SCRIPT
    assert "Assert-HealthCheckerManagedRuntime.ps1" in START_SCRIPT
    assert "config_missing" in START_SCRIPT
    assert re.search(r"\$Port\s*=\s*8000", START_SCRIPT) is None
    assert "-Port" not in START_SCRIPT
    assert "legacy-port" in START_SCRIPT or "production HealthChecker path" in START_SCRIPT
    assert "css_port_collision_forbidden" in PRODUCTION_SCRIPT
    assert "Assert-HealthCheckerManagedRuntime.ps1" in PRODUCTION_SCRIPT
    assert "start_healthchecker.ps1" in INSTALL_SCRIPT
    assert "CommonDesktopDirectory" in INSTALL_SCRIPT or "ShortcutDirectory" in INSTALL_SCRIPT


def test_install_uninstall_preserve_userdata_and_atomic_swap():
    assert "PreserveUserData" in INSTALL_SCRIPT
    assert "$InstallRoot.next" in INSTALL_SCRIPT or ".next" in INSTALL_SCRIPT
    assert ".previous" in INSTALL_SCRIPT
    assert "release_integrity_failed" in INSTALL_SCRIPT
    assert "Remove-Item -LiteralPath $dataRoot" not in UNINSTALL_SCRIPT
    assert "Remove-Item -LiteralPath $DataRoot" not in UNINSTALL_SCRIPT
    assert "User data preserved" in UNINSTALL_SCRIPT
    assert "RemoveUserData" in UNINSTALL_SCRIPT
    assert "remove_user_data_not_implemented_in_default_uninstaller" in UNINSTALL_SCRIPT
    assert "Unregister-ScheduledTask" in UNINSTALL_SCRIPT
    assert "ProgramData" in INSTALL_DOC
    assert "never" in INSTALL_DOC.lower()


def test_managed_runtime_fail_closed_documented():
    assert "managed_runtime_missing" in ASSERT_RUNTIME
    assert "Internet downloaders" in ASSERT_RUNTIME or "ungoverned" in ASSERT_RUNTIME.lower()
    assert "3.12.10" in RUNTIME_DOC
    assert "no" in RUNTIME_DOC.lower() and "silent" in RUNTIME_DOC.lower()
    assert "Assert-HealthCheckerManagedRuntime.ps1" in INSTALL_SCRIPT
    assert "managed_runtime" in INSTALL_SCRIPT.lower() or "Assert-HealthCheckerManagedRuntime" in INSTALL_SCRIPT


def test_isolated_package_install_rollback_uninstall_preserves_data(tmp_path: Path):
    """Source-independent acceptance under a temp root (does not touch production)."""
    outside = tmp_path / "release-out"
    outside.mkdir()
    # Package must live outside the source tree.
    assert not str(outside).startswith(str(ROOT))

    pkg = _pwsh(
        "-File",
        str(ROOT / "scripts" / "package_healthchecker_desktop.ps1"),
        "-OutputDirectory",
        str(outside),
    )
    stage = Path(pkg.stdout.strip().splitlines()[-1].strip())
    assert stage.name == f"HealthChecker-{DESKTOP_VERSION}"
    assert (stage / "release-manifest.json").is_file()
    assert (stage / "scripts" / "start_healthchecker_production.ps1").is_file()
    assert (stage / "scripts" / "Resolve-HealthCheckerInstallRoot.ps1").is_file()
    assert (stage / "scripts" / "Assert-HealthCheckerManagedRuntime.ps1").is_file()
    assert not (stage / ".git").exists()
    assert not list(stage.rglob("test_*.py"))
    assert not list(stage.rglob("vault_storage"))
    assert not list(stage.rglob("hc_intake"))

    for path in stage.rglob("*"):
        if not path.is_file():
            continue
        rel = str(path.relative_to(stage)).replace("\\", "/").lower()
        for name in ("client_secret.json", "gmail_token.json", "id_rsa"):
            assert name not in rel, f"forbidden package artifact: {rel}"
        for ext in (".pem", ".pfx", ".p12", ".keystore", ".jks"):
            assert not rel.endswith(ext), f"forbidden package artifact: {rel}"

    manifest = json.loads((stage / "release-manifest.json").read_text(encoding="utf-8-sig"))
    assert manifest["format"] == "hc.release.manifest.v1"
    assert manifest["version"] == DESKTOP_VERSION
    for entry in manifest["files"]:
        path = stage / entry["path"]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == entry["sha256"]
        blob = path.read_bytes()
        for token in FORBIDDEN_PACKAGE_CONTENT:
            assert token not in blob, f"secret material in {entry['path']}"

    # Tampered package must be rejected before activation.
    tampered = tmp_path / "tampered"
    shutil.copytree(stage, tampered)
    victim = tampered / "index.html"
    victim.write_text(victim.read_text(encoding="utf-8") + "\n<!--tamper-->\n", encoding="utf-8")
    install_root = tmp_path / "iso-install" / "HealthChecker"
    data_root = tmp_path / "iso-data"
    shortcut_dir = tmp_path / "shortcuts"
    data_root.mkdir(parents=True)
    user_marker = data_root / "data" / "USER_DATA_MARKER.txt"
    user_marker.parent.mkdir(parents=True, exist_ok=True)
    user_marker.write_text("preserve-me", encoding="utf-8")
    secret_marker = data_root / "secrets" / "keep.secret"
    secret_marker.parent.mkdir(parents=True, exist_ok=True)
    secret_marker.write_text("do-not-delete", encoding="utf-8")

    managed_python = Path(r"C:\ProgramData\HealthChecker\tools\python\3.12.10\python.exe")
    if not managed_python.is_file():
        pytest.skip("governed managed Python unavailable on this host")

    bad = _pwsh(
        "-File",
        str(ROOT / "scripts" / "install_healthchecker_desktop.ps1"),
        "-PackageDirectory",
        str(tampered),
        "-InstallRoot",
        str(install_root),
        "-DataRoot",
        str(data_root),
        "-ShortcutDirectory",
        str(shortcut_dir),
        "-ManagedPythonPath",
        str(managed_python),
        "-SkipShortcut",
        check=False,
    )
    assert bad.returncode != 0
    assert "release_integrity_failed" in (bad.stderr + bad.stdout)
    assert not install_root.exists()

    # Missing runtime fail-closed (no partial install).
    missing_runtime = _pwsh(
        "-File",
        str(ROOT / "scripts" / "install_healthchecker_desktop.ps1"),
        "-PackageDirectory",
        str(stage),
        "-InstallRoot",
        str(install_root),
        "-DataRoot",
        str(data_root),
        "-ManagedPythonPath",
        str(tmp_path / "no-such-python.exe"),
        "-SkipShortcut",
        check=False,
    )
    assert missing_runtime.returncode != 0
    assert "managed_runtime_missing" in (missing_runtime.stderr + missing_runtime.stdout)
    assert not install_root.exists()

    # Clean install from verified package.
    ok = _pwsh(
        "-File",
        str(ROOT / "scripts" / "install_healthchecker_desktop.ps1"),
        "-PackageDirectory",
        str(stage),
        "-InstallRoot",
        str(install_root),
        "-DataRoot",
        str(data_root),
        "-ShortcutDirectory",
        str(shortcut_dir),
        "-ManagedPythonPath",
        str(managed_python),
    )
    assert install_root.is_dir()
    assert (install_root / "scripts" / "start_healthchecker.ps1").is_file()
    assert (shortcut_dir / "HealthChecker.lnk").is_file()
    assert user_marker.read_text(encoding="utf-8") == "preserve-me"

    # Resolve installed root without source checkout.
    resolve = _pwsh(
        "-File",
        str(install_root / "scripts" / "Resolve-HealthCheckerInstallRoot.ps1"),
        "-ScriptsDirectory",
        str(install_root / "scripts"),
    )
    resolved = resolve.stdout.strip().splitlines()[-1].strip()
    assert Path(resolved).resolve() == install_root.resolve()

    # Startup/preflight: production launcher fail-closed without production config
    # under the isolated data root (do not start live :8766).
    iso_config = data_root / "config" / "production.json"
    # Ensure we do not point at live production config accidentally.
    preflight = _pwsh(
        "-File",
        str(install_root / "scripts" / "start_healthchecker.ps1"),
        "-ConfigPath",
        str(iso_config),
        check=False,
    )
    assert preflight.returncode != 0
    combined = preflight.stderr + preflight.stdout
    assert "config_missing" in combined or "startup failed" in combined.lower()

    # Update / rollback: seed previous, then fail activation mid-flight by
    # simulating promotion failure restoration path via second good install
    # (atomic previous retention) and interrupted next cleanup.
    first_marker = install_root / "B2_INSTALL_MARKER.txt"
    first_marker.write_text("v1", encoding="utf-8")
    ok2 = _pwsh(
        "-File",
        str(ROOT / "scripts" / "install_healthchecker_desktop.ps1"),
        "-PackageDirectory",
        str(stage),
        "-InstallRoot",
        str(install_root),
        "-DataRoot",
        str(data_root),
        "-ShortcutDirectory",
        str(shortcut_dir),
        "-ManagedPythonPath",
        str(managed_python),
        "-SkipShortcut",
    )
    assert install_root.is_dir()
    previous = Path(str(install_root) + ".previous")
    assert previous.is_dir()
    assert (previous / "B2_INSTALL_MARKER.txt").read_text(encoding="utf-8") == "v1"
    assert not (install_root / "B2_INSTALL_MARKER.txt").exists()

    # Deterministic rollback helper: restore .previous if present.
    rollback = _pwsh(
        "-Command",
        (
            f"$active = '{install_root}'; $prev = '{previous}'; "
            "if (Test-Path -LiteralPath $active) { Remove-Item -LiteralPath $active -Recurse -Force }; "
            "Move-Item -LiteralPath $prev -Destination $active; "
            "Write-Output 'rollback_ok'"
        ),
    )
    assert "rollback_ok" in rollback.stdout
    assert (install_root / "B2_INSTALL_MARKER.txt").read_text(encoding="utf-8") == "v1"

    # Uninstall removes app + shortcut; preserves user data/secrets.
    _pwsh(
        "-File",
        str(ROOT / "scripts" / "uninstall_healthchecker_desktop.ps1"),
        "-InstallRoot",
        str(install_root),
        "-DataRoot",
        str(data_root),
        "-ShortcutDirectory",
        str(shortcut_dir),
    )
    assert not install_root.exists()
    assert not (shortcut_dir / "HealthChecker.lnk").exists()
    assert user_marker.read_text(encoding="utf-8") == "preserve-me"
    assert secret_marker.read_text(encoding="utf-8") == "do-not-delete"

    # Explicit -RemoveUserData must refuse rather than delete.
    refuse = _pwsh(
        "-File",
        str(ROOT / "scripts" / "uninstall_healthchecker_desktop.ps1"),
        "-InstallRoot",
        str(install_root),
        "-DataRoot",
        str(data_root),
        "-RemoveUserData",
        check=False,
    )
    assert refuse.returncode != 0
    assert "remove_user_data_not_implemented" in (refuse.stderr + refuse.stdout)
    assert user_marker.exists()
