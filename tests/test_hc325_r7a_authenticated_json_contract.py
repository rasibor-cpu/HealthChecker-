"""HC325-R7A — authenticated mobile JSON contract diagnostic.

Fictional fixtures only. Does not talk to live :8766, restart the host,
touch CSS :8765, or mutate production vault/auth data.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from backend.health_vault.api import create_health_vault_app
from backend.health_vault.vault_store import VaultStore

ROOT = Path(__file__).resolve().parents[1]
MOBILE_JS = ROOT / "js/health_vault/mobile_consumer.js"
SNAPSHOT_JS = ROOT / "js/health_vault/health_snapshot.js"
HTML_SHELL = "<!DOCTYPE html><html><body>HealthChecker login</body></html>"
BEARER = "Bearer secret-test-token-do-not-echo"
BOOTSTRAP = "0" * 6
ANSWERS = [
    {"question_id": "CQ01", "answer": "Westfield School"},
    {"question_id": "CQ02", "answer": "Toronto"},
    {"question_id": "CQ03", "answer": "Buster"},
]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _node(script: str, *, load_mobile: bool = False) -> str:
    mobile_load = (
        f"vm.runInContext(fs.readFileSync({str(MOBILE_JS)!r}, 'utf8'), ctx);\n"
        if load_mobile
        else ""
    )
    prelude = f"""
const fs = require('fs');
const vm = require('vm');
function el() {{
  const node = {{
    hidden: false, textContent: '', value: '', innerHTML: '',
    classList: {{ toggle() {{}}, contains() {{ return false; }} }},
    dataset: {{}},
    style: {{}},
    querySelector(sel) {{
      if (sel === '[data-mobile-content]') return {{ replaceChildren() {{}}, appendChild() {{}} }};
      if (sel === '[data-hc-json-contract-error]') return this._err || null;
      return null;
    }},
    querySelectorAll() {{ return []; }},
    addEventListener() {{}},
    setAttribute() {{}},
    appendChild(child) {{ this._err = child; return child; }},
    replaceChildren() {{}},
  }};
  return node;
}}
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
    getElementById: () => el(),
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
vm.runInContext(fs.readFileSync({str(ROOT / "js/health_vault/clinical_rules.js")!r}, 'utf8'), ctx);
vm.runInContext(fs.readFileSync({str(ROOT / "js/health_vault/trend_engine.js")!r}, 'utf8'), ctx);
vm.runInContext(fs.readFileSync({str(ROOT / "js/health_vault/health_snapshot.js")!r}, 'utf8'), ctx);
{mobile_load}
const HS = ctx.HCHealthSnapshot;
const MC = ctx.HCMobileJsonContract;
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


def test_source_guards_inspect_status_type_url_and_redirect():
    mobile = _read("js/health_vault/mobile_consumer.js")
    snap = _read("js/health_vault/health_snapshot.js")
    for src in (mobile, snap):
        assert "response.redirected" in src
        assert "content-type" in src
        assert "API_RESPONSE_NOT_JSON" in src
        assert "JSON_PARSE_FAILED" in src
        assert "final_url=" in src
        assert "parseJsonResponse" in src
        assert src.count("return res.json()") == 0
    request_fn = mobile.split("async function request")[1].split("function showAuthenticated")[0]
    assert "await response.json()" not in request_fn
    assert "Session expired" in request_fn
    assert request_fn.index("401") < request_fn.index("parseJsonResponse")
    snap_refresh = snap.split("function refresh()")[1].split("function applyTheme")[0]
    assert "res.json()" not in snap_refresh
    snap_detail = snap.split("function openMetricDetail")[1].split("function bindCardClicks")[0]
    assert "res.json()" not in snap_detail
    assert "Authorization" not in mobile.split("function jsonContractError")[1].split("async function parseJsonResponse")[0]
    for src in (mobile, snap):
        assert "DOCTYPE" not in src.split("function jsonContractError")[1].split("function parseJsonResponse")[0]


def test_json_200_parses_normally():
    out = _node(
        """
const body = await HS.parseJsonResponse(fakeResponse({
  status: 200,
  contentType: 'application/json; charset=utf-8',
  body: JSON.stringify({ cards: [{ metric_id: 'glucose', title: 'Glucose' }] }),
  url: 'https://health.capitalstratasystems.com/api/health-vault/health-snapshot',
}), '/api/health-vault/health-snapshot');
return { metric: body.cards[0].metric_id, ok: HS.isJsonContentType('application/json; charset=utf-8') };
"""
    )
    payload = json.loads(out)
    assert payload["metric"] == "glucose"
    assert payload["ok"] is True


