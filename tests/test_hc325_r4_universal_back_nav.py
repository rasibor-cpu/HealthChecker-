"""HC325-R4 — universal consumer back navigation regression tests.

Fictional fixtures only. Does not talk to the live :8766 process or mutate
production vault data.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANDROID_MAIN = ROOT / "android/app/src/main"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _node(script: str) -> str:
    prelude = f"""
const fs = require('fs');
const vm = require('vm');
const session = {{
  _data: {{ hc_mobile_auth_session: JSON.stringify({{ token: 'session-token', userId: 'robert-test' }}) }},
  getItem(key) {{ return this._data[key] || null; }},
  setItem(key, value) {{ this._data[key] = String(value); }},
  removeItem(key) {{ delete this._data[key]; }},
}};
const ctx = {{
  console,
  sessionStorage: session,
  URLSearchParams,
  decodeURIComponent,
  location: {{ search: '', pathname: '/mobile' }},
  document: {{
    body: {{ classList: {{ contains: () => false }} }},
    addEventListener() {{}},
    querySelector() {{ return null; }},
    querySelectorAll() {{ return []; }},
  }},
}};
ctx.globalThis = ctx;
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync({str(ROOT / "js/health_vault/consumer_nav.js")!r}, 'utf8'), ctx);
const Nav = ctx.HCConsumerNav;
const activated = [];
ctx.HCConsumerNavAdapter = {{
  activate(route) {{ activated.push(route); }},
}};
function dump(obj) {{ console.log(JSON.stringify(obj)); }}
{script}
"""
    proc = subprocess.run(["node", "-e", prelude], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout.strip()


def test_secondary_screen_back_returns_to_parent():
    out = _node(
        """
Nav.note('dashboard');
Nav.note('records');
const first = Nav.back();
dump({
  handled: first.handled,
  route: first.route,
  current: Nav.currentRoute(),
  activated,
  session: session.getItem('hc_mobile_auth_session'),
});
"""
    )
    payload = json.loads(out)
    assert payload["handled"] is True
    assert payload["route"] == "dashboard"
    assert payload["current"] == "dashboard"
    assert payload["activated"] == ["dashboard"]
    assert "session-token" in payload["session"]


def test_nested_detail_repeated_back_reaches_dashboard():
    out = _node(
        """
Nav.note('dashboard');
Nav.note('records');
Nav.pushOverlay('record-detail', () => activated.push('close-detail'));
const overlayBack = Nav.back();
Nav.note('trends');
const fromTrends = Nav.back();
const fromRecords = Nav.back();
const atDash = Nav.back();
dump({
  overlayBack,
  fromTrends,
  fromRecords,
  atDash,
  current: Nav.currentRoute(),
  canLeave: Nav.canLeaveApp(),
  stack: Nav.stackRoutes(),
  activated,
  session: session.getItem('hc_mobile_auth_session'),
});
"""
    )
    payload = json.loads(out)
    assert payload["overlayBack"]["handled"] is True
    assert payload["overlayBack"]["overlay"] == "record-detail"
    assert payload["fromTrends"]["route"] == "records"
    assert payload["fromRecords"]["route"] == "dashboard"
    assert payload["atDash"]["handled"] is False
    assert payload["current"] == "dashboard"
    assert payload["canLeave"] is True
    assert payload["stack"] == []
    assert payload["activated"][0] == "close-detail"
    assert "session-token" in payload["session"]


def test_deep_entry_without_history_falls_back_to_dashboard():
    out = _node(
        """
Nav.reset();
Nav.note('timeline', { deepLink: true });
const first = Nav.back();
const second = Nav.back();
dump({
  deep: Nav.peekDeepLink('?view=records'),
  screen: Nav.peekDeepLink('?screen=consumer_trends_screen'),
  dash: Nav.peekDeepLink('?view=dashboard'),
  first,
  second,
  current: Nav.currentRoute(),
  fallback: first.fallback === true,
});
"""
    )
    payload = json.loads(out)
    assert payload["deep"] == "records"
    assert payload["screen"] == "trends"
    assert payload["dash"] is None
    assert payload["first"]["handled"] is True
    assert payload["first"]["route"] == "dashboard"
    assert payload["fallback"] is True
    assert payload["second"]["handled"] is False


def test_dashboard_does_not_create_back_loop():
    out = _node(
        """
Nav.note('records');
Nav.note('dashboard');
Nav.note('dashboard');
const first = Nav.back();
Nav.note('settings');
Nav.note('dash');
const second = Nav.back();
dump({
  current: Nav.currentRoute(),
  stack: Nav.stackRoutes(),
  firstHandled: first.handled,
  secondHandled: second.handled,
  canLeave: Nav.canLeaveApp(),
  leave: Nav.handleSystemBack(),
});
"""
    )
    payload = json.loads(out)
    assert payload["current"] == "dashboard"
    assert payload["stack"] == []
    assert payload["firstHandled"] is False
    assert payload["secondHandled"] is False
    assert payload["canLeave"] is True
    assert payload["leave"] is False


def test_authenticated_session_survives_navigation():
    nav_src = _read("js/health_vault/consumer_nav.js")
    out = _node(
        """
