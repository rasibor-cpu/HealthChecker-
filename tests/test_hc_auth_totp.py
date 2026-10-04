from __future__ import annotations

import json
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from backend.health_vault.api import create_health_vault_app
from backend.health_vault.auth import AuthenticationError
from backend.health_vault.auth_factors import matching_totp_counter, totp_code
from backend.health_vault.vault_store import VaultStore


PASSWORD = "Strong-Password-2026"
USER_ID = "totp-user"


@pytest.fixture
def auth_setup(tmp_path):
    store = VaultStore(root=tmp_path / "vault", encryption_key=b"T" * 32)
    app = create_health_vault_app(store, production=False)
    auth = app.state.auth_service
    auth.create_user(
        user_id=USER_ID,
        name="TOTP Test",
        email_identifier="totp@example.test",
        password=PASSWORD,
        must_change_password=False,
    )
    return store, auth, TestClient(app)


def enroll(auth, client):
    token = auth.login(USER_ID, PASSWORD)["token"]
    headers = {"Authorization": f"Bearer {token}"}
    started = client.post(
        "/api/auth/totp/setup/start",
        headers=headers,
        json={"current_password": PASSWORD},
    )
    assert started.status_code == 200
    secret = started.json()["secret"]
    confirmed = client.post(
        "/api/auth/totp/setup/confirm",
        headers=headers,
        json={"code": totp_code(secret, int(time.time() // 30))},
    )
    assert confirmed.status_code == 200
    return secret, confirmed.json()["recovery_codes"], token


def recovery_login(auth, code):
    challenge = auth.login(USER_ID, PASSWORD)
    return auth.verify_login_totp(challenge["challenge_token"], recovery_code=code)


def test_totp_matches_rfc6238_sha1_vector():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert totp_code(secret, 1) == "287082"
    assert matching_totp_counter(secret, "287082", now=59) == 1
    assert matching_totp_counter(secret, "000000", now=59) is None


def test_totp_enrollment_requires_password_then_valid_confirmation(auth_setup):
    _, auth, client = auth_setup
    token = auth.login(USER_ID, PASSWORD)["token"]
    trusted = auth.issue_trusted_device(token, "test-device-pre-totp-01")
    headers = {"Authorization": f"Bearer {token}"}
    wrong_password = client.post(
        "/api/auth/totp/setup/start",
        headers=headers,
        json={"current_password": "wrong-password"},
    )
    assert wrong_password.status_code == 401
    started = client.post(
        "/api/auth/totp/setup/start",
        headers=headers,
        json={"current_password": PASSWORD},
    )
    assert started.status_code == 200
    body = started.json()
    assert body["provisioning_uri"].startswith("otpauth://totp/")
    assert "secret=" + body["secret"] in body["provisioning_uri"]

    invalid = client.post(
        "/api/auth/totp/setup/confirm", headers=headers, json={"code": "000000"}
    )
    assert invalid.status_code == 401
    assert auth.safe_session(token)["totp_enabled"] is False

    confirmed = client.post(
        "/api/auth/totp/setup/confirm",
        headers=headers,
        json={"code": totp_code(body["secret"], int(time.time() // 30) + 1)},
    )
    assert confirmed.status_code == 200
    codes = confirmed.json()["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10
    with pytest.raises(AuthenticationError):
        auth.safe_session(token)
    with pytest.raises(AuthenticationError):
        auth.restore_trusted_device(
            trusted["device_id"], trusted["trusted_device_token"]
        )
    assert auth.get_account(USER_ID) is not None
    assert body["secret"].encode() not in auth.path.read_bytes()


def test_login_requires_totp_and_rejects_replay(auth_setup):
    _, auth, client = auth_setup
    secret, _, _ = enroll(auth, client)

    first = client.post("/api/auth/login", json={"user_id": USER_ID, "password": PASSWORD})
    assert first.status_code == 200
    challenge = first.json()
    assert challenge["requires_totp"] is True
    assert "token" not in challenge

    wrong = client.post(
        "/api/auth/login/totp",
        json={"challenge_token": challenge["challenge_token"], "code": "000000"},
    )
    assert wrong.status_code == 401
    valid_code = totp_code(secret, int(time.time() // 30) + 1)
    verified = client.post(
        "/api/auth/login/totp",
        json={"challenge_token": challenge["challenge_token"], "code": valid_code},
    )
    assert verified.status_code == 200
    assert verified.json()["token"]

    next_challenge = client.post(
        "/api/auth/login", json={"user_id": USER_ID, "password": PASSWORD}
    ).json()
    replay = client.post(
        "/api/auth/login/totp",
        json={"challenge_token": next_challenge["challenge_token"], "code": valid_code},
    )
    assert replay.status_code == 401
    assert auth.safe_session(verified.json()["token"])["totp_enabled"] is True


def test_totp_login_preserves_required_password_change_scope(auth_setup):
    _, auth, client = auth_setup
    secret, _, _ = enroll(auth, client)
    data = auth._read()
    data["accounts"][USER_ID]["must_change_password"] = True
    auth._write(data)

    challenge = auth.login(USER_ID, PASSWORD)
    assert challenge["requires_totp"] is True
    assert challenge["scope"] == "password_change"
    assert challenge["must_change_password"] is True

    verified = auth.verify_login_totp(
        challenge["challenge_token"],
        code=totp_code(secret, int(time.time() // 30) + 1),
    )
    assert verified["scope"] == "password_change"
    assert verified["must_change_password"] is True
    assert auth.safe_session(verified["token"])["must_change_password"] is True
    with pytest.raises(AuthenticationError):
        auth.resolve(verified["token"], require_full=True)


def test_totp_login_challenge_expires(auth_setup, monkeypatch):
    _, auth, client = auth_setup
    secret, _, _ = enroll(auth, client)
    challenge = auth.login(USER_ID, PASSWORD)
    import backend.health_vault.auth as auth_module

    current_now = auth_module._now
    monkeypatch.setattr(auth_module, "_now", lambda: current_now() + timedelta(minutes=6))
    with pytest.raises(AuthenticationError):
        auth.verify_login_totp(
            challenge["challenge_token"],
            code=totp_code(secret, int(time.time() // 30) + 1),
        )


def test_totp_login_attempts_engage_factor_lockout(auth_setup, monkeypatch):
    _, auth, client = auth_setup
    secret, _, _ = enroll(auth, client)
    import backend.health_vault.auth as auth_module

    monkeypatch.setattr(auth_module, "max_failed_logins", lambda: 2)
    monkeypatch.setattr(auth_module, "lockout_minutes", lambda: 10)
    challenge = auth.login(USER_ID, PASSWORD)
    for _ in range(2):
        with pytest.raises(AuthenticationError):
            auth.verify_login_totp(challenge["challenge_token"], code="000000")
        challenge = auth.login(USER_ID, PASSWORD)

    with pytest.raises(AuthenticationError):
        auth.verify_login_totp(
            challenge["challenge_token"],
            code=totp_code(secret, int(time.time() // 30) + 1),
        )
    assert auth._read()["accounts"][USER_ID]["totp_locked_until"]


def test_recovery_codes_are_single_use_and_regeneration_invalidates_old_codes(auth_setup):
    _, auth, client = auth_setup
    _, original_codes, _ = enroll(auth, client)
    authenticated = recovery_login(auth, original_codes[9])
    headers = {"Authorization": f"Bearer {authenticated['token']}"}

    challenge = client.post(
        "/api/auth/login", json={"user_id": USER_ID, "password": PASSWORD}
    ).json()
    accepted = client.post(
        "/api/auth/login/totp",
        json={
            "challenge_token": challenge["challenge_token"],
            "recovery_code": original_codes[0],
        },
    )
    assert accepted.status_code == 200

    replay_challenge = client.post(
        "/api/auth/login", json={"user_id": USER_ID, "password": PASSWORD}
    ).json()
    rejected_reuse = client.post(
        "/api/auth/login/totp",
        json={
            "challenge_token": replay_challenge["challenge_token"],
            "recovery_code": original_codes[0],
        },
    )
    assert rejected_reuse.status_code == 401

    secret = str(auth._read()["accounts"][USER_ID]["totp_secret"])
    regenerated = client.post(
        "/api/auth/totp/recovery-codes/regenerate",
        headers=headers,
        json={
            "current_password": PASSWORD,
            "code": totp_code(secret, int(time.time() // 30) + 1),
        },
    )
    assert regenerated.status_code == 200
    new_codes = regenerated.json()["recovery_codes"]
    assert len(new_codes) == 10 and not set(new_codes).intersection(original_codes)
    old_challenge = client.post(
        "/api/auth/login", json={"user_id": USER_ID, "password": PASSWORD}
    ).json()
    old_code = client.post(
        "/api/auth/login/totp",
        json={"challenge_token": old_challenge["challenge_token"], "recovery_code": original_codes[1]},
    )
    assert old_code.status_code == 401


def test_totp_disable_requires_password_and_factor_and_invalidates_sessions(auth_setup):
    _, auth, client = auth_setup
    secret, codes, _ = enroll(auth, client)
    token = recovery_login(auth, codes[0])["token"]
    response = client.post(
        "/api/auth/totp/disable",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": PASSWORD, "code": totp_code(secret, int(time.time() // 30) + 1)},
    )
    assert response.status_code == 200
    with pytest.raises(AuthenticationError):
        auth.safe_session(token)
    assert "requires_totp" not in auth.login(USER_ID, PASSWORD)


def test_totp_provisioning_and_challenge_secrets_are_not_audited(auth_setup):
    _, auth, client = auth_setup
    token = auth.login(USER_ID, PASSWORD)["token"]
    setup = client.post(
        "/api/auth/totp/setup/start",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": PASSWORD},
    ).json()
    assert setup["secret"] not in json.dumps(auth._read().get("audit", []))
    confirmed = client.post(
        "/api/auth/totp/setup/confirm",
        headers={"Authorization": f"Bearer {token}"},
        json={"code": totp_code(setup["secret"], int(time.time() // 30) + 1)},
    )
    assert confirmed.status_code == 200
    challenge = auth.login(USER_ID, PASSWORD)
    assert challenge["challenge_token"] not in json.dumps(auth._read().get("audit", []))
    response = client.post(
        "/api/auth/login", json={"user_id": USER_ID, "password": PASSWORD}
    )
    assert response.headers["cache-control"] == "no-store"


def test_trusted_device_token_is_rotated_and_revoked_on_logout(auth_setup):
    _, auth, client = auth_setup
    session = auth.login(USER_ID, PASSWORD)
    device_id = "test-device-00000001"
    issued = auth.issue_trusted_device(session["token"], device_id, "Test handset")
    restored = client.post(
        "/api/auth/login/trusted-device",
        json={"device_id": device_id, "trusted_device_token": issued["trusted_device_token"]},
    )
    assert restored.status_code == 200
    body = restored.json()
    assert body["token"] and body["trusted_device_token"] != issued["trusted_device_token"]
    assert body["device_id"] == device_id

    with pytest.raises(AuthenticationError):
        auth.restore_trusted_device(device_id, issued["trusted_device_token"])

    logged_out = client.post(
        "/api/auth/logout",
        headers={"Authorization": f"Bearer {body['token']}"},
        json={"trusted_device_id": device_id},
    )
    assert logged_out.status_code == 200
    with pytest.raises(AuthenticationError):
        auth.restore_trusted_device(device_id, body["trusted_device_token"])


def test_trusted_device_expiry_and_password_change_invalidation(auth_setup, monkeypatch):
    _, auth, _ = auth_setup
    session = auth.login(USER_ID, PASSWORD)
    device_id = "test-device-00000002"
    issued = auth.issue_trusted_device(session["token"], device_id)
    import backend.health_vault.auth as auth_module

    current_now = auth_module._now
    monkeypatch.setattr(auth_module, "_now", lambda: current_now() + timedelta(days=31))
    with pytest.raises(AuthenticationError):
        auth.restore_trusted_device(device_id, issued["trusted_device_token"])
    monkeypatch.setattr(auth_module, "_now", current_now)

    another = auth.login(USER_ID, PASSWORD)
    second_device = "test-device-00000003"
    second = auth.issue_trusted_device(another["token"], second_device)
    auth.change_password(another["token"], PASSWORD, "New-Strong-Password-2026")
    with pytest.raises(AuthenticationError):
        auth.restore_trusted_device(second_device, second["trusted_device_token"])


def test_admin_disabling_account_revokes_trusted_device_permanently(auth_setup):
    _, auth, _ = auth_setup
    auth.create_user(
        user_id="auth-admin",
        name="Auth admin",
        email_identifier="admin@example.test",
        password=PASSWORD,
        role="admin",
        must_change_password=False,
    )
    admin_token = auth.login("auth-admin", PASSWORD)["token"]
    user_token = auth.login(USER_ID, PASSWORD)["token"]
    trusted = auth.issue_trusted_device(user_token, "test-device-admin-revoke")

    auth.set_account_status(admin_token, USER_ID, "disabled")
    auth.set_account_status(admin_token, USER_ID, "active")
    with pytest.raises(AuthenticationError):
        auth.restore_trusted_device(
            trusted["device_id"], trusted["trusted_device_token"]
        )


def test_totp_enabled_device_trust_requires_second_factor(auth_setup):
    _, auth, client = auth_setup
    secret, _, _ = enroll(auth, client)
    challenge = auth.login(USER_ID, PASSWORD)
    assert challenge["requires_totp"] is True
    assert "token" not in challenge
    authenticated = auth.verify_login_totp(
        challenge["challenge_token"],
        code=totp_code(secret, int(time.time() // 30) + 1),
    )
    trusted = auth.issue_trusted_device(authenticated["token"], "test-device-00000004")
    restored = auth.restore_trusted_device(
        trusted["device_id"], trusted["trusted_device_token"]
    )
    assert restored["token"] and restored["totp_enabled"] is True
