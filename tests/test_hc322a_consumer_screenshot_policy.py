"""HC-322A — consumer launcher must not persist FLAG_SECURE after auth."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANDROID_SRC = ROOT / "android" / "app" / "src"
MAIN_KT = ANDROID_SRC / "main"


def test_launcher_never_sets_flag_secure():
    source = (
        MAIN_KT / "java/com/healthchecker/companion/ui/ConsumerLauncherActivity.kt"
    ).read_text(encoding="utf-8")
    assert "addFlags(WindowManager.LayoutParams.FLAG_SECURE)" not in source
    assert "applySecureWindow(true)" not in source
    assert "refreshSecureWindowFromDom" not in source
    assert source.count("ScreenshotPolicy.applyConsumerScreenshotPolicy(window)") >= 3


def test_screenshot_policy_is_route_specific():
    policy = (MAIN_KT / "java/com/healthchecker/companion/ui/ScreenshotPolicy.kt").read_text(
        encoding="utf-8"
    )
    assert "HAS_PROTECTED_SCREENS: Boolean = true" in policy
    assert "fun isScreenshotBlockingEnabled(): Boolean = HAS_PROTECTED_SCREENS" in policy
    assert 'route == "settings" || route == "password_recovery"' in policy
    assert "clearFlags(WindowManager.LayoutParams.FLAG_SECURE)" in policy
    assert "addFlags(WindowManager.LayoutParams.FLAG_SECURE)" in policy


def test_password_and_recovery_entry_routes_are_protected():
    secure_policy = (
        MAIN_KT / "java/com/healthchecker/companion/ui/SecureWindowPolicy.kt"
    ).read_text(encoding="utf-8")
    launcher = (
        MAIN_KT / "java/com/healthchecker/companion/ui/ConsumerLauncherActivity.kt"
    ).read_text(encoding="utf-8")
    mobile = (ROOT / "js" / "health_vault" / "mobile_consumer.js").read_text(encoding="utf-8")

    for form_id in (
        "mobile_settings",
        "mobile_password_change",
        "mobile_recovery_flow",
        "mobile_recovery_enroll",
    ):
        assert form_id in secure_policy
    assert "ScreenshotPolicy.isSensitiveRoute(route)" in launcher
    assert 'active ? "password_recovery" : "dashboard"' in mobile


def test_secure_window_policy_does_not_enable_blocking():
    policy = (MAIN_KT / "java/com/healthchecker/companion/ui/SecureWindowPolicy.kt").read_text(
        encoding="utf-8"
    )
    assert "fun shouldSecureWindow" in policy
    assert "return passwordChangeVisible || credentialOrSecretVisible" in policy
