from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.health_vault.api import create_health_vault_app
from backend.health_vault.auth import AuthenticationError, AuthenticationService, verify_password
from backend.health_vault.auth_recovery import (
    LocalPasswordRecoveryService,
    LocalRecoveryAuthorization,
    PasswordRecoveryError,
)
from backend.health_vault.models import MedicalDocument, create_measurement
from backend.health_vault.vault_store import VaultStore


KEY = b"Y" * 32


def authorization():
    return LocalRecoveryAuthorization(actor="authorized-test-admin", reason="test-recovery")


def setup_accounts(tmp_path):
    store = VaultStore(root=tmp_path / "vault", encryption_key=KEY)
    auth = AuthenticationService(store, bootstrap_password="Owner-Old-Password")
    auth.create_user(
        user_id="secondary", name="Secondary", email_identifier="secondary",
        password="Secondary-Password", must_change_password=False,
    )
    owner_token = auth.login("00000", "Owner-Old-Password")["token"]
    secondary_token = auth.login("secondary", "Secondary-Password")["token"]
    store.store(
        document=MedicalDocument(id="owner-doc", patient_id="00000", original_filename="owner.json"),
        measurements=[create_measurement(metric="weight", value=80, units="kg")], content=b"owner",
    )
    store.store(
        document=MedicalDocument(id="secondary-doc", patient_id="secondary", original_filename="secondary.json"),
        measurements=[create_measurement(metric="weight", value=70, units="kg")], content=b"secondary",
    )
    return store, auth, owner_token, secondary_token


def test_authorized_recovery_preserves_identity_health_and_revokes_sessions(tmp_path):
    store, auth, owner_token, secondary_token = setup_accounts(tmp_path)
    before_index = store.index_path.read_bytes()
    before = auth._read()
    before_account = dict(before["accounts"]["00000"])
    before["accounts"]["00000"]["failed_login_count"] = 4
    auth._write(before)

    result = LocalPasswordRecoveryService(auth, authorization_check=lambda _: True).recover(
        user_id="00000", new_password="Owner-New-Password", confirmation="Owner-New-Password",
        authorization=authorization(),
    )
    after = auth._read()
    account = after["accounts"]["00000"]
    assert result["account_status"] == "active"
    assert account["user_id"] == before_account["user_id"] == "00000"
    assert account["name"] == before_account["name"]
    assert account["failed_login_count"] == 0
    assert account["must_change_password"] is False
    assert not verify_password("Owner-Old-Password", account["password_hash"])
    assert verify_password("Owner-New-Password", account["password_hash"])
    changed = datetime.fromisoformat(account["password_changed_at"].replace("Z", "+00:00"))
    expiry = datetime.fromisoformat(account["password_expiry_date"].replace("Z", "+00:00"))
    assert timedelta(days=89, hours=23) < expiry - changed <= timedelta(days=90)
    with pytest.raises(AuthenticationError):
        auth.resolve(owner_token)
    assert auth.resolve(secondary_token)[0].user_id == "secondary"
    assert store.index_path.read_bytes() == before_index
    assert {row["patient_id"] for row in store.list_documents()} == {"00000", "secondary"}
    events = [row for row in after["audit"] if row["action"] == "authorized_local_password_recovery"]
    assert len(events) == 1 and events[0]["user_id"] == "00000"
    serialized = json.dumps(events)
    assert "Owner-New-Password" not in serialized and "password_hash" not in serialized


def test_recovery_failures_are_closed_and_do_not_affect_other_user(tmp_path):
    _, auth, _, _ = setup_accounts(tmp_path)
    service = LocalPasswordRecoveryService(auth, authorization_check=lambda _: False)
    with pytest.raises(PasswordRecoveryError, match="local_recovery_authorization_required"):
        service.recover(user_id="00000", new_password="Owner-New-Password",
                        confirmation="Owner-New-Password", authorization=authorization())
    allowed = LocalPasswordRecoveryService(auth, authorization_check=lambda _: True)
    for uid, password, confirm, error in (
        ("missing", "Valid-New-Password", "Valid-New-Password", "account_not_found"),
        ("00000", "short", "short", "password_policy_violation"),
        ("00000", "Valid-New-Password", "different", "password_confirmation_mismatch"),
    ):
        with pytest.raises(PasswordRecoveryError, match=error):
            allowed.recover(user_id=uid, new_password=password, confirmation=confirm, authorization=authorization())
    assert auth.login("secondary", "Secondary-Password")["user_id"] == "secondary"


def test_recovery_is_not_exposed_as_public_api(tmp_path):
    store = VaultStore(root=tmp_path / "vault", encryption_key=KEY)
    app = create_health_vault_app(store, production=True, bootstrap_password="Owner-Old-Password")
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/api/auth/reset-password" not in paths
    assert not any(
        ("reset-password" in path or "local-recovery" in path)
        or ("recover" in path and "/api/auth/recovery/" not in path)
        for path in paths
    )
    client = TestClient(app)
    assert client.post("/api/auth/reset-password", json={}).status_code in {401, 404}
