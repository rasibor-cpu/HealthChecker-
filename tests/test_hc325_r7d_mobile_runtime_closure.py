"""HC325-R7D — mobile data load + Settings interaction closure.

Fictional fixtures only. Does not talk to live :8766, restart the host,
touch CSS :8765, or mutate production vault/auth data.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from backend.health_vault.api import create_health_vault_app
from backend.health_vault.vault_store import VaultStore

ROOT = Path(__file__).resolve().parents[1]
MOBILE_HTML = ROOT / "mobile.html"
MOBILE_JS = ROOT / "js/health_vault/mobile_consumer.js"
SNAPSHOT_JS = ROOT / "js/health_vault/health_snapshot.js"
NAV_JS = ROOT / "js/health_vault/consumer_nav.js"
CONTRACT_JS = ROOT / "js/health_vault/json_contract.js"
STYLE = ROOT / "style.css"
API_PY = ROOT / "backend/health_vault/api.py"
BOOTSTRAP = "0" * 6
ANSWERS = [
    {"question_id": "CQ01", "answer": "Westfield School"},
    {"question_id": "CQ02", "answer": "Toronto"},
    {"question_id": "CQ03", "answer": "Buster"},
]
MOBILE_SCRIPTS = (CONTRACT_JS, NAV_JS, SNAPSHOT_JS, MOBILE_JS)
STARTUP_PATHS = (
    "/api/auth/session",
    "/api/dashboard/summary",
    "/api/dashboard/preferences",
    "/api/health-vault/health-snapshot",
    "/api/auth/recovery/catalog",
    "/api/records",
    "/api/health-vault/timeline",
    "/api/health-vault/trends",
    "/api/health-vault/doctor-visit",
)


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _authed_client(tmp_path: Path) -> tuple[TestClient, dict[str, str]]:
    store = VaultStore(root=tmp_path / "vault", encryption_key=b"R" * 32)
    app = create_health_vault_app(
        store, production=True, bootstrap_password="Owner-Temp-Password"
    )
    client = TestClient(app)
    user_id = "10001"
    app.state.auth_service.create_user(
        user_id=user_id,
        name="Consumer",
        email_identifier=user_id,
        password=BOOTSTRAP,
        must_change_password=True,
    )
    login = client.post("/api/auth/login", json={"user_id": user_id, "password": BOOTSTRAP})
    token = login.json()["token"]
    changed = client.post(
        "/api/auth/password/change",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "current_password": BOOTSTRAP,
            "new_password": "Consumer-Permanent-2026",
            "confirm_password": "Consumer-Permanent-2026",
            "recovery_answers": ANSWERS,
        },
    )
    assert changed.status_code == 200, changed.text
    authed = client.post(
        "/api/auth/login",
        json={"user_id": user_id, "password": "Consumer-Permanent-2026"},
    )
    assert authed.status_code == 200
    bearer = authed.json()["token"]
    headers = {"Authorization": f"Bearer {bearer}", "Accept": "application/json"}
    return client, headers


def _node(script: str) -> str:
    prelude = f"""
