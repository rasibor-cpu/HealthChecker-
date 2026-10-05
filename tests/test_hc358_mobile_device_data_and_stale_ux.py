"""HC-358 - /mobile Device Data route, stale labelling and import wiring contracts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "js/health_vault/mobile_consumer.js").read_text(encoding="utf-8")
HTML = (ROOT / "mobile.html").read_text(encoding="utf-8")
KT = (ROOT / "android/app/src/main/java/com/healthchecker/companion/ui/ConsumerLauncherActivity.kt").read_text(encoding="utf-8")


def test_device_data_route_reaches_native_health_connect_screen():
    assert 'id="mobile_open_device_data"' in HTML
    assert "bridge.openDeviceData()" in JS
    body = KT.split("fun openDeviceData()")[1].split("@JavascriptInterface")[0]
    assert "isFirstPartyBridgeCall()" in body
    assert "runOnUiThread" in body
    assert "openNativeSettings()" in body
    assert "CompanionStatusActivity::class.java" in KT


def test_mobile_trends_label_not_current_from_backend_currentness():
    assert 'trend.currentness !== "current"' in JS
    assert '"Not current"' in JS


def test_mobile_import_uses_preview_confirm_cancel_only():
    assert "/api/records/import-preview" in JS
    assert "/confirm`" in JS and "/cancel`" in JS
    assert '"/api/records/upload"' not in JS
    assert "preview_expired" in JS and "preview_token_invalid" in JS
    assert "will not be added twice" in JS
