"""
HC-306H-R1 — Runtime-asset packaging + bytecode immutability (adversarial).

TEMP-only synthetic releases/vaults. Never opens permanent monitoring vault,
vault_storage, or private_imports. Never installs scheduled tasks.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.health_vault.companion_host.scheduled_host import (  # noqa: E402
    RELEASE_RUNTIME_ASSETS,
    REQUIRED_RELEASE_REL_PATHS,
    ScheduledHostError,
    assert_release_tree_has_no_bytecode,
    build_release_manifest,
    iter_release_source_files,
    sha256_file,
    verify_release_manifest,
    write_release_copy,
)

COMMIT_40 = "1f28d67c41a20c8fbf16bc2e3b4889b0d9b34e37"
THRESHOLDS_REL = "backend/health_vault/config/monitoring_thresholds.json"
SCRIPTS = ROOT / "scripts" / "companion_host"


def _ps_parse(path: Path) -> None:
    import subprocess

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
    assert proc.returncode == 0, f"parse failed {path.name}: {proc.stdout}\n{proc.stderr}"


def test_thresholds_asset_classified_and_allowlisted():
    assert THRESHOLDS_REL in RELEASE_RUNTIME_ASSETS
    assert THRESHOLDS_REL in REQUIRED_RELEASE_REL_PATHS
    assert (ROOT / THRESHOLDS_REL).is_file()


def test_packaged_release_includes_thresholds_with_matching_hash(tmp_path: Path):
    release = write_release_copy(
        repo_root=ROOT, source_commit=COMMIT_40, dest_root=tmp_path / "releases"
    )
    asset = release / THRESHOLDS_REL
    assert asset.is_file()
    manifest = verify_release_manifest(release)
    assert THRESHOLDS_REL in manifest["files"]
    expected = manifest["files"][THRESHOLDS_REL]
    independent = hashlib.sha256(asset.read_bytes()).hexdigest().upper()
    assert independent == expected.upper()
    assert sha256_file(asset).upper() == expected.upper()


def test_missing_required_asset_fails_packaging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Point allowlist at a missing path without mutating the real repo tree.
    missing = "backend/health_vault/config/does_not_exist_hc306h.json"
    monkeypatch.setattr(
        "backend.health_vault.companion_host.scheduled_host.RELEASE_RUNTIME_ASSETS",
        (missing,),
    )
    with pytest.raises(ScheduledHostError) as ei:
        iter_release_source_files(ROOT)
    assert ei.value.code == "release_file_missing"


def test_altered_thresholds_fails_verification(tmp_path: Path):
    release = write_release_copy(
        repo_root=ROOT, source_commit=COMMIT_40, dest_root=tmp_path / "releases"
    )
    target = release / THRESHOLDS_REL
    target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ScheduledHostError) as ei:
        verify_release_manifest(release)
    assert ei.value.code == "release_file_modified"


def test_unexpected_json_not_copied_automatically(tmp_path: Path):
    # executive_dashboard.json exists in repo but is intentionally NOT allowlisted
    # for companion-host packaging (category D for this surface).
    files = iter_release_source_files(ROOT)
    rels = [p.relative_to(ROOT).as_posix() for p in files]
    assert "backend/health_vault/config/executive_dashboard.json" not in rels
    assert THRESHOLDS_REL in rels
    # Extra JSON must not appear via broad glob
    for r in rels:
        if r.endswith(".json") and r.startswith("backend/health_vault/"):
            assert r in RELEASE_RUNTIME_ASSETS


def test_traversal_and_absolute_asset_rejected(monkeypatch: pytest.MonkeyPatch):
    from backend.health_vault.companion_host import scheduled_host as sh

    with pytest.raises(ScheduledHostError):
        sh._assert_runtime_asset_rel("../secrets/x.json")
    with pytest.raises(ScheduledHostError):
        sh._assert_runtime_asset_rel(r"C:\Windows\System32\drivers\etc\hosts")
    with pytest.raises(ScheduledHostError):
        sh._assert_runtime_asset_rel("backend/health_vault/config/evil.exe")


def test_host_env_caddyfile_vault_logs_tests_excluded(tmp_path: Path):
    release = write_release_copy(
        repo_root=ROOT, source_commit=COMMIT_40, dest_root=tmp_path / "releases"
    )
    rels = set(json.loads((release / "RELEASE_MANIFEST.json").read_text(encoding="utf-8"))["files"])
    joined = "\n".join(rels)
    assert "host.env" not in joined
    assert "ProgramData" not in joined
    assert "vault_storage" not in joined
    assert "/logs/" not in joined
    assert not any(r.startswith("tests/") for r in rels)
    # Packaged Caddyfile is the template (scripts path), not live ProgramData Caddyfile
    assert "scripts/companion_host/Caddyfile" in rels


def test_bytecode_extensions_rejected_in_release_tree(tmp_path: Path):
    release = write_release_copy(
        repo_root=ROOT, source_commit=COMMIT_40, dest_root=tmp_path / "releases"
    )
    cache = release / "backend" / "__pycache__"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "x.cpython-312.pyc").write_bytes(b"\0")
    with pytest.raises(ScheduledHostError) as ei:
        assert_release_tree_has_no_bytecode(release)
    assert ei.value.code == "release_file_modified"
    with pytest.raises(ScheduledHostError):
        verify_release_manifest(release)


def test_packaged_import_with_bytecode_disabled_creates_no_cache(tmp_path: Path):
    release = write_release_copy(
        repo_root=ROOT, source_commit=COMMIT_40, dest_root=tmp_path / "releases"
    )
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    # Fresh interpreter: release first, Git tree removed from path.
    code = (
        "import sys\n"
        f"rel = r'''{release}'''\n"
        "sys.path = [rel] + [p for p in sys.path if 'HealthChecker-' not in str(p).replace('/','\\\\')]\n"
        "import backend.health_vault.companion_host.scheduled_host as sh\n"
        "from pathlib import Path\n"
        "print('ok', Path(sh.__file__).resolve().as_posix().startswith(Path(rel).resolve().as_posix().replace('\\\\','/')))\n"
    )
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-B", "-c", code],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=str(tmp_path),
    )
    assert proc.returncode == 0, proc.stderr
    assert "ok True" in proc.stdout
    assert not any(release.rglob("__pycache__"))
    assert not any(release.rglob("*.pyc"))


def test_protected_status_from_temp_release_via_trusted_proxy_path(tmp_path: Path):
    """
    Packaged release must serve status=200 through trusted synthetic proxy headers.

    Does not bind sockets. Uses TEMP vault only. Git tree off import path.
    """
    release = write_release_copy(
        repo_root=ROOT, source_commit=COMMIT_40, dest_root=tmp_path / "releases"
    )
    prior_path = list(sys.path)
    prior_modules = {k: sys.modules[k] for k in list(sys.modules) if k == "backend" or k.startswith("backend.")}
    prior_dwb = sys.dont_write_bytecode
    try:
        # Isolate imports to TEMP release; disable bytecode before any release import.
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        sys.dont_write_bytecode = True
        for key in list(sys.modules):
            if key == "backend" or key.startswith("backend."):
                del sys.modules[key]
        sys.path = [str(release)] + [
            p for p in sys.path if "HealthChecker-" not in str(p).replace("/", "\\")
        ]

        from backend.health_vault.companion_host.app import build_activated_app
        from backend.health_vault.monitoring import monitoring_engine as me
        from fastapi.testclient import TestClient

        # Thresholds must resolve inside the packaged release, not the Git tree.
        thresholds_path = Path(me._THRESHOLDS_PATH).resolve()
        assert str(thresholds_path).startswith(str(release.resolve()))
        assert not str(thresholds_path).startswith(str(ROOT.resolve()))

        vault = tmp_path / "syn_vault"
        origin = "https://phone-host.example.ts.net"
        env = {
            "HC_HOST_ACTIVATION": "enabled",
            "HC_COMPANION_ADMIN_TOKEN": "test-admin-token-24chars-min!!",
            "HC_COMPANION_PEPPER": "test-pepper-value-24chars-min!!",
            "HC_PROXY_SHARED_TOKEN": "test-proxy-shared-token-24min!!",
            "HC_MONITORING_VAULT_ROOT": str(vault),
            "HC_TRUSTED_PROXY_MODE": "tailscale_https",
            "HC_EXTERNAL_HTTPS_ORIGIN": origin,
            "HC_BIND_HOST": "127.0.0.1",
            "HC_BIND_PORT": "8743",
            "HC_HOST_ALLOW_TESTCLIENT_PEER": "1",
        }
        app, config, _store = build_activated_app(environ=env, repo_root=release)
        os.environ["HC_HOST_ALLOW_TESTCLIENT_PEER"] = "1"
        client = TestClient(app)

        hz = client.get("/healthz")
        assert hz.status_code == 200
        assert hz.content
        body = hz.json()
        assert "ok" in body or "status" in body

        # Direct protected status without proxy proof → 403
        direct = client.get("/api/companion/status")
        assert direct.status_code == 403

        # Trusted synthetic proxy path → gate passes; status handler returns 200
        headers = {
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "phone-host.example.ts.net",
            "Host": "phone-host.example.ts.net",
            "X-HC-Proxy-Token": config.proxy_shared_token,
            "Authorization": "Bearer not-a-real-device-token",
        }
        proxied = client.get("/api/companion/status", headers=headers)
        assert proxied.status_code == 200, proxied.text
        text = proxied.text
        assert config.proxy_shared_token not in text
        assert config.admin_token not in text
        assert not any(release.rglob("__pycache__"))
        assert not any(release.rglob("*.pyc"))
    finally:
        sys.path[:] = prior_path
        sys.dont_write_bytecode = prior_dwb
        for key in list(sys.modules):
            if key == "backend" or key.startswith("backend."):
                del sys.modules[key]
        sys.modules.update(prior_modules)


def test_tamper_copy_of_thresholds_fails_closed(tmp_path: Path):
    release = write_release_copy(
        repo_root=ROOT, source_commit=COMMIT_40, dest_root=tmp_path / "a"
    )
    tamper = tmp_path / "b"
    shutil.copytree(release, tamper)
    (tamper / THRESHOLDS_REL).write_text('{"schema_version":"tampered"}\n', encoding="utf-8")
    with pytest.raises(ScheduledHostError) as ei:
        verify_release_manifest(tamper)
    assert ei.value.code == "release_file_modified"
    # Original still valid
    verify_release_manifest(release)


def test_powershell_templates_parse_and_bytecode_controls_present():
    for name in (
        "bootstrap_companion_host.ps1.template",
        "bootstrap_companion_proxy.ps1.template",
        "host_env_loader.ps1.template",
        "package_verified_release.ps1.template",
    ):
        _ps_parse(SCRIPTS / name)
    host = (SCRIPTS / "bootstrap_companion_host.ps1.template").read_text(encoding="utf-8")
    assert "PYTHONDONTWRITEBYTECODE" in host
    assert "'-B'" in host or '"-B"' in host or "-B" in host
    loader = (SCRIPTS / "host_env_loader.ps1.template").read_text(encoding="utf-8")
    assert THRESHOLDS_REL.replace("/", "\\") in loader or THRESHOLDS_REL in loader
    pkg = (SCRIPTS / "package_verified_release.ps1.template").read_text(encoding="utf-8")
    assert "PYTHONDONTWRITEBYTECODE" in pkg
    assert " -B " in pkg or "-B -c" in pkg


def test_manifest_build_lists_runtime_assets():
    built = build_release_manifest(repo_root=ROOT, source_commit=COMMIT_40)
    for rel in RELEASE_RUNTIME_ASSETS:
        assert rel in built["files"]
    keys = list(built["files"].keys())
    assert keys == sorted(keys)
