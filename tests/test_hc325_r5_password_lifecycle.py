"""HC325-R5 — consumer password lifecycle and recovery tests.

Fictional fixtures only. Does not talk to the live :8766 process or mutate
production vault data.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from backend.health_vault.api import create_health_vault_app
from backend.health_vault.auth import PASSWORD_DAYS, verify_password
from backend.health_vault.consumer_recovery import CONSUMER_RECOVERY_QUESTIONS, catalog_public
from backend.health_vault.vault_store import VaultStore

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = "0" * 6
ANSWERS = [
    {"question_id": "CQ01", "answer": "Westfield School"},
    {"question_id": "CQ02", "answer": "Toronto"},
    {"question_id": "CQ03", "answer": "Buster"},
]
WRONG_ANSWERS = [
    {"question_id": "CQ01", "answer": "Wrong School"},
    {"question_id": "CQ02", "answer": "Wrong City"},
    {"question_id": "CQ03", "answer": "Wrong Pet"},
]


def _app(tmp_path, production=False):
    store = VaultStore(root=tmp_path / "vault", encryption_key=b"R" * 32)
    app = create_health_vault_app(store, production=production, bootstrap_password="Owner-Temp-Password")
    return store, app.state.auth_service, TestClient(app)


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _setup_consumer(auth, user_id="10001"):
    auth.create_user(
        user_id=user_id,
        name="Consumer",
        email_identifier=user_id,
        password=BOOTSTRAP,
        must_change_password=True,
    )
    return user_id


def _complete_first_change(client, user_id, current=BOOTSTRAP, new="Consumer-Permanent-2026"):
    login = client.post("/api/auth/login", json={"user_id": user_id, "password": current})
    assert login.status_code == 200
    token = login.json()["token"]
    changed = client.post(
        "/api/auth/password/change",
        headers=_headers(token),
        json={
            "current_password": current,
            "new_password": new,
            "confirm_password": new,
            "recovery_answers": ANSWERS,
        },
    )
    assert changed.status_code == 200, changed.text
    return changed.json()


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
ctx.HCConsumerNavAdapter = {{
  activate(route) {{ activated.push(route); }},
}};
function dump(obj) {{ console.log(JSON.stringify(obj)); }}
{script}
"""
    proc = subprocess.run(["node", "-e", prelude], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout.strip()


def test_catalog_has_eight_stable_question_ids():
    catalog = catalog_public()
    assert len(catalog) == 8
    assert [row["question_id"] for row in catalog] == [qid for qid, _ in CONSUMER_RECOVERY_QUESTIONS]
    assert {row["question_id"] for row in catalog} == {f"CQ0{i}" for i in range(1, 9)}


def test_bootstrap_login_forces_change_and_blocks_dashboard(tmp_path):
    store, auth, client = _app(tmp_path, production=True)
    user_id = _setup_consumer(auth)
    login = client.post("/api/auth/login", json={"user_id": user_id, "password": BOOTSTRAP})
    assert login.status_code == 200
    body = login.json()
    assert body["must_change_password"] is True
    assert body["scope"] == "password_change"
    assert "password_hash" not in body
    assert BOOTSTRAP not in json.dumps(body)
    headers = _headers(body["token"])
    denied = client.get("/api/dashboard/summary", headers=headers)
    assert denied.status_code == 403
    assert denied.json()["code"] == "password_change_required"
    for path in (
        "/api/records",
        "/api/health-vault/timeline",
        "/api/health-vault/health-snapshot",
    ):
        assert client.get(path, headers=headers).status_code == 403


def test_bootstrap_cannot_become_permanent_and_hash_only_storage(tmp_path):
    _, auth, client = _app(tmp_path, production=True)
    user_id = _setup_consumer(auth)
    token = client.post("/api/auth/login", json={"user_id": user_id, "password": BOOTSTRAP}).json()["token"]
    rejected = client.post(
        "/api/auth/password/change",
        headers=_headers(token),
        json={
            "current_password": BOOTSTRAP,
            "new_password": BOOTSTRAP,
            "confirm_password": BOOTSTRAP,
            "recovery_answers": ANSWERS,
        },
    )
    assert rejected.status_code == 400
    assert rejected.json()["code"] == "password_policy_violation"
    whitespace = client.post(
        "/api/auth/password/change",
        headers=_headers(token),
        json={
            "current_password": BOOTSTRAP,
            "new_password": "        ",
            "confirm_password": "        ",
            "recovery_answers": ANSWERS,
        },
    )
    assert whitespace.status_code == 400
    mismatch = client.post(
        "/api/auth/password/change",
        headers=_headers(token),
        json={
            "current_password": BOOTSTRAP,
            "new_password": "Consumer-Permanent-2026",
            "confirm_password": "Consumer-Permanent-2027",
            "recovery_answers": ANSWERS,
        },
    )
    assert mismatch.status_code == 400
    row = auth._read()["accounts"][user_id]
    assert row["password_hash"].startswith("scrypt$")
    assert verify_password(BOOTSTRAP, row["password_hash"])
    persisted = auth.path.read_bytes()
    assert BOOTSTRAP.encode() not in persisted
    assert b"password_hash" not in persisted


def test_first_change_enrolls_questions_sets_90_day_expiry_and_allows_login(tmp_path):
    _, auth, client = _app(tmp_path, production=True)
    user_id = _setup_consumer(auth)
    body = _complete_first_change(client, user_id)
    assert body["must_change_password"] is False
    assert body["scope"] == "full"
    assert body["recovery_enrolled"] is True
    changed_at = datetime.fromisoformat(body["password_changed_at"].replace("Z", "+00:00"))
    expires = datetime.fromisoformat(body["password_expires_at"].replace("Z", "+00:00"))
    assert timedelta(days=89, hours=23) < expires - changed_at <= timedelta(days=PASSWORD_DAYS)
    row = auth._read()["accounts"][user_id]
    assert verify_password("Consumer-Permanent-2026", row["password_hash"])
    assert not verify_password(BOOTSTRAP, row["password_hash"])
    questions = row["recovery_questions"]
    assert len(questions) == 3
    serialized = json.dumps(row).lower()
    for secret in ("westfield", "toronto", "buster", BOOTSTRAP, "consumer-permanent"):
        assert secret not in serialized
    for item in questions:
        assert "answer" not in item
        assert item["answer_hash"].startswith("scrypt$")
    full = client.post("/api/auth/login", json={"user_id": user_id, "password": "Consumer-Permanent-2026"})
    assert full.status_code == 200
    assert full.json()["must_change_password"] is False
    assert client.get("/api/dashboard/summary", headers=_headers(full.json()["token"])).status_code == 200
    assert client.post("/api/auth/login", json={"user_id": user_id, "password": BOOTSTRAP}).status_code == 401


def test_expired_password_forces_change(tmp_path):
    _, auth, client = _app(tmp_path, production=True)
    user_id = _setup_consumer(auth)
    _complete_first_change(client, user_id)
    data = auth._read()
    data["accounts"][user_id]["password_expiry_date"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat().replace("+00:00", "Z")
    auth._write(data)
    login = client.post("/api/auth/login", json={"user_id": user_id, "password": "Consumer-Permanent-2026"})
    assert login.json()["scope"] == "password_change"
    assert login.json()["must_change_password"] is True
    headers = _headers(login.json()["token"])
    assert client.get("/api/dashboard/summary", headers=headers).status_code == 403


def test_voluntary_change_requires_current_password_and_revokes_other_sessions(tmp_path):
    _, auth, client = _app(tmp_path, production=True)
    user_id = _setup_consumer(auth)
    first = _complete_first_change(client, user_id)
    other = client.post("/api/auth/login", json={"user_id": user_id, "password": "Consumer-Permanent-2026"}).json()
    denied = client.post(
        "/api/auth/password/change",
        headers=_headers(first["token"]),
        json={"current_password": "wrong-password", "new_password": "Consumer-Next-Password1",
              "confirm_password": "Consumer-Next-Password1"},
    )
    assert denied.status_code == 401
    changed = client.post(
        "/api/auth/password/change",
        headers=_headers(first["token"]),
        json={"current_password": "Consumer-Permanent-2026", "new_password": "Consumer-Next-Password1",
              "confirm_password": "Consumer-Next-Password1"},
    )
    assert changed.status_code == 200
    assert client.get("/api/dashboard/summary", headers=_headers(other["token"])).status_code == 401
    assert client.get("/api/dashboard/summary", headers=_headers(changed.json()["token"])).status_code == 200


def test_forgot_password_recovery_and_non_enumeration(tmp_path):
    _, auth, client = _app(tmp_path, production=True)
    user_id = _setup_consumer(auth)
    _complete_first_change(client, user_id)
    unknown = client.post("/api/auth/recovery/start", json={"user_id": "missing-user"})
    known = client.post("/api/auth/recovery/start", json={"user_id": user_id})
    assert unknown.status_code == 200
    assert known.status_code == 200
    assert set(unknown.json()) == set(known.json())
    assert len(unknown.json()["questions"]) == len(known.json()["questions"]) == 3
    assert "not found" not in json.dumps(unknown.json()).lower()
    failed = client.post(
        "/api/auth/recovery/verify",
        json={"recovery_id": known.json()["recovery_id"], "answers": WRONG_ANSWERS},
    )
    assert failed.status_code == 401
    assert failed.json()["code"] == "invalid_recovery"
    assert "CQ01" not in failed.text
    assert "which" not in failed.text.lower()
    retry = client.post("/api/auth/recovery/start", json={"user_id": user_id})
    verified = client.post(
        "/api/auth/recovery/verify",
        json={"recovery_id": retry.json()["recovery_id"], "answers": ANSWERS},
    )
    assert verified.status_code == 200
    token = verified.json()["token"]
    assert verified.json()["scope"] == "password_recovery"
    blocked = client.get("/api/dashboard/summary", headers=_headers(token))
    assert blocked.status_code == 403
    complete = client.post(
        "/api/auth/recovery/complete",
        headers=_headers(token),
        json={"new_password": "Recovered-Password-2026", "confirm_password": "Recovered-Password-2026"},
    )
    assert complete.status_code == 200
    assert "token" not in complete.json()
    row = auth._read()["accounts"][user_id]
    changed_at = datetime.fromisoformat(row["password_changed_at"].replace("Z", "+00:00"))
    expires = datetime.fromisoformat(row["password_expiry_date"].replace("Z", "+00:00"))
    assert timedelta(days=89, hours=23) < expires - changed_at <= timedelta(days=PASSWORD_DAYS)
    assert row["must_change_password"] is False
    assert client.post(
        "/api/auth/login", json={"user_id": user_id, "password": "Consumer-Permanent-2026"}
    ).status_code == 401
    fresh = client.post("/api/auth/login", json={"user_id": user_id, "password": "Recovered-Password-2026"})
    assert fresh.status_code == 200
    assert fresh.json()["must_change_password"] is False


def test_recovery_rate_limit_and_cross_account_protection(tmp_path, monkeypatch):
    monkeypatch.setenv("HC_AUTH_MAX_FAILED_LOGINS", "2")
    _, auth, client = _app(tmp_path, production=True)
    alice = _setup_consumer(auth, "alice")
    bob = _setup_consumer(auth, "bob")
    _complete_first_change(client, alice, new="Alice-Permanent-2026")
    _complete_first_change(client, bob, new="Bob-Permanent-2026")
    start = client.post("/api/auth/recovery/start", json={"user_id": "alice"})
    for _ in range(2):
        failed = client.post(
            "/api/auth/recovery/verify",
            json={"recovery_id": start.json()["recovery_id"], "answers": WRONG_ANSWERS},
        )
        assert failed.status_code == 401
        assert failed.json()["code"] == "invalid_recovery"
    locked = client.post("/api/auth/recovery/start", json={"user_id": "alice"})
    assert locked.status_code == 200
    locked_verify = client.post(
        "/api/auth/recovery/verify",
        json={"recovery_id": locked.json()["recovery_id"], "answers": ANSWERS},
    )
    assert locked_verify.status_code == 401
    bob_start = client.post("/api/auth/recovery/start", json={"user_id": "bob"})
    bob_ok = client.post(
        "/api/auth/recovery/verify",
        json={"recovery_id": bob_start.json()["recovery_id"], "answers": ANSWERS},
    )
    assert bob_ok.status_code == 200
    hijack = client.post(
        "/api/auth/recovery/complete",
        headers=_headers(bob_ok.json()["token"]),
        json={
            "user_id": "alice",
            "patient_id": "alice",
            "new_password": "Hijacked-Password-2026",
            "confirm_password": "Hijacked-Password-2026",
        },
    )
    assert hijack.status_code == 200
    assert client.post("/api/auth/login", json={"user_id": "alice", "password": "Alice-Permanent-2026"}).status_code == 200
    assert client.post("/api/auth/login", json={"user_id": "alice", "password": "Hijacked-Password-2026"}).status_code == 401
    assert client.post("/api/auth/login", json={"user_id": "bob", "password": "Hijacked-Password-2026"}).status_code == 200
    alice_full = client.post("/api/auth/login", json={"user_id": "alice", "password": "Alice-Permanent-2026"}).json()
    foreign = client.post(
        "/api/auth/password/change",
        headers=_headers(alice_full["token"]),
        json={
            "user_id": "bob",
            "patient_id": "bob",
            "current_password": "Alice-Permanent-2026",
            "new_password": "Should-Not-Change-Bob1",
            "confirm_password": "Should-Not-Change-Bob1",
        },
    )
    assert foreign.status_code == 200
    assert client.post("/api/auth/login", json={"user_id": "bob", "password": "Hijacked-Password-2026"}).status_code == 200
    assert client.post("/api/auth/login", json={"user_id": "bob", "password": "Should-Not-Change-Bob1"}).status_code == 401


def test_security_gate_blocks_deep_link_note_and_android_back():
    out = _node(
        """
Nav.setSecurityGate(true);
const deep = Nav.peekDeepLink();
const noted = Nav.note('records');
const backed = Nav.back();
const system = Nav.handleSystemBack();
const leave = Nav.canLeaveApp();
dump({
  deep,
  noted,
  current: Nav.currentRoute(),
  gated: Nav.isSecurityGate(),
  handled: backed.handled,
  gatedFlag: backed.gated,
  system,
  leave,
  activated,
  session: session.getItem('hc_mobile_auth_session'),
});
"""
    )
    payload = json.loads(out)
    assert payload["deep"] is None
    assert payload["current"] == "dashboard"
    assert payload["gated"] is True
    assert payload["handled"] is True
    assert payload["gatedFlag"] is True
    assert payload["system"] is True
    assert payload["leave"] is False
    assert payload["activated"] == []
    assert "session-token" in payload["session"]
    nav_src = (ROOT / "js/health_vault/consumer_nav.js").read_text(encoding="utf-8")
    assert "setSecurityGate" in nav_src
    assert "isSecurityGate" in nav_src
    policy = (
        ROOT / "android/app/src/main/java/com/healthchecker/companion/ui/ConsumerInAppBackPolicy.kt"
    ).read_text(encoding="utf-8")
    assert "HCConsumerNav.handleSystemBack" in policy
    launcher = (
        ROOT / "android/app/src/main/java/com/healthchecker/companion/ui/ConsumerLauncherActivity.kt"
    ).read_text(encoding="utf-8")
    assert "evaluateJavascript" in launcher
    assert "webView.goBack" not in launcher


def test_consumer_surfaces_do_not_expose_bootstrap_password():
    for rel in (
        "index.html",
        "mobile.html",
        "js/health_vault/dashboard.js",
        "js/health_vault/mobile_consumer.js",
        "js/health_vault/consumer_nav.js",
        "js/health_vault/consumer_surfaces.js",
        "backend/health_vault/api.py",
    ):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert BOOTSTRAP not in text
    index = (ROOT / "index.html").read_text(encoding="utf-8")
    mobile = (ROOT / "mobile.html").read_text(encoding="utf-8")
    assert "Forgot password" in index
    assert "Forgot password" in mobile
    assert "password_enroll_questions" in index
    assert "password_recovery_flow" in index
    assert "settings_password_form" in index
    assert "mobile_recovery_flow" in mobile
    assert "mobile_recovery_enroll" in mobile
    assert "mobile_settings_password_form" in mobile
    dash = (ROOT / "js/health_vault/dashboard.js").read_text(encoding="utf-8")
    assert "setSecurityGate(true)" in dash
    mobile_js = (ROOT / "js/health_vault/mobile_consumer.js").read_text(encoding="utf-8")
    assert "/api/auth/recovery/start" in mobile_js
    assert "HCConsumerNav.setSecurityGate" in mobile_js
