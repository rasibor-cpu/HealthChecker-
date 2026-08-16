"""HC-318B production authentication foundation acceptance tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.health_vault.api import create_health_vault_app
from backend.health_vault.auth import AuthenticationService, verify_password
from backend.health_vault.dashboard_service import DashboardService
from backend.health_vault.models import UserDashboardPreferences, create_measurement
from backend.health_vault.vault_store import VaultStore


@pytest.fixture
def auth_app(tmp_path):
    store = VaultStore(root=tmp_path / "vault", encryption_key=b"A" * 32)
    app = create_health_vault_app(store)
    return store, app.state.auth_service, TestClient(app)


def login(client, user_id, password):
    return client.post("/api/auth/login", json={"user_id": user_id, "password": password})


def test_bootstrap_owner_is_exact_idempotent_and_hash_only(auth_app):
    store, auth, _ = auth_app
    owner = auth.get_account("00000")
    assert owner is not None
    assert owner.user_id == "00000"
    assert owner.name == "Robert Asibor"
    assert owner.email_identifier == "00000"
    assert owner.role == "owner"
    assert owner.account_status == "password_change_required"
    assert owner.must_change_password is True
    assert owner.password_changed_at is None
    assert owner.password_expiry_date is None
    assert owner.password_hash.startswith("scrypt$")
    assert verify_password("123456", owner.password_hash)
    assert "123456" not in store.index_path.read_bytes().decode("latin-1")
    assert b"123456" not in auth.path.read_bytes()
    original_hash = owner.password_hash
    assert auth.bootstrap_owner("replacement-must-not-apply") is False
    assert auth.get_account("00000").password_hash == original_hash


def test_first_login_is_restricted_until_secure_password_change(auth_app):
    _, _, client = auth_app
    first = login(client, "00000", "123456")
    assert first.status_code == 200
    restricted = first.json()
    assert restricted["must_change_password"] is True
    headers = {"Authorization": f"Bearer {restricted['token']}"}
    denied = client.get("/api/dashboard/summary", headers=headers)
    assert denied.status_code == 403
    assert denied.json()["code"] == "password_change_required"

    changed = client.post(
        "/api/auth/password/change", headers=headers,
        json={"current_password": "123456", "new_password": "Robert-Secure-2026"},
    )
    assert changed.status_code == 200
    body = changed.json()
    assert body["must_change_password"] is False
    assert body["password_changed_at"]
    changed_at = datetime.fromisoformat(body["password_changed_at"].replace("Z", "+00:00"))
    expires = datetime.fromisoformat(body["password_expiry_date"].replace("Z", "+00:00"))
    assert timedelta(days=29, hours=23) < expires - changed_at <= timedelta(days=30)
    assert client.get("/api/dashboard/summary", headers=headers).status_code == 401
    full_headers = {"Authorization": f"Bearer {body['token']}"}
    assert client.get("/api/dashboard/summary", headers=full_headers).status_code == 200
    assert login(client, "00000", "123456").status_code == 401


def test_password_policy_rejects_short_and_reused_passwords(auth_app):
    _, _, client = auth_app
    token = login(client, "00000", "123456").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    for candidate in ("short", "123456"):
        response = client.post(
            "/api/auth/password/change", headers=headers,
            json={"current_password": "123456", "new_password": candidate},
        )
        assert response.status_code == 400
        assert response.json()["code"] == "password_policy_violation"


def test_expired_password_gets_change_only_session(auth_app):
    _, auth, client = auth_app
    auth.create_user(user_id="expired", name="Expired User", email_identifier="expired@example.test",
                     password="Current-Password", must_change_password=False)
    data = auth._read()
    data["accounts"]["expired"]["password_expiry_date"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat().replace("+00:00", "Z")
    auth._write(data)
    result = login(client, "expired", "Current-Password")
    assert result.status_code == 200
    assert result.json()["scope"] == "password_change"
    headers = {"Authorization": f"Bearer {result.json()['token']}"}
    assert client.get("/api/records", headers=headers).status_code == 403


def test_authentication_boundary_rejects_unknown_forged_and_logged_out_sessions(auth_app):
    _, auth, client = auth_app
    assert login(client, "unknown", "anything").status_code == 401
    assert client.get("/api/records", headers={"Authorization": "Bearer forged"}).status_code == 401
    auth.create_user(user_id="active", name="Active User", email_identifier="active@example.test",
                     password="Active-Password", must_change_password=False)
    token = login(client, "active", "Active-Password").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/auth/session", headers=headers).json()["user_id"] == "active"
    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/records", headers=headers).status_code == 401


def test_new_users_are_empty_and_robert_data_never_leaks(auth_app):
    store, auth, client = auth_app
    data = store._read_index()
    data["documents"].extend([
        {"id": "robert-doc", "patient_id": "00000", "status": "parsed", "original_filename": "robert-private.pdf"},
        {"id": "synthetic-doc", "patient_id": "fixture-patient", "status": "parsed",
         "original_filename": "synthetic.pdf", "data_classification": "synthetic_test"},
    ])
    data["measurements"].append(create_measurement(document_id="robert-doc", metric="glucose", value=7.1).to_dict())
    data["observations"].append({"observation_id": "robert-obs", "patient_id": "00000",
                                 "fact": "Robert private", "evidence": [{"document_id": "robert-doc"}]})
    store._write_index(data)
    auth.create_user(user_id="new-user", name="New User", email_identifier="new@example.test",
                     password="New-User-Password", must_change_password=False)
    token = login(client, "new-user", "New-User-Password").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    records = client.get("/api/records", headers=headers)
    assert records.status_code == 200
    assert records.json() == {"records": []}
    assert client.get("/api/records/robert-doc", headers=headers).status_code == 404
    dashboard_body = client.get("/api/dashboard/summary", headers=headers).json()
    dashboard = json.dumps(dashboard_body)
    status_widget = next(widget for widget in dashboard_body["widgets"] if widget["widget_id"] == "status_summary")
    assert status_widget["payload"]["measurements_count"] == 0
    assert "Robert private" not in dashboard
    assert "robert-private.pdf" not in dashboard
    assert "synthetic.pdf" not in dashboard
    dashboard_service = DashboardService(store)
    dashboard_service.save_preferences("00000", UserDashboardPreferences(theme="dark"))
    dashboard_service.save_preferences("new-user", UserDashboardPreferences(theme="light"))
    assert dashboard_service.get_preferences("00000").theme == "dark"
    assert dashboard_service.get_preferences("new-user").theme == "light"


def test_account_registry_is_encrypted_and_audits_are_secret_free(auth_app):
    _, auth, client = auth_app
    login(client, "00000", "wrong-password")
    persisted = auth.path.read_bytes()
    assert not persisted.startswith(b"{")
    assert b"password_hash" not in persisted
    data = auth._read()
    serialized_audit = json.dumps(data["audit"])
    assert "123456" not in serialized_audit
    assert "wrong-password" not in serialized_audit
    assert all("password_hash" not in event for event in data["audit"])
