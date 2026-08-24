"""HC-320B fail-closed production security boundary acceptance."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.health_vault.api import create_health_vault_app
from backend.health_vault.auth import AuthenticationStateError
from backend.health_vault.production_runtime import ProductionRuntimeError, create_production_vault
from backend.health_vault.import_service import ImportService
from backend.health_vault.vault_store import VaultStore


KEY = b"P" * 32


def _production_app(tmp_path: Path):
    store = VaultStore(root=tmp_path / "vault", encryption_key=KEY)
    app = create_health_vault_app(
        store,
        production=True,
        bootstrap_password="Controlled-Enrollment-Only",
    )
    auth = app.state.auth_service
    auth.create_user(
        user_id="secondary",
        name="Secondary",
        email_identifier="secondary@test.invalid",
        password="Secondary-Secure-Password",
        must_change_password=False,
    )
    return store, auth, TestClient(app)


def _login(client: TestClient, user: str, password: str) -> str:
    response = client.post("/api/auth/login", json={"user_id": user, "password": password})
    assert response.status_code == 200
    return response.json()["token"]


def test_production_factory_requires_protected_key_and_encryption(tmp_path):
    with pytest.raises(ProductionRuntimeError, match="production_vault_activation_failed"):
        create_production_vault(
            environ={"HC_VAULT_ROOT": str(tmp_path / "vault"), "HC_VAULT_KEY_FILE": str(tmp_path / "missing")}
        )
    store = create_production_vault(
        environ={"HC_VAULT_ROOT": str(tmp_path / "encrypted"), "HC_VAULT_KEY_FILE": "ignored"},
        key_reader=lambda _: KEY,
    )
    assert store.encrypted is True
    assert not store.index_path.read_bytes().startswith(b"{")


def test_plaintext_or_corrupt_production_state_fails_closed(tmp_path):
    plain = VaultStore(root=tmp_path / "plain")
    with pytest.raises(RuntimeError, match="production_vault_encryption_required"):
        create_health_vault_app(plain, production=True, bootstrap_password="Not-Used")

    corrupt_root = tmp_path / "corrupt"
    corrupt_root.mkdir()
    (corrupt_root / "index.json").write_bytes(b"not-an-encrypted-vault")
    with pytest.raises(ProductionRuntimeError, match="production_vault_activation_failed"):
        create_production_vault(
            environ={"HC_VAULT_ROOT": str(corrupt_root), "HC_VAULT_KEY_FILE": "ignored"},
            key_reader=lambda _: KEY,
        )


def test_missing_or_corrupt_auth_never_recreates_default_owner(tmp_path):
    store = VaultStore(root=tmp_path / "vault", encryption_key=KEY)
    with pytest.raises(AuthenticationStateError, match="auth_bootstrap_credential_required"):
        create_health_vault_app(store, production=True)
    auth_path = store.root / "auth_registry.json"
    auth_path.write_bytes(b"corrupt-auth-state")
    with pytest.raises(AuthenticationStateError, match="auth_registry_invalid"):
        create_health_vault_app(store, production=True, bootstrap_password="Must-Not-Recover")


def test_every_production_clinical_route_rejects_missing_and_forged_sessions(tmp_path):
    _, _, client = _production_app(tmp_path)
    public = {
        ("POST", "/api/auth/login"),
        ("GET", "/api/auth/session"),
        ("POST", "/api/auth/password/change"),
        ("POST", "/api/auth/logout"),
        ("GET", "/api/auth/recovery/catalog"),
        ("POST", "/api/auth/recovery/start"),
        ("POST", "/api/auth/recovery/verify"),
        ("POST", "/api/auth/recovery/complete"),
        ("POST", "/api/companion/pair/confirm"),
        ("POST", "/api/companion/observations"),
        ("GET", "/api/companion/status"),
        ("GET", "/api/health-vault/batch-limits"),
    }
    for route in client.app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/"):
            continue
        for method in set(getattr(route, "methods", set())) - {"HEAD", "OPTIONS"}:
            if (method, path) in public:
                continue
            concrete = path.replace("{document_id}", "missing").replace("{device_id}", "missing")
            concrete = concrete.replace("{alert_id}", "missing").replace("{sensor_id}", "missing")
            response = client.request(method, concrete)
            assert response.status_code == 401, (method, path, response.status_code, response.text)
            forged = client.request(method, concrete, headers={"Authorization": "Bearer forged"})
            assert forged.status_code == 401, (method, path, forged.status_code, forged.text)


def test_revoked_and_expired_sessions_fail_on_legacy_clinical_api(tmp_path):
    _, auth, client = _production_app(tmp_path)
    token = _login(client, "secondary", "Secondary-Secure-Password")
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/health-vault/timeline", headers=headers).status_code == 200
    client.post("/api/auth/logout", headers=headers)
    assert client.get("/api/health-vault/timeline", headers=headers).status_code == 401

    token = _login(client, "secondary", "Secondary-Secure-Password")
    data = auth._read()
    data["sessions"][auth._token_hash(token)]["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat().replace("+00:00", "Z")
    auth._write(data)
    assert client.get("/api/health-vault/timeline", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_legacy_timeline_and_import_ignore_forged_patient_identity(tmp_path):
    store, auth, client = _production_app(tmp_path)
    data = store._read_index()
    data["documents"].append({
        "id": "robert-private",
        "patient_id": "00000",
        "status": "imported",
        "original_filename": "private-robert-record.pdf",
    })
    data["timeline_events"].append({
        "event_id": "robert-event",
        "patient_id": "00000",
        "summary": "private-robert-timeline",
    })
    store._write_index(data)

    token = _login(client, "secondary", "Secondary-Secure-Password")
    headers = {"Authorization": f"Bearer {token}"}
    timeline = client.get(
        "/api/health-vault/timeline?unified=true&patient_id=00000", headers=headers
    )
    assert timeline.status_code == 200
    assert "robert-private" not in timeline.text
    assert "private-robert-timeline" not in timeline.text

    imported = client.post(
        "/api/records/upload",
        headers=headers,
        data={"patient_id": "00000"},
        files={"file": ("secondary.json", b'{"systolic":120,"diastolic":70}', "application/json")},
    )
    assert imported.status_code == 200
    assert imported.json()["ok"] is True
    created = next(row for row in store.list_documents() if row["id"] == imported.json()["document_id"])
    assert created["patient_id"] == "secondary"


@pytest.mark.parametrize("path", [
    "/api/health-vault/timeline?unified=true&patient_id=00000",
    "/api/health-vault/doctor-visit?patient_id=00000",
    "/api/health-vault/intelligence?patient_id=00000",
    "/api/health-vault/import-log?patient_id=00000",
    "/api/health-vault/executive-briefing?patient_id=00000",
    "/api/health-vault/executive-briefing/print?patient_id=00000",
    "/api/ai-health/import-history?patient_id=00000",
    "/api/monitoring/status?patient_id=00000",
    "/api/guardian/status?patient_id=00000",
    "/api/guardian/alerts?patient_id=00000",
    "/api/guardian/baselines?patient_id=00000",
    "/api/guardian/cgm/sensors?patient_id=00000",
    "/api/guardian/cgm/inventory?patient_id=00000",
    "/api/guardian/cgm/continuity?patient_id=00000",
    "/api/guardian/cgm/data-gaps?patient_id=00000",
])
def test_legacy_reads_are_authenticated_identity_scoped(tmp_path, path):
    store, _, client = _production_app(tmp_path)
    data = store._read_index()
    data["documents"].append({
        "id": "owner-secret-marker",
        "patient_id": "00000",
        "status": "imported",
        "original_filename": "owner-secret-marker.pdf",
    })
    data["alerts"].append({
        "alert_id": "owner-secret-marker",
        "patient_id": "00000",
        "status": "active",
        "summary": "owner-secret-marker",
    })
    store._write_index(data)
    token = _login(client, "secondary", "Secondary-Secure-Password")
    response = client.get(path, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code < 500, (path, response.status_code, response.text)
    assert "owner-secret-marker" not in response.text


def test_bootstrap_password_cannot_return_after_change_or_registry_damage(tmp_path):
    store = VaultStore(root=tmp_path / "vault", encryption_key=KEY)
    client = TestClient(create_health_vault_app(
        store, production=True, bootstrap_password="Controlled-Enrollment-Only"
    ))
    restricted = _login(client, "00000", "Controlled-Enrollment-Only")
    changed = client.post(
        "/api/auth/password/change",
        headers={"Authorization": f"Bearer {restricted}"},
        json={
            "current_password": "Controlled-Enrollment-Only",
            "new_password": "Robert-Production-Password",
            "recovery_answers": [
                {"question_id": "CQ01", "answer": "Westfield School"},
                {"question_id": "CQ02", "answer": "Toronto"},
                {"question_id": "CQ03", "answer": "Buster"},
            ],
        },
    )
    assert changed.status_code == 200
    assert client.post(
        "/api/auth/login", json={"user_id": "00000", "password": "Controlled-Enrollment-Only"}
    ).status_code == 401
    assert b"Controlled-Enrollment-Only" not in (store.root / "auth_registry.json").read_bytes()
    (store.root / "auth_registry.json").unlink()
    with pytest.raises(AuthenticationStateError, match="auth_registry_missing_after_enrollment"):
        create_health_vault_app(
            store, production=True, bootstrap_password="Controlled-Enrollment-Only"
        )


def test_explicit_development_fixture_remains_available(tmp_path):
    store = VaultStore(root=tmp_path / "development")
    app = create_health_vault_app(store, production=False, test_users={"fixture": "fixture-password"})
    assert app.state.production_mode is False
    assert store.encrypted is False
    assert TestClient(app).get("/api/health-vault/timeline").status_code == 200


def test_scheduled_intake_and_gmail_use_encrypted_user_bound_runtime(tmp_path):
    store = VaultStore(root=tmp_path / "vault", encryption_key=KEY)
    service = ImportService(store=store, patient_id="secondary")
    result = service.import_health_record({
        "patient_id": "00000",
        "filename": "bound.json",
        "mime_type": "application/json",
        "document_type": "blood_pressure_screenshot",
        "content": b'{"systolic":120,"diastolic":70}',
    })
    assert result["ok"] is True
    assert result["document"]["patient_id"] == "secondary"

    root = Path(__file__).resolve().parents[1]
    intake = (root / "backend/health_vault/intake/runner.py").read_text(encoding="utf-8")
    acquisition = (root / "backend/health_vault/acquisition/runner.py").read_text(encoding="utf-8")
    for source in (intake, acquisition):
        assert "create_production_vault()" in source
        assert "HC_RUNTIME_PATIENT_ID" in source
        assert "runtime_patient_identity_required" in source
