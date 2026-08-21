"""HC-319D Android launcher and hybrid boundary acceptance tests."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.health_vault.api import create_health_vault_app
from backend.health_vault.vault_store import VaultStore

ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android/app/src/main"


def test_existing_application_id_and_launcher_activity_are_reused():
    gradle = (ROOT / "android/app/build.gradle.kts").read_text(encoding="utf-8")
    manifest = (ANDROID / "AndroidManifest.xml").read_text(encoding="utf-8")
    assert 'applicationId = "com.healthchecker.companion"' in gradle
    assert '.ui.ConsumerLauncherActivity' in manifest
    launcher_block = manifest.split('.ui.ConsumerLauncherActivity', 1)[1].split("</activity>", 1)[0]
    assert "android.intent.action.MAIN" in launcher_block
    assert "android.intent.category.LAUNCHER" in launcher_block
    assert '.ui.CompanionStatusActivity' in manifest
    assert 'android:icon="@mipmap/ic_launcher"' in manifest


def test_launcher_is_hardened_and_has_no_javascript_bridge():
    source = (ANDROID / "java/com/healthchecker/companion/ui/ConsumerLauncherActivity.kt").read_text(
        encoding="utf-8"
    )
    policy = (ANDROID / "java/com/healthchecker/companion/ui/SecureWindowPolicy.kt").read_text(
        encoding="utf-8"
    )
    required = (
        "allowFileAccess = false", "allowContentAccess = false",
        "allowFileAccessFromFileURLs = false", "allowUniversalAccessFromFileURLs = false",
        "MIXED_CONTENT_NEVER_ALLOW", "LOAD_NO_CACHE", "setAcceptThirdPartyCookies(webView, false)",
        "handler?.cancel()", "FLAG_SECURE", "ConsumerOriginPolicy", "clearUserScopedState",
        "SecureWindowPolicy", "applySecureWindow", "refreshSecureWindowFromDom",
    )
    assert all(term in source for term in required)
    assert "window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)" not in source.split("fun onCreate", 1)[1].split("setContentView", 1)[0]
    assert "shouldSecureWindow" in policy
    assert "addJavascriptInterface" not in source
    assert "setWebContentsDebuggingEnabled(true)" not in source
    assert "ACTION_VIEW" not in source
    assert "prefs.getConsumerOrigin() ?: debugConsumerOrigin()" in source
    assert 'DEBUG_CONSUMER_ORIGIN = "http://localhost:8766"' in source
    assert "prefs.getConsumerOrigin() ?: prefs.getHostUrl()" not in source


def test_origin_policy_is_explicit_and_excludes_legacy_and_sensitive_paths():
    source = (ANDROID / "java/com/healthchecker/companion/consumer/ConsumerOriginPolicy.kt").read_text(
        encoding="utf-8"
    )
    for allowed in ("/mobile", "/style.css", "/js/health_vault/mobile_consumer.js", "/api/"):
        assert allowed in source
    for forbidden in ("vault_storage", "hc_intake", "evidence", "scratch", "file://"):
        assert forbidden not in source
    assert 'path == "/"' not in source
    assert 'path == "/index.html"' not in source


def test_mobile_logout_revokes_all_owned_devices_and_session(tmp_path):
    store = VaultStore(root=tmp_path / "vault", encryption_key=b"D" * 32)
    app = create_health_vault_app(store)
    auth = app.state.auth_service
    auth.create_user(
        user_id="mobile-owner", name="Mobile Owner", email_identifier="owner@test.invalid",
        password="Mobile-Owner-Password", must_change_password=False,
    )
    client = TestClient(app)
    login = client.post(
        "/api/auth/login", json={"user_id": "mobile-owner", "password": "Mobile-Owner-Password"}
    ).json()
    headers = {"Authorization": f"Bearer {login['token']}"}
    started = client.post("/api/companion/pair/start", headers=headers, json={"display_name": "S24"})
    confirmed = client.post(
        "/api/companion/pair/confirm",
        json={"pair_code": started.json()["pair_code"], "device_label": "S24"},
    )
    assert confirmed.status_code == 200
    logged_out = client.post(
        "/api/auth/logout", headers=headers, json={"revoke_companion_devices": True}
    )
    assert logged_out.status_code == 200
    assert logged_out.json()["devices_revoked"] == 1
    assert client.get("/api/auth/session", headers=headers).status_code == 401
    assert store.get_companion_device(confirmed.json()["device_id"])["revoked"] is True


def test_mobile_consumer_contract_covers_required_experience_without_local_phi():
    html = (ROOT / "mobile.html").read_text(encoding="utf-8")
    js = (ROOT / "js/health_vault/mobile_consumer.js").read_text(encoding="utf-8")
    for destination in ("dashboard", "records", "trends", "observations", "import", "settings"):
        assert f'data-mobile-view="{destination}"' in html
    for endpoint in ("/api/auth/login", "/api/auth/session", "/api/auth/password/change",
                     "/api/auth/logout", "/api/dashboard/summary", "/api/records", "/api/records/upload"):
        assert endpoint in js
    assert "revoke_companion_devices: true" in js
    assert "/mobile/native-logout-complete" in js
    for forbidden in ("localStorage", "indexedDB", "HCHealthVault", "caches.open", "cache.put"):
        assert forbidden not in js


def test_debug_connectivity_uses_adb_reverse_and_loopback_only():
    source = (ROOT / "scripts/start_healthchecker_android_debug.ps1").read_text(encoding="utf-8")
    assert 'adb reverse "tcp:$Port" "tcp:$Port"' in source
    assert "start_healthchecker.ps1" in source
    assert "127.0.0.1" in source
    assert "0.0.0.0" not in source
    assert "Exactly one authorized Android device" in source
    assert "[int]$Port = 8766" in source