const before = session.getItem('hc_mobile_auth_session');
Nav.note('records');
Nav.note('trends');
Nav.note('observations');
Nav.back();
Nav.back();
Nav.back();
const after = session.getItem('hc_mobile_auth_session');
dump({
  before,
  after,
  same: before === after,
});
"""
    )
    payload = json.loads(out)
    assert payload["same"] is True
    assert "session-token" in payload["after"]
    assert "sessionStorage.setItem" not in nav_src
    assert "sessionStorage.removeItem" not in nav_src
    assert "clearSession" not in nav_src


def test_visible_back_controls_exist_on_secondary_screens():
    mobile = _read("mobile.html")
    desktop = _read("index.html")
    for view in ("records", "trends", "observations", "timeline", "reports", "import", "settings"):
        section = mobile.split(f'id="mobile_{view}"', 1)[1].split("</section>", 1)[0]
        assert "data-hc-back" in section
        assert "← Back" in section
    assert 'id="mobile_dashboard"' in mobile
    dash = mobile.split('id="mobile_dashboard"', 1)[1].split('id="mobile_records"', 1)[0]
    assert "data-hc-back" not in dash
    for screen in (
        "consumer_trends_screen",
        "consumer_observations_screen",
        "consumer_timeline_screen",
        "consumer_reports_screen",
        "consumer_settings_screen",
        "health_records_screen",
    ):
        block = desktop.split(f'id="{screen}"', 1)[1]
        assert "data-hc-back" in block[:800]
    assert 'src="/js/health_vault/consumer_nav.js"' in mobile
    assert "js/health_vault/consumer_nav.js" in desktop
    assert mobile.index("/js/health_vault/consumer_nav.js") < mobile.index(
        "/js/health_vault/health_snapshot.js"
    )
    assert mobile.index("/js/health_vault/health_snapshot.js") < mobile.index(
        "/js/health_vault/mobile_consumer.js"
    )


def test_android_system_back_uses_in_app_hierarchy_not_webview_history():
    launcher = (
        ANDROID_MAIN / "java/com/healthchecker/companion/ui/ConsumerLauncherActivity.kt"
    ).read_text(encoding="utf-8")
    policy = (
        ANDROID_MAIN / "java/com/healthchecker/companion/ui/ConsumerInAppBackPolicy.kt"
    ).read_text(encoding="utf-8")
    origin = (
        ANDROID_MAIN / "java/com/healthchecker/companion/consumer/ConsumerOriginPolicy.kt"
    ).read_text(encoding="utf-8")
    handle = launcher.split("handleOnBackPressed", 1)[1].split("loadConsumer()", 1)[0]
    assert "ConsumerInAppBackPolicy.HANDLE_SCRIPT" in handle
    assert "evaluateJavascript" in handle
    assert "didHandleInApp" in handle
    assert "webView.goBack" not in launcher
    assert "canGoBack" not in launcher
    assert "addJavascriptInterface" not in launcher
    assert "HCConsumerNav.handleSystemBack" in policy
    assert 'path == "/js/health_vault/consumer_nav.js"' in origin
    assert "parsed.fragment != null" in origin
    assert "ScreenshotPolicy.applyConsumerScreenshotPolicy" in handle


def test_existing_mobile_launcher_and_health_snapshot_contracts_remain():
    mobile = _read("mobile.html")
    js = _read("js/health_vault/mobile_consumer.js")
    snap = _read("js/health_vault/health_snapshot.js")
    launcher = (
        ANDROID_MAIN / "java/com/healthchecker/companion/ui/ConsumerLauncherActivity.kt"
    ).read_text(encoding="utf-8")
    assert 'id="hc_health_snapshot"' in mobile
    assert "HCHealthSnapshot.refresh" in js
    assert "hc:session-changed" in js
    assert "HCConsumerNav.note" in js
    assert "HCConsumerNavAdapter" in js
    assert "peekDeepLink" in js
    assert "hc-drill-back" in snap
    assert 'id="hc_snapshot_back"' in snap
    assert "pushOverlay(" in snap and '"snapshot-drill"' in snap
    assert "ScreenshotPolicy.applyConsumerScreenshotPolicy" in launcher
    assert "FLAG_SECURE" not in launcher or "addFlags" not in launcher
    assert "vault_store.js" not in mobile
    assert "service-worker.js" not in mobile
    assert "localStorage" not in js
    assert "sessionStorage" in js
    assert 'data-mobile-view="dashboard"' in mobile
    html = _read("index.html")
    assert 'id="consumer_dashboard_container"' in html
    assert 'data="dash"' in html
    assert "HCConsumerNav.note(screenId)" in html
    surfaces = _read("js/health_vault/consumer_surfaces.js")
    assert "HCConsumerNav.note(screenId)" in surfaces
    assert "activateConsumerScreen(screenId)" in surfaces
    sw = _read("service-worker.js")
    assert "consumer_nav" in sw
