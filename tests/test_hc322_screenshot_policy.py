"""
HC-322 — Android screenshot policy static regression.

Ordinary consumer-facing HealthChecker windows must remain screenshot-capable.
Production sources must not set FLAG_SECURE. Login / pairing surfaces are not
automatically screenshot-blocked on this tree.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANDROID_SRC = ROOT / "android" / "app" / "src"
MAIN_KT = ANDROID_SRC / "main"
DEBUG_KT = ANDROID_SRC / "debug"


def _read_kt(root: Path) -> dict[str, str]:
    return {str(p.relative_to(ROOT)): p.read_text(encoding="utf-8") for p in root.rglob("*.kt")}


def test_screenshot_policy_never_enables_blocking():
    policy = (MAIN_KT / "java/com/healthchecker/companion/ui/ScreenshotPolicy.kt").read_text(
        encoding="utf-8"
    )
    assert "object ScreenshotPolicy" in policy
    assert "HAS_PROTECTED_SCREENS: Boolean = false" in policy
    assert "fun isScreenshotBlockingEnabled(): Boolean = false" in policy
    assert "clearFlags(WindowManager.LayoutParams.FLAG_SECURE)" in policy
    assert "addFlags(WindowManager.LayoutParams.FLAG_SECURE)" not in policy
    assert "setFlags(" not in policy


def test_production_sources_do_not_set_flag_secure():
    sources = {**_read_kt(MAIN_KT), **_read_kt(DEBUG_KT)}
    setters = []
    for path, text in sources.items():
        for i, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            if "FLAG_SECURE" not in line:
                continue
            if "clearFlags" in line or "isFlagSecureSet" in line or "attributes.flags and" in line:
                continue
            if "addFlags" in line or "setFlags" in line:
                setters.append(f"{path}:{i}:{stripped}")
    assert setters == []


def test_consumer_activities_apply_screenshot_policy():
    status = (MAIN_KT / "java/com/healthchecker/companion/ui/CompanionStatusActivity.kt").read_text(
        encoding="utf-8"
    )
    rationale = (
        MAIN_KT / "java/com/healthchecker/companion/ui/PermissionsRationaleActivity.kt"
    ).read_text(encoding="utf-8")
    harness = (
        DEBUG_KT / "java/com/healthchecker/companion/ui/ToolbarHarnessActivity.kt"
    ).read_text(encoding="utf-8")
    assert "ScreenshotPolicy.applyConsumerScreenshotPolicy(window)" in status
    assert "ScreenshotPolicy.applyConsumerScreenshotPolicy(window)" in rationale
    assert "ScreenshotPolicy.applyConsumerScreenshotPolicy(window)" in harness


def test_theme_and_manifest_do_not_enable_secure_windows():
    theme = (ANDROID_SRC / "main/res/values/themes.xml").read_text(encoding="utf-8")
    manifest = (ANDROID_SRC / "main/AndroidManifest.xml").read_text(encoding="utf-8")
    assert "FLAG_SECURE" not in theme
    assert "windowIsSecure" not in theme
    assert "FLAG_SECURE" not in manifest
    assert "filterTouchesWhenObscured" not in manifest
