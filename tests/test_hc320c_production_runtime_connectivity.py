from pathlib import Path
import json


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = (ROOT / "scripts" / "start_healthchecker_production.ps1").read_text(encoding="utf-8")
CONFIG = json.loads((ROOT / "config" / "healthchecker.production.example.json").read_text(encoding="utf-8"))
ORIGIN = (ROOT / "android" / "app" / "src" / "main" / "java" / "com" / "healthchecker" / "companion" / "consumer" / "ConsumerOriginPolicy.kt").read_text(encoding="utf-8")
ACTIVITY = (ROOT / "android" / "app" / "src" / "main" / "java" / "com" / "healthchecker" / "companion" / "ui" / "ConsumerLauncherActivity.kt").read_text(encoding="utf-8")
PREFS = (ROOT / "android" / "app" / "src" / "main" / "java" / "com" / "healthchecker" / "companion" / "secure" / "SecurePrefs.kt").read_text(encoding="utf-8")
WORKER = (ROOT / "android" / "app" / "src" / "main" / "java" / "com" / "healthchecker" / "companion" / "work" / "MonitoringSyncWorker.kt").read_text(encoding="utf-8")
TUNNEL_CONFIGURATOR = (ROOT / "scripts/configure_healthchecker_cloudflare_tunnel.ps1").read_text(encoding="utf-8")
TUNNEL_LAUNCHER = (ROOT / "scripts/start_healthchecker_cloudflare_tunnel.ps1").read_text(encoding="utf-8")
RUNTIME_TASK_INSTALLER = (ROOT / "scripts/install_healthchecker_runtime_task.ps1").read_text(encoding="utf-8")


def test_production_launcher_requires_tls_and_explicit_identity():
    assert '--ssl-certfile' not in LAUNCHER
    assert 'approved_https_origin_required' in LAUNCHER
    assert 'service_identity_invalid' in LAUNCHER
    assert CONFIG["service_id"] == "healthchecker.consumer.api"
    assert CONFIG["public_origin"] == "https://health.capitalstratasystems.com"


def test_cloudflare_tunnel_origin_is_loopback_only_and_css_port_is_reserved():
    assert CONFIG["transport"] == "cloudflare_tunnel"
    assert CONFIG["bind_address"] == "127.0.0.1"
    assert CONFIG["port"] == 8766
    assert 'loopback_bind_required' in LAUNCHER
    assert 'css_port_collision_forbidden' in LAUNCHER
    assert 'service: http://127.0.0.1:8766' in TUNNEL_CONFIGURATOR
    assert 'hostname: health.capitalstratasystems.com' in TUNNEL_CONFIGURATOR
    assert 'http_status:404' in TUNNEL_CONFIGURATOR


def test_no_http_downgrade_and_wrong_origin_navigation_is_blocked():
    assert '"https" -> Unit' in (ROOT / "android/app/src/main/java/com/healthchecker/companion/host/PairingInputs.kt").read_text(encoding="utf-8")
    assert 'candidateOrigin != origin' in ORIGIN
    assert 'handler?.cancel()' in ACTIVITY
    assert 'MIXED_CONTENT_NEVER_ALLOW' in ACTIVITY


def test_mobile_reconnect_and_revocation_clear_user_state():
    assert 'consumerRetry' in ACTIVITY
    assert 'onResume()' in ACTIVITY
    assert 'clearUserScopedState()' in ACTIVITY
    assert '.remove(KEY_PENDING_BATCH)' in PREFS
    assert 'BackoffPolicy.EXPONENTIAL' in WORKER


def test_healthchecker_namespace_is_independent_from_css():
    combined = LAUNCHER + TUNNEL_LAUNCHER + PREFS + WORKER
    assert 'healthchecker.consumer.api' in combined
    assert 'hc_companion_secure' in PREFS
    assert 'hc303a_monitoring_sync' in WORKER
    assert 'healthchecker-cloudflare-tunnel.pid' in TUNNEL_LAUNCHER
    assert '8765' not in TUNNEL_CONFIGURATOR


def test_restart_port_collision_stale_pid_and_watchdog_controls():
    for marker in (
        'instance_already_running', 'port_already_occupied',
        'Remove-Item -LiteralPath $pidPath', 'restart_limit_exceeded',
        'healthchecker-consumer-api.heartbeat.json',
    ):
        assert marker in LAUNCHER


def test_operational_logs_do_not_emit_origins_certificates_or_secrets():
    log_lines = [line for line in LAUNCHER.splitlines() if 'Add-Content' in line]
    joined = '\n'.join(log_lines).lower()
    for forbidden in ('publicorigin', 'credentials', 'token', 'password', 'patient'):
        assert forbidden not in joined


def test_tunnel_credentials_are_external_and_no_identity_is_invented():
    assert 'TunnelId' in TUNNEL_CONFIGURATOR
    assert 'CredentialsFile' in TUNNEL_CONFIGURATOR
    assert 'tunnel_credentials_missing' in TUNNEL_CONFIGURATOR
    assert 'tunnel_credentials_path_invalid' in TUNNEL_CONFIGURATOR
    assert 'ProgramData\\HealthChecker\\secrets\\cloudflare' in TUNNEL_CONFIGURATOR
    assert '<UUID>' not in TUNNEL_CONFIGURATOR
    assert 'tunnel_config_missing' in TUNNEL_LAUNCHER
    assert 'cloudflared_missing' in TUNNEL_LAUNCHER


def test_persistent_runtime_task_uses_interactive_dpapi_identity():
    assert 'New-ScheduledTaskTrigger -AtLogOn' in RUNTIME_TASK_INSTALLER
    assert '-LogonType Interactive' in RUNTIME_TASK_INSTALLER
    assert 'interactive_dpapi_owner_required' in RUNTIME_TASK_INSTALLER
    assert 'start_healthchecker_production.ps1' in RUNTIME_TASK_INSTALLER
    assert 'Register-ScheduledTask' in RUNTIME_TASK_INSTALLER
