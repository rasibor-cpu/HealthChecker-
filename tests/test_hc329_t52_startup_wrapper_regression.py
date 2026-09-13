from pathlib import Path
import hashlib
import re


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_PATH = ROOT / "scripts" / "start_healthchecker_production.ps1"
LAUNCHER = LAUNCHER_PATH.read_text(encoding="utf-8")


def test_hc329_t52_launcher_preserves_known_good_startup_contract():
    """
    Regression guard for HC329-T51 and the later supervised wrapper.

    The contract is behavioral: governed runtime resolution, loopback binding,
    CSS-port protection, single-instance controls, heartbeat/PID lifecycle, and
    the supervised Uvicorn child launch must remain present.
    """
    required_markers = (
        'Assert-HealthCheckerManagedRuntime.ps1',
        'Resolve-HealthCheckerInstallRoot.ps1',
        'healthchecker.consumer.api',
        'cloudflare_tunnel',
        '127.0.0.1',
        'css_port_collision_forbidden',
        'port_already_occupied',
        'instance_already_running',
        'healthchecker-consumer-api.pid',
        'healthchecker-consumer-api.heartbeat.json',
        'Start-HcUvicornChild',
        '$psi.Arguments = "-m uvicorn backend.health_vault.api:create_health_vault_app --factory --host $BindAddress --port $Port --no-access-log"',
    )
    for marker in required_markers:
        assert marker in LAUNCHER


def test_hc329_t52_launcher_does_not_gain_unsafe_process_or_network_actions():
    # The supervised wrapper may terminate only the child process tree it owns.
    # What remains forbidden is broad/name-based killing, CSS-port handling,
    # system network reconfiguration, ACL mutation, or tunnel lifecycle changes.
    assert 'function Stop-HcOwnedChild' in LAUNCHER
    assert 'Stop-HcOwnedChild -Child $child' in LAUNCHER

    forbidden_patterns = (
        r'Stop-Process\s+-Name\b',
        r'Get-Process[^\n|]*\|[^\n]*Stop-Process',
        r'taskkill(?:\.exe)?\b',
        r'Get-NetTCPConnection[^\n]*8765',
        r'LocalPort\s+8765',
        r'netsh\b',
        r'icacls\b',
        r'Set-Acl\b',
        r'cloudflared\s+tunnel\s+(?:run|delete|create)',
    )
    for pattern in forbidden_patterns:
        assert re.search(pattern, LAUNCHER, flags=re.IGNORECASE) is None


def test_hc329_t52_launcher_keeps_fail_closed_runtime_and_config_checks():
    for marker in (
        'managed_runtime_assert_missing',
        'runtime_missing',
        'install_root_resolver_missing',
        'install_root_unresolved',
        'config_missing',
        'config_invalid',
        'service_identity_invalid',
        'transport_invalid',
        'tunnel_service_identity_invalid',
        'loopback_bind_required',
        'approved_https_origin_required',
    ):
        assert marker in LAUNCHER


def test_hc329_t52_baseline_wrapper_fingerprint_is_documented_not_enforced():
    """
    Useful diagnostic only: do not hard-pin the launcher hash because reviewed,
    tested supervisor improvements are legitimate.
    """
    digest = hashlib.sha256(LAUNCHER_PATH.read_bytes()).hexdigest()
    assert len(digest) == 64
    assert digest != "0" * 64
