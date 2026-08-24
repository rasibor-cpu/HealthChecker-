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


def test_screenshot_policy_is_clear_only():
    policy = (MAIN_KT / "java/com/healthchecker/companion/ui/ScreenshotPolicy.kt").read_text(
        encoding="utf-8"
    )
    assert "HAS_PROTECTED_SCREENS: Boolean = false" in policy
    assert "fun isScreenshotBlockingEnabled(): Boolean = false" in policy
    assert "clearFlags(WindowManager.LayoutParams.FLAG_SECURE)" in policy
    assert "addFlags(WindowManager.LayoutParams.FLAG_SECURE)" not in policy


def test_secure_window_policy_does_not_enable_blocking():
    policy = (MAIN_KT / "java/com/healthchecker/companion/ui/SecureWindowPolicy.kt").read_text(
        encoding="utf-8"
    )
    assert "fun shouldSecureWindow" in policy
    assert "return false" in policy
