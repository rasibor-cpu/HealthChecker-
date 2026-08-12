"""
HC-306E-R2 — Windows Task Scheduler always-on host foundation (adversarial tests).

TEMP-only synthetic data. Never opens production vault_storage or private_imports.
Never installs scheduled tasks or creates ProgramData release copies.
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

from backend.health_vault.companion_host.scheduled_host import (  # noqa: E402
    ALLOWED_HOST_ENV_KEYS,
    APPROVAL_SCHEDULED_HOST,
    EXACT_HEALTHCHECKER_TASK_NAMES,
    FIXED_CADDY_PATH,
    FIXED_PYTHON_PATH,
    FORBIDDEN_PORTS,
    HOST_ENV_PATH,
    MULTIPLE_INSTANCES_POLICY,
    PROXY_STARTUP_DELAY_SECONDS,
    REJECTED_SERVICE_WRAPPERS,
    RESTART_COUNT,
    RESTART_INTERVAL_MINUTES,
    TASK_COMPANION_HOST,
    TASK_COMPANION_PROXY,
    ScheduledHostError,
    apply_host_env_to_mapping,
    assert_fixed_executable,
    assert_not_git_working_tree,
    assert_uninstall_task_name_allowed,
    assess_executable_for_system_task,
    build_release_manifest,
    iter_release_source_files,
    parse_host_env_file,
    parse_host_env_text,
    path_is_excluded,
    public_policy_dict,
    scheduled_task_settings_contract,
    task_action_arguments_are_secret_free,
    verify_release_manifest,
    write_release_copy,
)

SCRIPTS = ROOT / "scripts" / "companion_host"
COMMIT_40 = "1f28d67c41a20c8fbf16bc2e3b4889b0d9b34e37"


def _ps_parse(path: Path) -> None:
    """PowerShell templates must parse cleanly (AST)."""
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


def test_nssm_winsw_rejected_no_active_install_path():
    assert "NSSM" in REJECTED_SERVICE_WRAPPERS
    assert "WinSW" in REJECTED_SERVICE_WRAPPERS
    install_svc = (SCRIPTS / "install_service.ps1.template").read_text(encoding="utf-8")
    assert "REJECTED" in install_svc
    assert "nssm_winsw_rejected" in install_svc
    assert "Register-ScheduledTask" not in install_svc
    # Active path uses Task Scheduler template
    install_tasks = (SCRIPTS / "install_scheduled_tasks.ps1.template").read_text(encoding="utf-8")
    assert "Register-ScheduledTask" in install_tasks
    assert "IgnoreNew" in install_tasks
    assert "NSSM" in install_tasks and "REJECTED" in install_tasks
    # No live nssm/winsw install commands outside rejected commentary
    for path in SCRIPTS.glob("*.ps1.template"):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            code = line.split("#", 1)[0].strip().lower()
            if not code:
                continue
            assert not re.search(r"\bnssm\s+install\b", code)
            assert not re.search(r"\bwinsw(\.exe)?\s+install\b", code)


def test_no_secret_in_task_definitions_arguments():
    for name in (
        "install_scheduled_tasks.ps1.template",
        "control_scheduled_tasks.ps1.template",
        "bootstrap_companion_host.ps1.template",
        "bootstrap_companion_proxy.ps1.template",
    ):
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        assert "HC_COMPANION_ADMIN_TOKEN=" not in text
        assert "HC_COMPANION_PEPPER=" not in text
        assert "HC_PROXY_SHARED_TOKEN=" not in text
        # Argument construction must reference File bootstrap only
        assert "token=" not in text.lower() or "secret" in text.lower()
    assert task_action_arguments_are_secret_free(
        r'-NoProfile -ExecutionPolicy Bypass -File "C:\ProgramData\HealthChecker\releases\abc\scripts\companion_host\bootstrap_companion_host.ps1"'
    )
    assert not task_action_arguments_are_secret_free(
        "-File x.ps1 -Token super-secret-value-here-24"
    )


def test_no_execution_from_git_working_tree(tmp_path: Path):
    with pytest.raises(ScheduledHostError) as ei:
        assert_not_git_working_tree(ROOT / "backend", ROOT)
    assert ei.value.code == "git_tree_execution_forbidden"
    outside = tmp_path / "release" / COMMIT_40
    outside.mkdir(parents=True)
    assert_not_git_working_tree(outside, ROOT)  # does not raise
    from backend.health_vault.companion_host.scheduled_host import assert_release_dir_location

    with pytest.raises(ScheduledHostError) as loc:
        assert_release_dir_location(outside)
    assert loc.value.code == "git_tree_execution_forbidden"


def test_manifest_mismatch_and_missing_file_fail_closed(tmp_path: Path):
    release = write_release_copy(
        repo_root=ROOT,
        source_commit=COMMIT_40,
        dest_root=tmp_path / "releases",
    )
    verify_release_manifest(release)
    # Modify a tracked file
    target = next(release.rglob("activation.py"))
    target.write_text(target.read_text(encoding="utf-8") + "\n# tamper\n", encoding="utf-8")
    with pytest.raises(ScheduledHostError) as ei:
        verify_release_manifest(release)
    assert ei.value.code == "release_file_modified"
    # Missing file
    target.unlink()
    with pytest.raises(ScheduledHostError) as ei2:
        verify_release_manifest(release)
    assert ei2.value.code == "release_file_missing"
    # Missing manifest
    (release / "RELEASE_MANIFEST.json").unlink()
    with pytest.raises(ScheduledHostError) as ei3:
        verify_release_manifest(release)
    assert ei3.value.code == "manifest_missing"


def test_env_parser_rejects_unknown_duplicate_malformed_injection(tmp_path: Path):
    good = "\n".join(
        [
            "HC_HOST_ACTIVATION=enabled",
            "HC_COMPANION_ADMIN_TOKEN=test-admin-token-24chars-min!!",
            "HC_COMPANION_PEPPER=test-pepper-value-24chars-min!!",
            "HC_PROXY_SHARED_TOKEN=test-proxy-shared-token-24min!!",
            r"HC_MONITORING_VAULT_ROOT=C:\HealthCheckerData\monitoring_vault",
            "HC_TRUSTED_PROXY_MODE=tailscale_https",
            "HC_EXTERNAL_HTTPS_ORIGIN=https://example.ts.net",
            "HC_EXTERNAL_HTTPS_HOST=example.ts.net",
            "HC_BIND_HOST=127.0.0.1",
            "HC_BIND_PORT=8743",
            "HC_PROXY_LISTEN_HOST=127.0.0.1",
            "HC_PROXY_LISTEN_PORT=8744",
            "HC_TAILSCALE_SERVE_TARGET_PORT=8744",
        ]
    )
    parsed = parse_host_env_text(good)
    assert "HC_BIND_PORT" in parsed.values
    env_map: dict[str, str] = {}
    apply_host_env_to_mapping(parsed, env_map)
    assert env_map["HC_BIND_PORT"] == "8743"

    with pytest.raises(ScheduledHostError) as u:
        parse_host_env_text(good + "\nHC_EVIL_KEY=1\n")
    assert u.value.code == "env_unknown_key"

    with pytest.raises(ScheduledHostError) as d:
        parse_host_env_text("HC_BIND_PORT=8743\nHC_BIND_PORT=8744\n")
    assert d.value.code == "env_duplicate_key"

    with pytest.raises(ScheduledHostError) as m:
        parse_host_env_text("NOT_A_KEY=1\n")
    assert m.value.code == "env_malformed"

    with pytest.raises(ScheduledHostError) as inj:
        parse_host_env_text("HC_BIND_PORT=$(calc)\n")
    assert inj.value.code == "env_injection_rejected"

    with pytest.raises(ScheduledHostError) as inj2:
        parse_host_env_text("HC_BIND_PORT=1;Remove-Item -Recurse C:\\\n")
    assert inj2.value.code == "env_injection_rejected"

    with pytest.raises(ScheduledHostError) as blank:
        parse_host_env_text("=value\n")
    assert blank.value.code == "env_malformed"

    # Wrong path rejected
    env_file = tmp_path / "host.env"
    env_file.write_text("HC_BIND_PORT=8743\n", encoding="utf-8")
    with pytest.raises(ScheduledHostError) as path_err:
        parse_host_env_file(env_file, expected_path=HOST_ENV_PATH)
    assert path_err.value.code == "env_path_forbidden"
    # Explicit expected_path for TEMP tests
    ok = parse_host_env_file(env_file, expected_path=env_file)
    assert ok.values["HC_BIND_PORT"] == "8743"


def test_manifest_rejects_absolute_paths_and_missing_required(tmp_path: Path):
    release = write_release_copy(
        repo_root=ROOT,
        source_commit=COMMIT_40,
        dest_root=tmp_path / "releases",
    )
    verify_release_manifest(release)
    manifest_path = release / "RELEASE_MANIFEST.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    # Drop required bootstrap → fail closed
    data["files"].pop("scripts/companion_host/bootstrap_companion_host.ps1", None)
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ScheduledHostError) as ei:
        verify_release_manifest(release)
    assert ei.value.code == "manifest_mismatch"
    # Absolute path escape
    release2 = write_release_copy(
        repo_root=ROOT,
        source_commit=COMMIT_40,
        dest_root=tmp_path / "releases2",
    )
    m2 = json.loads((release2 / "RELEASE_MANIFEST.json").read_text(encoding="utf-8"))
    m2["files"][r"C:\Windows\System32\cmd.exe"] = "A" * 64
    (release2 / "RELEASE_MANIFEST.json").write_text(json.dumps(m2), encoding="utf-8")
    with pytest.raises(ScheduledHostError) as ei2:
        verify_release_manifest(release2)
    assert ei2.value.code == "manifest_mismatch"


def test_fixed_executable_rejects_symlink(tmp_path: Path):
    tools = tmp_path / "ProgramData" / "HealthChecker" / "tools" / "python"
    tools.mkdir(parents=True)
    real = tmp_path / "evil" / "python.exe"
    real.parent.mkdir(parents=True)
    real.write_bytes(b"MZ")
    link = tools / "python.exe"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlink creation requires privilege on this host")
    # Monkeypatch TOOLS_ROOT via assessing literal under tmp tools is out of scope;
    # assert_fixed_executable uses fixed TOOLS_ROOT. Test assess + override instead.
    with pytest.raises(ScheduledHostError) as ei:
        assert_fixed_executable(Path(r"C:\Users\wadys\AppData\Local\Programs\Python\Python312\python.exe"), allowed=FIXED_PYTHON_PATH)
    assert ei.value.code in {"executable_override_rejected", "executable_privilege_risk"}
    assessment = assess_executable_for_system_task(link)
    assert assessment.acceptable_for_system_task is False


def test_companion_precedes_caddy_and_bounded_health_wait():
    policy = scheduled_task_settings_contract()
    assert policy["startup_order"] == [TASK_COMPANION_HOST, TASK_COMPANION_PROXY]
    assert policy["shutdown_order"] == [TASK_COMPANION_PROXY, TASK_COMPANION_HOST]
    assert policy["proxy_startup_delay_seconds"] == PROXY_STARTUP_DELAY_SECONDS
    assert policy["companion_healthz_timeout_seconds"] == 60
    proxy = (SCRIPTS / "bootstrap_companion_proxy.ps1.template").read_text(encoding="utf-8")
    assert "HealthTimeoutSec = 60" in proxy
    assert "HealthPollSec = 2" in proxy
    assert "Wait-HcCompanionHealthz" in proxy
    assert "healthz_timeout" in proxy
    install = (SCRIPTS / "install_scheduled_tasks.ps1.template").read_text(encoding="utf-8")
    assert "PT15S" in install
    assert "HealthCheckerCompanionHost" in install
    assert "HealthCheckerCompanionProxy" in install
    # Companion registered before proxy in template order
    assert install.index("HealthCheckerCompanionHost") < install.index(
        "HealthCheckerCompanionProxy"
    )


def test_ignore_new_bounded_restart_no_reboot():
    policy = public_policy_dict()
    assert policy["multiple_instances"] == MULTIPLE_INSTANCES_POLICY == "IgnoreNew"
    assert policy["restart_count"] == RESTART_COUNT == 3
    assert policy["restart_interval_minutes"] == RESTART_INTERVAL_MINUTES == 1
    assert policy["reboot_on_failure"] is False
    install = (SCRIPTS / "install_scheduled_tasks.ps1.template").read_text(encoding="utf-8")
    assert "IgnoreNew" in install
    assert "RestartCount 3" in install
    assert "RestartInterval" in install
    assert "Restart-Computer" not in install
    assert not re.search(r"(?i)shutdown\s+/r", install)
    # Forbidden note "reboot-on-failure" may appear; no reboot action cmdlets
    assert "reboot-on-failure" in install.lower()


def test_no_serve_funnel_firewall_forbidden_ports():
    policy = scheduled_task_settings_contract()
    assert policy["auto_configure_serve"] is False
    assert policy["auto_configure_funnel"] is False
    assert policy["firewall_changes_allowed"] is False
    assert set(policy["forbidden_ports"]) == FORBIDDEN_PORTS
    for name in (
        "bootstrap_companion_host.ps1.template",
        "bootstrap_companion_proxy.ps1.template",
        "install_scheduled_tasks.ps1.template",
        "control_scheduled_tasks.ps1.template",
    ):
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        low = text.lower()
        for line in text.splitlines():
            code = line.split("#", 1)[0].lower()
            assert "tailscale serve" not in code
            assert "tailscale funnel" not in code
            assert "new-netfirewallrule" not in code
            assert "netsh advfirewall" not in code
        assert "8765" in text and "8877" in text
        assert "forbidden" in low or "8765" in text


def test_uninstall_targets_only_exact_tasks_preserves_vault():
    assert EXACT_HEALTHCHECKER_TASK_NAMES == {
        "HealthCheckerCompanionHost",
        "HealthCheckerCompanionProxy",
    }
    assert_uninstall_task_name_allowed("HealthCheckerCompanionHost")
    with pytest.raises(ScheduledHostError) as ei:
        assert_uninstall_task_name_allowed("SomeOtherTask")
    assert ei.value.code == "task_name_forbidden"
    control = (SCRIPTS / "control_scheduled_tasks.ps1.template").read_text(encoding="utf-8")
    assert "Unregister-ScheduledTask" in control
    assert "vault_preserved" in control
    assert "host_env_preserved" in control
    assert "secrets_preserved" in control
    for line in control.splitlines():
        code = line.split("#", 1)[0].lower()
        if "remove-item" in code:
            assert "monitoring_vault" not in code
            assert "host.env" not in code
            assert "\\secrets" not in code
    assert "Remove-Item" not in control


def test_packaging_excludes_private_records_and_artifacts(tmp_path: Path):
    assert path_is_excluded("vault_storage/index.json")
    assert path_is_excluded("private_imports/x.pdf")
    assert path_is_excluded("android/app/build/outputs/apk/debug/app-debug.apk")
    assert path_is_excluded("docs/readme.md")
    assert path_is_excluded("tests/test_foo.py")
    assert path_is_excluded("frontend/dist/index.js")
    files = iter_release_source_files(ROOT)
    rels = [p.relative_to(ROOT).as_posix() for p in files]
    assert any(r.startswith("backend/health_vault/companion_host/") for r in rels)
    assert any(r.endswith("bootstrap_companion_host.ps1.template") for r in rels)
    assert "backend/health_vault/config/monitoring_thresholds.json" in rels
    assert "backend/health_vault/config/guardian_rules.json" in rels
    assert "backend/health_vault/config/executive_dashboard.json" not in rels
    for r in rels:
        assert "vault_storage" not in r
        assert "private_imports" not in r
        assert not r.startswith("tests/")
        assert not r.startswith("docs/")
        assert "android" not in r.lower()
        assert not r.endswith(".apk")
    release = write_release_copy(
        repo_root=ROOT, source_commit=COMMIT_40, dest_root=tmp_path / "releases"
    )
    manifest = verify_release_manifest(release)
    assert manifest["source_commit"] == COMMIT_40
    assert (release / "SOURCE_COMMIT.txt").read_text(encoding="utf-8").strip() == COMMIT_40
    # Bootstraps present without .template suffix
    assert (release / "scripts/companion_host/bootstrap_companion_host.ps1").is_file()
    assert (release / "scripts/companion_host/host_env_loader.ps1").is_file()
    built = build_release_manifest(repo_root=ROOT, source_commit=COMMIT_40)
    assert built["rejected_wrappers"] == list(REJECTED_SERVICE_WRAPPERS)
    # Dirty working tree must fail closed when require_clean_commit=True
    from backend.health_vault.companion_host.scheduled_host import (
        assert_packaging_matches_commit,
    )

    with pytest.raises(ScheduledHostError) as dirty:
        assert_packaging_matches_commit(ROOT, COMMIT_40)
    assert dirty.value.code == "manifest_mismatch"


def test_powershell_templates_parse_cleanly_and_remain_inert():
    templates = list(SCRIPTS.glob("*.ps1.template"))
    assert templates
    for path in templates:
        _ps_parse(path)
    # Inert without approval
    for name in (
        "package_verified_release.ps1.template",
        "install_scheduled_tasks.ps1.template",
        "control_scheduled_tasks.ps1.template",
    ):
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        assert APPROVAL_SCHEDULED_HOST in text
        assert "I_UNDERSTAND" in text
    # Loader allowlist matches Python
    loader = (SCRIPTS / "host_env_loader.ps1.template").read_text(encoding="utf-8")
    for key in sorted(ALLOWED_HOST_ENV_KEYS):
        assert key in loader
    assert "Invoke-Expression" in loader  # mentioned as forbidden
    assert FIXED_PYTHON_PATH.as_posix().replace("/", "\\") in (
        SCRIPTS / "bootstrap_companion_host.ps1.template"
    ).read_text(encoding="utf-8") or str(FIXED_PYTHON_PATH) in (
        SCRIPTS / "bootstrap_companion_host.ps1.template"
    ).read_text(encoding="utf-8")
    assert str(FIXED_CADDY_PATH) in (
        SCRIPTS / "bootstrap_companion_proxy.ps1.template"
    ).read_text(encoding="utf-8")


def test_docs_mark_nssm_rejected_task_scheduler_active():
    doc_b = (ROOT / "docs" / "HC304B_PRIVATE_HOST_FOUNDATION.md").read_text(encoding="utf-8")
    doc_a = (ROOT / "docs" / "HC304A_PERMANENT_HOST_READINESS.md").read_text(encoding="utf-8")
    for doc in (doc_a, doc_b):
        assert "Task Scheduler" in doc or "scheduled" in doc.lower()
        assert "NSSM" in doc
        assert "REJECTED" in doc or "Rejected" in doc
    assert "HealthCheckerCompanionHost" in doc_b
    assert "HealthCheckerCompanionProxy" in doc_b


def test_install_scheduled_tasks_materializes_verified_release_caddyfile():
    script = (
        SCRIPTS / "install_scheduled_tasks.ps1.template"
    ).read_text(encoding="utf-8")

    assert "scripts\\companion_host\\Caddyfile" in script
    assert "C:\\ProgramData\\HealthChecker\\companion_host\\Caddyfile" in script
    assert "Copy-Item" in script
    assert "-LiteralPath $releaseCaddyfile" in script
    assert "-Destination $liveCaddyfile" in script
    assert "Get-FileHash -LiteralPath $releaseCaddyfile -Algorithm SHA256" in script
    assert "Get-FileHash -LiteralPath $liveCaddyfile -Algorithm SHA256" in script
    assert "if ($releaseCaddyHash -ne $liveCaddyHash)" in script
