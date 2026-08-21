"""HC321-B3: Android version closure + governed signing / provenance assertions."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GRADLE = (ROOT / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
ROOT_GRADLE = (ROOT / "android" / "build.gradle.kts").read_text(encoding="utf-8")
RUNBOOK = (ROOT / "docs" / "ops" / "HC321_B3_ANDROID_PRODUCTION_SIGNING_RUNBOOK.md").read_text(
    encoding="utf-8"
)
README = (ROOT / "android" / "README.md").read_text(encoding="utf-8")
PROVENANCE_SCRIPT = (
    ROOT / "scripts" / "Write-HealthCheckerAndroidReleaseProvenance.ps1"
).read_text(encoding="utf-8")
RELEASE = json.loads((ROOT / "config" / "healthchecker.release.json").read_text(encoding="utf-8"))

ANDROID_VERSION_CODE = 321
ANDROID_VERSION_NAME = "0.321.0"
DESKTOP_VERSION = "0.321.0"
PRIOR_ANDROID_VERSION_CODE = 320

ENV_VARS = (
    "HC_ANDROID_KEYSTORE_FILE",
    "HC_ANDROID_KEYSTORE_PASSWORD",
    "HC_ANDROID_KEY_ALIAS",
    "HC_ANDROID_KEY_PASSWORD",
)

SECRET_PATTERNS = (
    re.compile(r"(?i)storePassword\s*=\s*\"[^\"]+\""),
    re.compile(r"(?i)keyPassword\s*=\s*\"[^\"]+\""),
    re.compile(r"(?i)password\s*=\s*\"[^\"]{4,}\""),
    re.compile(r"BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY"),
)


def _pwsh(*args: str, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            *args,
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=check,
        env=env,
    )


def test_android_version_advanced_monotonically_to_321():
    assert f"versionCode = {ANDROID_VERSION_CODE}" in GRADLE
    assert f'versionName = "{ANDROID_VERSION_NAME}"' in GRADLE
    assert ANDROID_VERSION_CODE > PRIOR_ANDROID_VERSION_CODE
    # Desktop release metadata must remain untouched by B3 versioning work.
    assert RELEASE["version"] == DESKTOP_VERSION
    assert "0.321.0" in README
    assert "321" in README
    assert "320" in README  # prior line documented for monotonicity


def test_signing_is_env_driven_fail_closed_no_debug_fallback():
    for name in ENV_VARS:
        assert name in GRADLE
        assert name in RUNBOOK
    assert "HC_ANDROID_REQUIRE_PRODUCTION_SIGNING" in GRADLE
    assert "hc_android_signing_env_incomplete" in GRADLE
    assert "hc_android_production_signing_required" in GRADLE
    assert "hc_android_keystore_missing" in GRADLE
    assert "hc_android_keystore_path_forbidden" in GRADLE
    assert "debug.keystore" in GRADLE
    assert "signingConfig = signingConfigs.getByName(\"debug\")" not in GRADLE
    release_block = GRADLE.split("release {", 1)[1].split("compileOptions", 1)[0]
    assert "debug" not in release_block.lower() or "debug.keystore" in GRADLE
    assert "getByName(\"production\")" in release_block
    # Production config only when externalSigningReady; no hard-coded passwords.
    assert "storePassword = signingStorePassword" in GRADLE
    assert "keyPassword = signingKeyPassword" in GRADLE
    assert 'storePassword = "' not in GRADLE
    assert 'keyPassword = "' not in GRADLE
    assert "System.getenv(\"HC_ANDROID_KEYSTORE_PASSWORD\")" in GRADLE


def test_no_committed_keystore_or_hardcoded_passwords_in_android_tree():
    android_root = ROOT / "android"
    forbidden_suffixes = (".jks", ".keystore", ".p12", ".pfx")
    for path in android_root.rglob("*"):
        if not path.is_file():
            continue
        rel = str(path.relative_to(ROOT)).replace("\\", "/").lower()
        if "/build/" in f"/{rel}/":
            continue
        for suffix in forbidden_suffixes:
            assert not rel.endswith(suffix), f"committed keystore-like artifact: {rel}"
        if path.suffix.lower() in {".kts", ".gradle", ".properties", ".md", ".kt", ".xml", ".ps1"}:
            text = path.read_text(encoding="utf-8", errors="replace")
            for pat in SECRET_PATTERNS:
                assert not pat.search(text), f"secret-like pattern in {rel}"


def test_runbook_covers_governed_signing_and_key_custody():
    required = (
        "org-controlled",
        "outside Git",
        "HC_ANDROID_KEYSTORE_FILE",
        "HC_ANDROID_KEYSTORE_PASSWORD",
        "HC_ANDROID_KEY_ALIAS",
        "HC_ANDROID_KEY_PASSWORD",
        "bundleRelease",
        "versionCode",
        "monotonic",
        "debug keystore",
        "Key loss",
        "RELEASE/SIGNING OWNER — ASSIGN BEFORE EXTERNAL PRODUCTION HANDOFF",
        "BLOCKED_EXTERNAL_KEY_CUSTODY",
        "without uninstalling",
    )
    lower = RUNBOOK.lower()
    for token in required:
        assert token.lower() in lower, f"missing runbook coverage: {token}"


def test_provenance_script_is_non_secret_and_distinguishes_unsigned():
    assert "hc.android.release.provenance.v1" in PROVENANCE_SCRIPT
    assert "BLOCKED_EXTERNAL_KEY_CUSTODY" in PROVENANCE_SCRIPT
    assert "SIGNED_VERIFIED" in PROVENANCE_SCRIPT
    assert "UNSIGNED" in PROVENANCE_SCRIPT
    assert "secrets_recorded" in PROVENANCE_SCRIPT
    assert "GetEnvironmentVariable" in PROVENANCE_SCRIPT
    # Must not echo password env values into artifacts.
    assert "$env:HC_ANDROID_KEYSTORE_PASSWORD" not in PROVENANCE_SCRIPT
    assert "HC_ANDROID_KEYSTORE_PASSWORD)" in PROVENANCE_SCRIPT or "HC_ANDROID_KEYSTORE_PASSWORD\"" in PROVENANCE_SCRIPT
    assert "device_upgrade_proof" in PROVENANCE_SCRIPT
    assert "sha256" in PROVENANCE_SCRIPT.lower()


def test_provenance_script_parser_ok_and_emits_files(tmp_path: Path):
    out = tmp_path / "prov"
    result = _pwsh(
        "-File",
        str(ROOT / "scripts" / "Write-HealthCheckerAndroidReleaseProvenance.ps1"),
        "-OutputDirectory",
        str(out),
        "-DeviceUpgradeProof",
        "BLOCKED_EXTERNAL_KEY_CUSTODY",
        "-Notes",
        "hc321-b3-unit-test",
    )
    lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
    assert len(lines) >= 2
    json_path = Path(lines[0])
    txt_path = Path(lines[1])
    assert json_path.is_file() and txt_path.is_file()
    doc = json.loads(json_path.read_text(encoding="utf-8-sig"))
    assert doc["format"] == "hc.android.release.provenance.v1"
    assert doc["android_version_code"] == ANDROID_VERSION_CODE
    assert doc["android_version_name"] == ANDROID_VERSION_NAME
    assert doc["hc_release_version"] == DESKTOP_VERSION
    assert doc["secrets_recorded"] is False
    assert doc["production_signing_status"] in {
        "BLOCKED_EXTERNAL_KEY_CUSTODY",
        "AVAILABLE_AND_VERIFIED",
        "ENV_PRESENT_VERIFY_INCOMPLETE",
    }
    blob = (json_path.read_text(encoding="utf-8-sig") + "\n" + txt_path.read_text(encoding="utf-8-sig"))
    for forbidden in ("BEGIN PRIVATE KEY", "keystore_password=", "key_password=", "HC_ANDROID_KEYSTORE_PASSWORD="):
        assert forbidden not in blob
    assert "HC_ANDROID_KEY_PASSWORD=" not in blob


def test_unsigned_vs_signed_status_vocabulary_documented():
    assert "UNSIGNED" in RUNBOOK or "unsigned" in RUNBOOK.lower()
    assert "SIGNED" in PROVENANCE_SCRIPT or "SIGNED_VERIFIED" in PROVENANCE_SCRIPT
    assert "BLOCKED_EXTERNAL_KEY_CUSTODY" in RUNBOOK
