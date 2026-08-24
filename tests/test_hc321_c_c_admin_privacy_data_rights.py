"""HC321-C-C: admin lifecycle, consent, export, amendment, deletion, authz denials."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.health_vault.api import create_health_vault_app
from backend.health_vault.auth import AuthenticationError, AuthenticationService
from backend.health_vault.privacy_rights import PrivacyDataRightsService, PrivacyRightsError
from backend.health_vault.vault_store import VaultStore

KEY = b"k" * 32


def _vault(tmp_path: Path) -> VaultStore:
    return VaultStore(root=tmp_path / "vault", encryption_key=KEY)


def _login(client: TestClient, user_id: str, password: str) -> str:
    response = client.post("/api/auth/login", json={"user_id": user_id, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["token"]


@pytest.fixture
def client(tmp_path):
    vault = _vault(tmp_path)
    auth = AuthenticationService(vault, bootstrap_password="owner-password-xx")
    # Force owner password usable for full-scope session after change.
    owner = auth.get_account("00000")
    assert owner is not None
    # Bootstrap requires password change; complete it via change_password.
    app = create_health_vault_app(store=vault, production=True, bootstrap_password="owner-password-xx")
    with TestClient(app) as test_client:
        boot = test_client.post(
            "/api/auth/login", json={"user_id": "00000", "password": "owner-password-xx"}
        )
        assert boot.status_code == 200
        token = boot.json()["token"]
        changed = test_client.post(
            "/api/auth/password/change",
            headers={"Authorization": f"Bearer {token}"},
            json={"current_password": "owner-password-xx", "new_password": "owner-password-yy",
                  "recovery_answers": [
                      {"question_id": "CQ01", "answer": "Westfield School"},
                      {"question_id": "CQ02", "answer": "Toronto"},
                      {"question_id": "CQ03", "answer": "Buster"},
                  ]},
        )
        assert changed.status_code == 200
        yield test_client, changed.json()["token"], vault


def test_admin_create_disable_role_and_unauthorized(client):
    test_client, owner_token, vault = client
    headers = {"Authorization": f"Bearer {owner_token}"}

    created = test_client.post(
        "/api/admin/users",
        headers=headers,
        json={
            "user_id": "10001",
            "name": "Second",
            "email_identifier": "second",
            "password": "secondary-pass",
            "role": "user",
        },
    )
    assert created.status_code == 200
    assert created.json()["user"]["role"] == "user"

    # User cannot list admin users.
    user_login = test_client.post(
        "/api/auth/login", json={"user_id": "10001", "password": "secondary-pass"}
    )
    # must_change_password scope — change first
    assert user_login.status_code == 200
    ut = user_login.json()["token"]
    changed = test_client.post(
        "/api/auth/password/change",
        headers={"Authorization": f"Bearer {ut}"},
        json={"current_password": "secondary-pass", "new_password": "secondary-pass2",
              "recovery_answers": [
                  {"question_id": "CQ01", "answer": "Westfield School"},
                  {"question_id": "CQ02", "answer": "Toronto"},
                  {"question_id": "CQ03", "answer": "Buster"},
              ]},
    )
    ut = changed.json()["token"]
    denied = test_client.get("/api/admin/users", headers={"Authorization": f"Bearer {ut}"})
    assert denied.status_code == 403

    # Silent escalation blocked: user cannot set own role; admin create as owner blocked.
    bad_role = test_client.post(
        "/api/admin/users/10001/role",
        headers={"Authorization": f"Bearer {ut}"},
        json={"role": "admin"},
    )
    assert bad_role.status_code == 403
    owner_assign = test_client.post(
        "/api/admin/users",
        headers=headers,
        json={
            "user_id": "10002",
            "name": "X",
            "email_identifier": "x",
            "password": "password123",
            "role": "owner",
        },
    )
    assert owner_assign.status_code == 403

    disabled = test_client.post(
        "/api/admin/users/10001/status",
        headers=headers,
        json={"status": "disabled"},
    )
    assert disabled.status_code == 200
    locked = test_client.post(
        "/api/auth/login", json={"user_id": "10001", "password": "secondary-pass2"}
    )
    assert locked.status_code == 401


def test_consent_export_amend_deletion_and_cross_user_isolation(client):
    test_client, owner_token, vault = client
    headers = {"Authorization": f"Bearer {owner_token}"}

    # Seed docs for two patients.
    index = vault._read_index()
    index["documents"] = [
        {"id": "d1", "patient_id": "00000", "document_type": "lab"},
        {"id": "d2", "patient_id": "10001", "document_type": "lab"},
    ]
    index["measurements"] = [
        {"document_id": "d1", "metric": "glucose", "value": 5.0},
        {"document_id": "d2", "metric": "glucose", "value": 6.0},
    ]
    vault._write_index(index)

    notice = test_client.get("/api/privacy/notice", headers=headers)
    assert notice.status_code == 200
    assert notice.json()["certification_claims"] == []
    assert "PLACEHOLDER" in notice.json()["privacy_notice_version"]

    granted = test_client.post(
        "/api/privacy/consent",
        headers=headers,
        json={"purpose": "product_use"},
    )
    assert granted.status_code == 200
    withdrawn = test_client.post(
        "/api/privacy/consent/withdraw",
        headers=headers,
        json={"purpose": "product_use"},
    )
    assert withdrawn.status_code == 200

    exported = test_client.get("/api/privacy/export", headers=headers)
    assert exported.status_code == 200
    package = exported.json()["export"]
    assert package["patient_id"] == "00000"
    assert package["document_count"] == 1
    assert all(d["patient_id"] == "00000" for d in package["documents"])

    amended = test_client.post(
        "/api/privacy/amend",
        headers=headers,
        json={"amendments": {"display_name": "Robert Updated"}},
    )
    assert amended.status_code == 200
    assert vault.get_profile("00000")["display_name"] == "Robert Updated"

    # Fail closed without confirmation.
    bad = test_client.post(
        "/api/privacy/deletion/request",
        headers=headers,
        json={"confirmation": "nope"},
    )
    assert bad.status_code == 400

    req = test_client.post(
        "/api/privacy/deletion/request",
        headers=headers,
        json={"confirmation": "DELETE"},
    )
    assert req.status_code == 200
    token = req.json()["confirmation_token"]
    confirmed = test_client.post(
        "/api/privacy/deletion/confirm",
        headers=headers,
        json={"confirmation": "DELETE", "confirmation_token": token},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["documents_removed"] == 1
    remaining = vault.list_documents()
    assert all(d.get("patient_id") != "00000" for d in remaining)
    assert any(d.get("patient_id") == "10001" for d in remaining)
    # Audit must not recreate clinical payload.
    privacy = PrivacyDataRightsService(vault)._read()
    blob = str(privacy.get("audit"))
    assert "5.0" not in blob and "glucose" not in blob


def test_unauthorized_privacy_and_admin_without_token(tmp_path):
    vault = _vault(tmp_path)
    AuthenticationService(vault, bootstrap_password="owner-password-xx")
    app = create_health_vault_app(store=vault, production=True, bootstrap_password="owner-password-xx")
    with TestClient(app) as test_client:
        assert test_client.get("/api/admin/users").status_code == 401
        assert test_client.get("/api/privacy/export").status_code == 401