const fs = require('fs');
const vm = require('vm');
function selectEl(id) {{
  const node = {{
    id,
    hidden: false,
    disabled: false,
    textContent: '',
    value: '',
    innerHTML: '',
    className: 'mobile-select',
    tagName: 'SELECT',
    classList: {{ toggle() {{}}, contains() {{ return false; }} }},
    dataset: {{}},
    style: {{ pointerEvents: 'auto', touchAction: 'manipulation', position: 'relative', zIndex: '6' }},
    children: [],
    getBoundingClientRect() {{
      return id === 'mobile_theme'
        ? {{ left: 20, top: 200, width: 200, height: 44 }}
        : {{ left: 20, top: 260, width: 200, height: 44 }};
    }},
    contains(other) {{ return other === this || (other && other.parent === this); }},
    querySelector(sel) {{
      if (sel === '[data-mobile-content]') return {{ replaceChildren() {{}}, appendChild() {{}} }};
      return null;
    }},
    querySelectorAll() {{ return []; }},
    addEventListener() {{}},
    setAttribute() {{}},
    removeAttribute() {{}},
    appendChild(child) {{ this.children.push(child); return child; }},
    replaceChildren() {{ this.children = []; }},
  }};
  return node;
}}
function el(id) {{
  if (id === 'mobile_theme' || id === 'mobile_priority_metric') return selectEl(id);
  const node = {{
    id: id || '',
    hidden: false, textContent: '', value: '', innerHTML: '',
    classList: {{ toggle() {{}}, contains() {{ return false; }} }},
    dataset: {{}},
    style: {{}},
    querySelector(sel) {{
      if (sel === '[data-mobile-content]') return {{ replaceChildren() {{}}, appendChild() {{}} }};
      return null;
    }},
    querySelectorAll() {{ return []; }},
    addEventListener() {{}},
    setAttribute() {{}},
    removeAttribute() {{}},
    appendChild() {{}},
    replaceChildren() {{}},
  }};
  return node;
}}
const selects = {{
  mobile_theme: selectEl('mobile_theme'),
  mobile_priority_metric: selectEl('mobile_priority_metric'),
}};
const session = {{
  _data: {{}},
  getItem(key) {{ return this._data[key] || null; }},
  setItem(key, value) {{ this._data[key] = String(value); }},
  removeItem(key) {{ delete this._data[key]; }},
}};
const ctx = {{
  console,
  fetch: globalThis.fetch,
  URL,
  sessionStorage: session,
  localStorage: session,
  document: {{
    body: {{ classList: {{ toggle() {{}}, contains() {{ return false; }} }}, dataset: {{}} }},
    documentElement: {{ setAttribute() {{}}, getAttribute() {{ return 'dark'; }} }},
    getElementById: (id) => selects[id] || el(id),
    elementFromPoint(x, y) {{
      if (y >= 200 && y < 244) return selects.mobile_theme;
      if (y >= 260 && y < 304) return selects.mobile_priority_metric;
      return {{ id: 'blocker', tagName: 'DIV', className: 'overlay' }};
    }},
    addEventListener() {{}},
    querySelector() {{ return null; }},
    querySelectorAll() {{ return []; }},
    dispatchEvent() {{}},
  }},
  location: {{ origin: 'https://health.capitalstratasystems.com', pathname: '/mobile', href: 'https://health.capitalstratasystems.com/mobile' }},
}};
ctx.globalThis = ctx;
ctx.window = ctx;
ctx.CustomEvent = function CustomEvent(name, init) {{ this.type = name; this.detail = (init && init.detail) || {{}}; }};
function headers(map) {{
  return {{ get(name) {{
    const key = String(name || '').toLowerCase();
    for (const [k, v] of Object.entries(map || {{}})) {{
      if (String(k).toLowerCase() === key) return v;
    }}
    return null;
  }} }};
}}
function fakeResponse({{ status=200, contentType='application/json', body='', url='', redirected=false }}) {{
  return {{
    status,
    ok: status >= 200 && status < 300,
    redirected: !!redirected,
    url,
    headers: headers({{ 'content-type': contentType }}),
    text: async () => body,
    json: async () => JSON.parse(body),
  }};
}}
ctx.fetch = async () => fakeResponse({{ status: 200, contentType: 'application/json', body: '{{}}' }});
vm.createContext(ctx);
vm.runInContext(fs.readFileSync({str(CONTRACT_JS)!r}, 'utf8'), ctx);
vm.runInContext(fs.readFileSync({str(ROOT / "js/health_vault/clinical_rules.js")!r}, 'utf8'), ctx);
vm.runInContext(fs.readFileSync({str(ROOT / "js/health_vault/trend_engine.js")!r}, 'utf8'), ctx);
vm.runInContext(fs.readFileSync({str(SNAPSHOT_JS)!r}, 'utf8'), ctx);
vm.runInContext(fs.readFileSync({str(NAV_JS)!r}, 'utf8'), ctx);
vm.runInContext(fs.readFileSync({str(MOBILE_JS)!r}, 'utf8'), ctx);
async function run() {{
{script}
}}
run().then((value) => {{
  if (value !== undefined) console.log(typeof value === 'string' ? value : JSON.stringify(value));
}}).catch((err) => {{
  console.error(String(err && err.message || err));
  process.exit(1);
}});
"""
    proc = subprocess.run(["node", "-e", prelude], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout.strip()


def test_all_mobile_json_calls_are_guarded():
    html = _read("mobile.html")
    assert html.index("/js/health_vault/json_contract.js") < html.index("/js/health_vault/health_snapshot.js")
    assert html.index("/js/health_vault/json_contract.js") < html.index("/js/health_vault/mobile_consumer.js")
    contract = _read("js/health_vault/json_contract.js")
    for token in ("API_RESPONSE_NOT_JSON", "JSON_PARSE_FAILED", "response.redirected", "content-type", "final_url="):
        assert token in contract
    for path in MOBILE_SCRIPTS:
        src = path.read_text(encoding="utf-8")
        assert "response.json()" not in src
        assert "res.json()" not in src
        assert ".then(res => res.json())" not in src
        assert "await response.json()" not in src
    mobile = _read("js/health_vault/mobile_consumer.js")
    for path in (
        "/api/auth/recovery/catalog",
        "/api/auth/recovery/start",
        "/api/auth/recovery/verify",
        "/api/auth/recovery/complete",
        "/api/auth/recovery/enroll",
        "/api/auth/password/change",
    ):
        assert path in mobile
    assert mobile.count("parseJsonResponse(response, \"/api/auth/recovery/catalog\")") == 1
    assert mobile.count("parseJsonResponse(response, \"/api/auth/recovery/start\")") == 1
    assert mobile.count("parseJsonResponse(response, \"/api/auth/recovery/verify\")") == 1
    assert mobile.count("parseJsonResponse(response, \"/api/auth/recovery/complete\")") == 1
    assert 'request("/api/auth/recovery/enroll"' in mobile


def test_api_html_fallback_prohibited_and_unknown_is_json_404(tmp_path):
    api = API_PY.read_text(encoding="utf-8")
    assert "api_never_html_shell" in api
    assert "api_html_forbidden" in api
    assert "api_unknown_route" in api
    client, headers = _authed_client(tmp_path)
    missing = client.get("/api/r7d-no-such-route", headers=headers)
    assert missing.status_code == 404
    ctype = (missing.headers.get("content-type") or "").split(";")[0].strip().lower()
    assert ctype == "application/json"
    body = missing.json()
    assert body["code"] == "not_found"
    assert body["path"] == "/api/r7d-no-such-route"
    dumped = json.dumps(body)
    assert "<!DOCTYPE" not in dumped
    assert "<html" not in dumped
    shell = client.get("/mobile")
    assert "text/html" in (shell.headers.get("content-type") or "")
    assert "<!doctype html>" in shell.text.lower()
    assert client.get("/api/mobile.html", headers=headers).status_code == 404
    html_404 = client.get("/api/mobile.html", headers=headers)
    assert "application/json" in (html_404.headers.get("content-type") or "")
    assert "<!DOCTYPE" not in html_404.text


def test_api_auth_error_is_json(tmp_path):
    client, _headers = _authed_client(tmp_path)
    denied = client.get("/api/dashboard/summary")
    assert denied.status_code in {401, 403}
    ctype = (denied.headers.get("content-type") or "").split(";")[0].strip().lower()
    assert ctype == "application/json"
    body = denied.json()
    assert isinstance(body, dict)
    assert "<!DOCTYPE" not in json.dumps(body)
    forbidden = client.get("/api/dashboard/summary", headers={"Authorization": "Bearer not-a-token"})
    assert forbidden.status_code in {401, 403}
    assert "application/json" in (forbidden.headers.get("content-type") or "")
    assert "<!DOCTYPE" not in forbidden.text


def test_recovery_catalog_and_flow_are_json(tmp_path):
    client, headers = _authed_client(tmp_path)
    catalog = client.get("/api/auth/recovery/catalog")
    assert catalog.status_code == 200
    assert "application/json" in (catalog.headers.get("content-type") or "")
    questions = catalog.json()["questions"]
    assert questions
    start = client.post("/api/auth/recovery/start", json={"user_id": "10001"})
    assert start.status_code == 200
    assert "application/json" in (start.headers.get("content-type") or "")
    verify = client.post(
        "/api/auth/recovery/verify",
        json={"recovery_id": start.json()["recovery_id"], "answers": ANSWERS},
    )
    assert "application/json" in (verify.headers.get("content-type") or "")
    enroll = client.post(
        "/api/auth/recovery/enroll",
        headers=headers,
        json={
            "current_password": "Consumer-Permanent-2026",
            "recovery_answers": ANSWERS,
        },
    )
    assert enroll.status_code == 200
    assert "application/json" in (enroll.headers.get("content-type") or "")


def test_authenticated_mobile_startup_sequence_never_returns_html(tmp_path):
    client, headers = _authed_client(tmp_path)
    html_paths = []
    for path in STARTUP_PATHS:
        response = client.get(path, headers=headers)
        ctype = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
        text = response.text[:80]
        if ctype != "application/json" or text.lstrip().startswith("<"):
            html_paths.append({"path": path, "status": response.status_code, "content_type": ctype})
        else:
            assert isinstance(response.json(), dict)
            assert "<!DOCTYPE" not in response.text
    assert html_paths == []


def test_settings_selects_are_enabled_and_hit_test_passes():
    html = _read("mobile.html")
    css = _read("style.css")
    js = _read("js/health_vault/mobile_consumer.js")
    assert 'id="mobile_theme"' in html
    assert 'id="mobile_priority_metric"' in html
    assert 'disabled' not in html.split('id="mobile_theme"', 1)[1].split("</select>", 1)[0]
    assert 'disabled' not in html.split('id="mobile_priority_metric"', 1)[1].split("</select>", 1)[0]
    assert "inert" not in html.split('id="mobile_settings"', 1)[1].split('id="mobile_save_preferences"', 1)[0]
    assert "pointer-events: none !important" in css
    assert "body.mobile-consumer #mobile_theme" in css
    assert "body.mobile-consumer #mobile_priority_metric" in css
    assert "pointer-events: auto" in css
    assert "inspectSettingsSelects" in js
    assert "elementFromPoint" in js
    out = _node(
        """
