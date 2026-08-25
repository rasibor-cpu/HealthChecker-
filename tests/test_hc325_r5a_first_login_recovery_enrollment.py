"""HC325-R5A — first-login recovery enrollment UX.

Fictional fixtures only. Does not talk to live :8766 or mutate production
account 000001 / production vault data.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from backend.health_vault.api import create_health_vault_app
from backend.health_vault.auth import verify_password
from backend.health_vault.vault_store import VaultStore

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = "0" * 6
ANSWERS = [
    {"question_id": "CQ01", "answer": "Westfield School"},
    {"question_id": "CQ02", "answer": "Toronto"},
    {"question_id": "CQ03", "answer": "Buster"},
]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _app(tmp_path):
    store = VaultStore(root=tmp_path / "vault", encryption_key=b"A" * 32)
    app = create_health_vault_app(store, production=True, bootstrap_password="Owner-Temp-Password")
    return store, app.state.auth_service, TestClient(app)


def _node(script: str) -> str:
    prelude = f"""
const fs = require('fs');
const vm = require('vm');
const session = {{
  _data: {{ hc_mobile_auth_session: JSON.stringify({{ token: 'session-token', userId: 'fixture-user' }}) }},
  getItem(key) {{ return this._data[key] || null; }},
  setItem(key, value) {{ this._data[key] = String(value); }},
  removeItem(key) {{ delete this._data[key]; }},
}};
const ctx = {{
  console,
  sessionStorage: session,
  URLSearchParams,
  decodeURIComponent,
  location: {{ search: '?view=records', pathname: '/mobile' }},
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
ctx.HCConsumerNavAdapter = {{ activate(route) {{ activated.push(route); }} }};
function dump(obj) {{ console.log(JSON.stringify(obj)); }}
{script}
"""
    proc = subprocess.run(["node", "-e", prelude], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout.strip()


def test_ui_has_explicit_enrollment_state_and_hides_raw_codes():
    mobile = _read("mobile.html")
    desktop = _read("index.html")
    mobile_js = _read("js/health_vault/mobile_consumer.js")
    dash_js = _read("js/health_vault/dashboard.js")
    assert 'id="mobile_password_change"' in mobile
    assert 'id="mobile_recovery_enroll"' in mobile
    assert "Choose recovery questions" in mobile
    assert 'id="password_enroll_form"' in desktop
    assert "Choose recovery questions" in desktop
    for source in (mobile_js, dash_js):
        assert 'recovery_enrollment_required' in source
        assert "setAuthState" in source
        assert "password_change_required" in source
        assert "enterEnrollmentGate" in source or "showEnrollment" in source
        assert 'textContent = "recovery_enrollment_required"' not in source
        assert "userFacingAuthError" in source
        assert "Choose three different recovery questions." in source
        assert "Enter an answer for each recovery question." in source
    assert BOOTSTRAP not in mobile
    assert BOOTSTRAP not in desktop
    assert BOOTSTRAP not in mobile_js
    assert BOOTSTRAP not in dash_js
    assert "000001" not in mobile_js
    assert "000001" not in dash_js


def test_password_change_without_answers_stays_gated_then_enrollment_succeeds(tmp_path):
    _, auth, client = _app(tmp_path)
    auth.create_user(
        user_id="fixture-10001",
        name="Fixture Consumer",
        email_identifier="fixture-10001",
        password=BOOTSTRAP,
        must_change_password=True,
    )
    login = client.post("/api/auth/login", json={"user_id": "fixture-10001", "password": BOOTSTRAP})
    assert login.status_code == 200
    assert login.json()["must_change_password"] is True
    token = login.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/dashboard/summary", headers=headers).status_code == 403
    missing = client.post(
        "/api/auth/password/change",
        headers=headers,
        json={
            "current_password": BOOTSTRAP,
            "new_password": "Fixture-Permanent-2026",
            "confirm_password": "Fixture-Permanent-2026",
        },
    )
    assert missing.status_code == 400
    assert missing.json()["code"] == "recovery_enrollment_required"
    empty = client.post(
        "/api/auth/password/change",
        headers=headers,
        json={
            "current_password": BOOTSTRAP,
            "new_password": "Fixture-Permanent-2026",
            "confirm_password": "Fixture-Permanent-2026",
            "recovery_answers": [
                {"question_id": "CQ01", "answer": ""},
                {"question_id": "CQ02", "answer": "Toronto"},
                {"question_id": "CQ03", "answer": "Buster"},
            ],
        },
    )
    assert empty.status_code == 400
    duplicates = client.post(
        "/api/auth/password/change",
        headers=headers,
        json={
            "current_password": BOOTSTRAP,
            "new_password": "Fixture-Permanent-2026",
            "confirm_password": "Fixture-Permanent-2026",
            "recovery_answers": [
                {"question_id": "CQ01", "answer": "Westfield School"},
                {"question_id": "CQ01", "answer": "Toronto"},
                {"question_id": "CQ03", "answer": "Buster"},
            ],
        },
    )
    assert duplicates.status_code == 400
    changed = client.post(
        "/api/auth/password/change",
        headers=headers,
        json={
            "current_password": BOOTSTRAP,
            "new_password": "Fixture-Permanent-2026",
            "confirm_password": "Fixture-Permanent-2026",
            "recovery_answers": ANSWERS,
        },
    )
    assert changed.status_code == 200
    assert changed.json()["must_change_password"] is False
    assert changed.json()["scope"] == "full"
    full_headers = {"Authorization": f"Bearer {changed.json()['token']}"}
    assert client.get("/api/dashboard/summary", headers=full_headers).status_code == 200
    row = auth._read()["accounts"]["fixture-10001"]
    assert len(row["recovery_questions"]) == 3
    for item in row["recovery_questions"]:
        assert "answer" not in item
        assert item["answer_hash"].startswith("scrypt$")
    assert verify_password("Fixture-Permanent-2026", row["password_hash"])
    serialized = json.dumps(row).lower()
    assert "westfield" not in serialized
    assert BOOTSTRAP not in serialized


def test_security_gate_blocks_deep_link_and_android_back_during_lifecycle():
    out = _node(
        """
Nav.setSecurityGate(true);
dump({
  deep: Nav.peekDeepLink(),
  noted: Nav.note('records'),
  back: Nav.back(),
  system: Nav.handleSystemBack(),
  leave: Nav.canLeaveApp(),
  activated,
  session: session.getItem('hc_mobile_auth_session'),
});
"""
    )
    payload = json.loads(out)
    assert payload["deep"] is None
    assert payload["back"]["handled"] is True
    assert payload["system"] is True
    assert payload["leave"] is False
    assert payload["activated"] == []
    assert "session-token" in payload["session"]
    mobile_js = _read("js/health_vault/mobile_consumer.js")
    dash_js = _read("js/health_vault/dashboard.js")
    assert 'setAuthState("recovery_enrollment_required")' in mobile_js
    assert 'setAuthState("password_change_required")' in mobile_js
    assert 'setAuthState("recovery_enrollment_required")' in dash_js
    assert 'setAuthState("password_change_required")' in dash_js


def test_patient_isolation_fixture_does_not_see_owner_records(tmp_path):
    store, auth, client = _app(tmp_path)
    data = store._read_index()
    data["documents"].append({
        "id": "owner-private",
        "patient_id": "00000",
        "status": "parsed",
        "original_filename": "owner-private.pdf",
    })
    store._write_index(data)
    auth.create_user(
        user_id="fixture-10001",
        name="Fixture Consumer",
        email_identifier="fixture-10001",
        password=BOOTSTRAP,
        must_change_password=True,
    )
    login = client.post("/api/auth/login", json={"user_id": "fixture-10001", "password": BOOTSTRAP})
    token = login.json()["token"]
    changed = client.post(
        "/api/auth/password/change",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "current_password": BOOTSTRAP,
            "new_password": "Fixture-Permanent-2026",
            "confirm_password": "Fixture-Permanent-2026",
            "recovery_answers": ANSWERS,
        },
    )
    headers = {"Authorization": f"Bearer {changed.json()['token']}"}
    records = client.get("/api/records", headers=headers)
    assert records.status_code == 200
    assert records.json().get("records") == []
    assert client.get("/api/records/owner-private", headers=headers).status_code == 404
    assert "000001" not in json.dumps(auth._read()["accounts"])