def test_json_401_and_403_preserve_existing_auth_handling():
    mobile = _read("js/health_vault/mobile_consumer.js")
    request_fn = mobile.split("async function request")[1].split("function showAuthenticated")[0]
    assert "Session expired" in request_fn
    assert "Password change required" in request_fn
    assert "await logout(false)" in request_fn
    out = _node(
        """
let parsed = null;
try {
  parsed = await HS.parseJsonResponse(fakeResponse({
    status: 401,
    contentType: 'application/json',
    body: JSON.stringify({ code: 'invalid_token' }),
    url: 'https://health.capitalstratasystems.com/api/dashboard/summary',
  }), '/api/dashboard/summary');
} catch (err) {
  return { failed: true, message: String(err.message) };
}
return { failed: false, code: parsed.code };
"""
    )
    payload = json.loads(out)
    assert payload["failed"] is False
    assert payload["code"] == "invalid_token"


def test_html_200_is_rejected_before_json_parse():
    out = _node(
        f"""
let jsonCalled = false;
const response = fakeResponse({{
  status: 200,
  contentType: 'text/html; charset=utf-8',
  body: {HTML_SHELL!r},
  url: 'https://health.capitalstratasystems.com/mobile',
}});
response.json = async () => {{ jsonCalled = true; return JSON.parse(response.body || '{{}}'); }};
try {{
  await HS.parseJsonResponse(response, '/api/dashboard/summary');
  return {{ threw: false, jsonCalled }};
}} catch (err) {{
  return {{ threw: true, jsonCalled, message: String(err.message) }};
}}
"""
    )
    payload = json.loads(out)
    assert payload["threw"] is True
    assert payload["jsonCalled"] is False
    assert payload["message"].startswith("API_RESPONSE_NOT_JSON ")
    assert "path=/api/dashboard/summary" in payload["message"]
    assert "status=200" in payload["message"]
    assert "text/html" in payload["message"]
    assert "<!DOCTYPE" not in payload["message"]
    assert "<html" not in payload["message"]


def test_html_redirect_destination_is_reported_safely():
    out = _node(
        """
try {
  await HS.parseJsonResponse(fakeResponse({
    status: 200,
    contentType: 'text/html',
    body: '<!DOCTYPE html><html><body>gate</body></html>',
    url: 'https://health.capitalstratasystems.com/mobile?token=super-secret&view=dashboard',
    redirected: true,
  }), '/api/dashboard/summary?patient_id=00000&token=should-not-appear');
  return { threw: false };
} catch (err) {
  return { message: String(err.message) };
}
"""
    )
    payload = json.loads(out)
    msg = payload["message"]
    assert msg.startswith("API_RESPONSE_NOT_JSON ")
    assert "path=/api/dashboard/summary" in msg
    assert "final_url=https://health.capitalstratasystems.com/mobile" in msg
    assert "token=" not in msg
    assert "patient_id" not in msg
    assert "super-secret" not in msg
    assert "should-not-appear" not in msg
    assert "?view=" not in msg


def test_malformed_json_content_type_is_json_parse_failed():
    out = _node(
        """
try {
  await HS.parseJsonResponse(fakeResponse({
    status: 200,
    contentType: 'application/json',
    body: '{not-json',
    url: 'https://health.capitalstratasystems.com/api/health-vault/health-snapshot',
  }), '/api/health-vault/health-snapshot');
  return { threw: false };
} catch (err) {
  return { message: String(err.message) };
}
"""
    )
    payload = json.loads(out)
    assert payload["message"].startswith("JSON_PARSE_FAILED ")
    assert "path=/api/health-vault/health-snapshot" in payload["message"]
    assert "{not-json" not in payload["message"]


