"""HC321-B1: HTTPS / certificate lifecycle ops — static regression gates."""
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = (ROOT / "scripts" / "start_healthchecker_production.ps1").read_text(encoding="utf-8")
RESOLVER = (ROOT / "scripts" / "Resolve-HealthCheckerInstallRoot.ps1").read_text(encoding="utf-8")
TUNNEL_CONFIGURATOR = (ROOT / "scripts" / "configure_healthchecker_cloudflare_tunnel.ps1").read_text(encoding="utf-8")
TUNNEL_LAUNCHER = (ROOT / "scripts" / "start_healthchecker_cloudflare_tunnel.ps1").read_text(encoding="utf-8")
RUNTIME_TASK = (ROOT / "scripts" / "install_healthchecker_runtime_task.ps1").read_text(encoding="utf-8")
TUNNEL_RUNBOOK = (ROOT / "docs" / "ops" / "HC321_B1_CLOUDFLARE_TUNNEL_LIFECYCLE_RUNBOOK.md").read_text(encoding="utf-8")
CERT_DOC = (ROOT / "docs" / "ops" / "HC321_B1_CERTIFICATE_LIFECYCLE.md").read_text(encoding="utf-8")
EXAMPLE = (ROOT / "config" / "healthchecker.production.example.json").read_text(encoding="utf-8")

APPROVED_ORIGIN = "https://health.capitalstratasystems.com"
OWNER_PLACEHOLDER = "RELEASE/INFRASTRUCTURE OWNER — ASSIGN BEFORE EXTERNAL PRODUCTION HANDOFF"


def test_approved_https_origin_is_enforced():
    assert "health.capitalstratasystems.com" in LAUNCHER
    assert "approved_https_origin_required" in LAUNCHER
    assert 'Scheme -ne "https"' in LAUNCHER or ".Scheme -ne" in LAUNCHER
    assert APPROVED_ORIGIN in EXAMPLE
    assert APPROVED_ORIGIN in TUNNEL_LAUNCHER
    assert "hostname: health.capitalstratasystems.com" in TUNNEL_CONFIGURATOR


def test_loopback_only_binding_and_css_8765_forbidden():
    assert "loopback_bind_required" in LAUNCHER
    assert "css_port_collision_forbidden" in LAUNCHER
    assert 'service: http://127.0.0.1:8766' in TUNNEL_CONFIGURATOR
    assert "8765" not in TUNNEL_CONFIGURATOR
    assert "8765" in LAUNCHER  # explicit collision guard
    assert "port -eq 8765" in LAUNCHER or "$port -eq 8765" in LAUNCHER


def test_install_root_aware_launcher_does_not_require_git():
    assert "Resolve-HealthCheckerInstallRoot.ps1" in LAUNCHER
    assert "Resolve-HealthCheckerInstallRoot.ps1" in RUNTIME_TASK
    assert "$installRoot" in LAUNCHER
    assert "$installRoot" in RUNTIME_TASK
    assert "install_root_markers_missing" in RESOLVER
    assert "HEALTHCHECKER_INSTALL_ROOT" in RESOLVER
    assert "rev-parse" not in RESOLVER
    assert not re.search(r"(?i)\bgit(\.exe)?\s+-", RESOLVER)
    assert "repositoryRoot" not in LAUNCHER
    assert "repositoryRoot" not in RUNTIME_TASK
    assert "backend\\health_vault\\api.py" in RESOLVER


def test_tunnel_config_fail_closed_and_no_embedded_credentials():
    for marker in (
        "tunnel_credentials_missing",
        "tunnel_credentials_path_invalid",
        "tunnel_config_path_invalid",
        "ProgramData\\HealthChecker\\secrets\\cloudflare",
    ):
        assert marker in TUNNEL_CONFIGURATOR
    assert "tunnel_config_missing" in TUNNEL_LAUNCHER
    assert "cloudflared_missing" in TUNNEL_LAUNCHER
    assert "tunnel_config_path_invalid" in TUNNEL_LAUNCHER
    # No embedded UUID or credential JSON blobs in scripts.
    assert not re.search(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", TUNNEL_CONFIGURATOR)
    assert "BEGIN PRIVATE KEY" not in TUNNEL_CONFIGURATOR
    assert "BEGIN PRIVATE KEY" not in TUNNEL_LAUNCHER
    assert '"AccountTag"' not in TUNNEL_CONFIGURATOR
    assert '"TunnelSecret"' not in TUNNEL_CONFIGURATOR
    assert "never invent" in TUNNEL_CONFIGURATOR.lower() or "Never invent" in TUNNEL_CONFIGURATOR


def test_ops_runbook_has_required_lifecycle_sections():
    required = (
        "Startup",
        "Shutdown",
        "Restart",
        "Verify loopback 8766",
        "Verify public HTTPS",
        "Recover from 524",
        "API healthy / tunnel unhealthy",
        "Re-enable",
        "DNS / tunnel identity",
        "Credential and config locations",
        "Prohibition",
        "Rollback / escalation",
        OWNER_PLACEHOLDER,
        "must not corrupt",
    )
    lowered = TUNNEL_RUNBOOK
    for section in required:
        assert section in lowered, f"missing runbook section/marker: {section}"
    assert APPROVED_ORIGIN in TUNNEL_RUNBOOK
    assert "127.0.0.1:8766" in TUNNEL_RUNBOOK
    assert "Caddy" in TUNNEL_RUNBOOK or "caddy" in TUNNEL_RUNBOOK.lower()


def test_certificate_lifecycle_doc_has_required_sections():
    required = (
        "TLS model",
        "no local public TLS private key",
        "Provider-managed edge certificate renewal",
        "Operator verification",
        "Evidence after renewal",
        "Escalation",
        OWNER_PLACEHOLDER,
        APPROVED_ORIGIN,
        "Cloudflare Tunnel",
    )
    for section in required:
        assert section in CERT_DOC, f"missing cert doc section/marker: {section}"
    assert "private key" in CERT_DOC.lower()
    assert "BEGIN PRIVATE KEY" not in CERT_DOC


def test_tunnel_launcher_is_programdata_scoped_not_git_coupled():
    assert "repositoryRoot" not in TUNNEL_LAUNCHER
    assert "rev-parse" not in TUNNEL_LAUNCHER
    assert not re.search(r"(?i)\bgit(\.exe)?\s+-", TUNNEL_LAUNCHER)
    assert "ProgramData\\HealthChecker\\config" in TUNNEL_LAUNCHER or "ProgramData\\HealthChecker\\config\\*" in TUNNEL_LAUNCHER
    assert "never resolves a repository root" in TUNNEL_LAUNCHER.lower()