const results = ctx.HCMobileJsonContract.inspectSettingsSelects();
return {
  theme: results.mobile_theme,
  priority: results.mobile_priority_metric,
};
"""
    )
    payload = json.loads(out)
    assert payload["theme"]["enabled"] is True
    assert payload["priority"]["enabled"] is True
    assert payload["theme"]["pointerEvents"] is True
    assert payload["priority"]["pointerEvents"] is True
    assert payload["theme"]["hitOk"] is True
    assert payload["priority"]["hitOk"] is True
    assert payload["theme"]["blocking"] == ""
    assert payload["priority"]["blocking"] == ""


def test_surface_loads_are_isolated_and_do_not_keep_stale_errors():
    js = _read("js/health_vault/mobile_consumer.js")
    for msg in (
        "Dashboard data unavailable.",
        "Records data unavailable.",
        "Trends data unavailable.",
        "Observations data unavailable.",
        "Timeline data unavailable.",
        "Reports data unavailable.",
    ):
        assert msg in js
    show = js.split("async function showView")[1].split("async function upload")[0]
    records_block = show.split('if (name === "records")')[1].split('if (name === "trends")')[0]
    assert "loadDashboard" not in records_block
    assert "await loadRecords()" in records_block
    assert "fail(\"records\"" in show
    assert "fail(\"timeline\"" in show
    assert "ok()" in show
    out = _node(
        """
