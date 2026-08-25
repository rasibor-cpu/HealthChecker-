"""HC325-R6 — Android production origin lock.

Static/source proofs only. Does not talk to live :8766, restart the host,
touch CSS :8765, or mutate production vault/auth data.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android/app/src/main"
LAUNCHER = ANDROID / "java/com/healthchecker/companion/ui/ConsumerLauncherActivity.kt"
LOCK = ANDROID / "java/com/healthchecker/companion/consumer/ConsumerOriginLock.kt"
MANIFEST = ANDROID / "AndroidManifest.xml"
NSC = ANDROID / "res/xml/network_security_config.xml"
BACK = ANDROID / "java/com/healthchecker/companion/ui/ConsumerInAppBackPolicy.kt"
MOBILE_JS = ROOT / "js/health_vault/mobile_consumer.js"
NAV_JS = ROOT / "js/health_vault/consumer_nav.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_production_origin_is_governed_https_mobile():
    lock = _read(LOCK)
    launcher = _read(LAUNCHER)
    assert 'PRODUCTION_ORIGIN = "https://health.capitalstratasystems.com"' in lock
    assert 'PRODUCTION_MOBILE_URL = "$PRODUCTION_ORIGIN$PRODUCTION_MOBILE_PATH"' in lock
    assert "ConsumerOriginLock.resolve" in launcher
    assert "resolution.mobileUrl" in launcher
    assert 'loadUrl("http://localhost:8766/mobile")' not in launcher


def test_localhost_and_loopback_are_never_automatic_fallbacks():
    lock = _read(LOCK)
    launcher = _read(LAUNCHER)
    assert 'DEBUG_CONSUMER_ORIGIN = "http://localhost:8766"' not in launcher
    assert "debugConsumerOrigin()" not in launcher
    assert "prefs.getConsumerOrigin() ?: debugConsumerOrigin()" not in launcher
    assert "http://localhost:8766" not in lock
    assert "http://localhost:8766" not in launcher
    assert "10.0.2.2" in lock
    assert "isForbiddenLoopbackHost" in lock
    assert "EXTRA_EXPLICIT_LOCAL_DEV" in lock
    assert "explicitLocalDevRequested" in launcher


def test_webview_state_and_intents_cannot_restore_loopback():
    launcher = _read(LAUNCHER)
    lock = _read(LOCK)
    assert "webView.restoreState" not in launcher
    assert "webView.saveState" not in launcher
    assert "shouldRestoreWebViewState(): Boolean = false" in lock
    assert "savedStateUrl = null" in launcher
    assert "onNewIntent" in launcher
    assert "ACTION_VIEW" not in launcher
    assert "usesCleartextTraffic" not in _read(MANIFEST)
    nsc = _read(NSC)
    assert 'cleartextTrafficPermitted="false"' in nsc
    assert 'cleartextTrafficPermitted="true"' not in nsc


def test_android_back_does_not_use_webview_history():
    launcher = _read(LAUNCHER)
    back = _read(BACK)
    assert "webView.goBack()" not in launcher
    assert "goBack()" not in back
    assert "ConsumerOriginLock.mustRecover" in launcher
    assert "HCConsumerNav.handleSystemBack()" in back


def test_r5a_password_and_r4_nav_contracts_remain():
    js = _read(MOBILE_JS)
    nav = _read(NAV_JS)
    html = _read(ROOT / "mobile.html")
    assert "password_change_required" in js
    assert "recovery_enrollment_required" in js
    assert "/api/auth/password/change" in js
    assert "recovery_answers" in js or "/api/auth/recovery" in js
    assert "setSecurityGate" in nav
    assert "mobile_consumer.js" in html