def test_user_visible_diagnostics_omit_html_and_authorization():
    out = _node(
        """
const root = {
  innerHTML: '',
  _node: { textContent: '' },
  querySelector() { return this._node; },
};
const err = new Error('API_RESPONSE_NOT_JSON path=/api/dashboard/summary status=200 content_type=text/html final_url=https://health.capitalstratasystems.com/mobile');
HS.showSnapshotContractError(root, err);
return {
  html: root.innerHTML,
  text: root._node.textContent,
};
"""
    )
    payload = json.loads(out)
    assert "data-hc-json-contract-error" in payload["html"]
    assert payload["text"].startswith("API_RESPONSE_NOT_JSON ")
    assert "<!DOCTYPE" not in payload["text"]
    assert BEARER not in payload["text"]
    assert "Authorization" not in payload["text"]
    assert "secret-test-token" not in payload["text"]
    mobile = _read("js/health_vault/mobile_consumer.js")
    snap = _read("js/health_vault/health_snapshot.js")
    for src in (mobile, snap):
        assert "Bearer ${session.token}" not in src.split("function jsonContractError")[1].split("parseJsonResponse")[0]
        assert "headers.get(\"authorization\")" not in src.lower()


def test_health_snapshot_html_does_not_fallback_to_local():
    out = _node(
        """
const root = {
  innerHTML: '',
  querySelector(sel) {
    if (sel === '.hc-health-snapshot') return {};
    if (sel === '[data-hc-json-contract-error]') return this._err;
    return null;
  },
  _err: { textContent: '' },
};
ctx.document.getElementById = (id) => id === 'hc_health_snapshot' ? root : null;
ctx.sessionStorage.setItem('hc_mobile_auth_session', JSON.stringify({ token: 'secret-test-token-do-not-echo' }));
ctx.fetch = async () => fakeResponse({
  status: 200,
  contentType: 'text/html',
  body: '<!DOCTYPE html><html><body>Dashboard shell</body></html>',
  url: 'https://health.capitalstratasystems.com/',
  redirected: true,
});
await HS.refresh();
return {
  diagnostic: root._err.textContent,
  html: root.innerHTML,
};
"""
    )
    payload = json.loads(out)
    assert payload["diagnostic"].startswith("API_RESPONSE_NOT_JSON ")
    assert "path=/api/health-vault/health-snapshot" in payload["diagnostic"]
    assert "final_url=https://health.capitalstratasystems.com/" in payload["diagnostic"]
    assert "Dashboard shell" not in payload["diagnostic"]
    assert "secret-test-token" not in payload["diagnostic"]
    assert "<!DOCTYPE" not in payload["diagnostic"]


def test_mobile_request_helper_matches_snapshot_contract(tmp_path: Path):
    out = _node(
        """
const html = await (async () => {
  try {
    return await MC.parseJsonResponse(fakeResponse({
      status: 200,
      contentType: 'text/html',
      body: '<!DOCTYPE html><html><body>nope</body></html>',
      url: 'https://health.capitalstratasystems.com/index.html',
      redirected: true,
    }), '/api/records?surface=clinical_document');
  } catch (err) {
    return String(err.message);
  }
})();
const ok = await MC.parseJsonResponse(fakeResponse({
  status: 200,
  contentType: 'application/json',
  body: JSON.stringify({ records: [] }),
  url: 'https://health.capitalstratasystems.com/api/records',
}), '/api/records');
return { html, records: ok.records, path: MC.safeApiPath('/api/records?surface=clinical_document') };
""",
        load_mobile=True,
    )
    payload = json.loads(out)
    assert payload["html"].startswith("API_RESPONSE_NOT_JSON ")
    assert "path=/api/records" in payload["html"]
    assert "index.html" in payload["html"]
    assert "surface=clinical_document" not in payload["html"]
    assert payload["records"] == []
    assert payload["path"] == "/api/records"


def test_isolated_authenticated_dashboard_apis_return_json(tmp_path: Path):
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
    assert login.status_code == 200
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
    paths = (
        "/api/auth/session",
        "/api/dashboard/summary",
        "/api/dashboard/preferences",
        "/api/health-vault/health-snapshot",
        "/api/records",
        "/api/health-vault/timeline",
        "/api/health-vault/doctor-visit",
    )
    for path in paths:
        response = client.get(path, headers=headers)
        assert response.status_code == 200, path
        ctype = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
        assert ctype == "application/json", path
        body = response.json()
        assert isinstance(body, dict)
        dumped = json.dumps(body)
        assert "<!DOCTYPE" not in dumped
        assert BOOTSTRAP not in dumped
        assert "Consumer-Permanent-2026" not in dumped
        assert bearer not in dumped