const html = await (async () => {
  try {
    return await ctx.HCMobileJsonContract.parseJsonResponse({
      status: 200,
      redirected: true,
      url: 'https://health.capitalstratasystems.com/mobile',
      headers: { get(name) { return name === 'content-type' ? 'text/html' : null; } },
      text: async () => '<!DOCTYPE html><html></html>',
      json: async () => { throw new Error("Unexpected token '<'"); },
    }, '/api/dashboard/summary');
  } catch (err) {
    return String(err.message);
  }
})();
return { html, hasUnexpected: html.indexOf('Unexpected token') >= 0 };
"""
    )
    payload = json.loads(out)
    assert payload["html"].startswith("API_RESPONSE_NOT_JSON ")
    assert payload["hasUnexpected"] is False


def test_import_screen_and_reports_empty_states_exist():
    html = _read("mobile.html")
    js = _read("js/health_vault/mobile_consumer.js")
    assert 'id="mobile_record_file"' in html
    assert 'id="mobile_import"' in html
    assert 'id="mobile_reports"' in html
    assert "No report information is available yet." in js
    assert 'if (name === "import")' in js


def test_json_contract_helper_rejects_html_without_calling_response_json():
    out = _node(
        """
let jsonCalled = false;
const response = {
  status: 200,
  redirected: false,
  url: 'https://health.capitalstratasystems.com/api/auth/recovery/catalog',
  headers: { get(name) { return name === 'content-type' ? 'text/html' : null; } },
  text: async () => '<!DOCTYPE html><html><body>nope</body></html>',
  json: async () => { jsonCalled = true; return JSON.parse('<'); },
};
try {
  await ctx.HCMobileJsonContract.parseJsonResponse(response, '/api/auth/recovery/catalog');
  return { threw: false, jsonCalled };
} catch (err) {
  return { threw: true, jsonCalled, message: String(err.message) };
}
"""
    )
    payload = json.loads(out)
    assert payload["threw"] is True
    assert payload["jsonCalled"] is False
    assert payload["message"].startswith("API_RESPONSE_NOT_JSON ")
    assert "path=/api/auth/recovery/catalog" in payload["message"]
    assert "<!DOCTYPE" not in payload["message"]
