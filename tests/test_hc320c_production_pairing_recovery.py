from pathlib import Path

from fastapi.testclient import TestClient

from backend.health_vault.api import create_health_vault_app
from backend.health_vault.vault_store import VaultStore


ROOT = Path(__file__).resolve().parents[1]
PAIRING_SCRIPT = (ROOT / "scripts/start_healthchecker_secure_pairing.ps1").read_text(encoding="utf-8")


def test_secure_launcher_consumes_actual_pair_start_schema():
    assert "/api/companion/pair/start" in PAIRING_SCRIPT
    assert "$pair.pair_code" in PAIRING_SCRIPT
    assert "$pair.pairing_code" not in PAIRING_SCRIPT
    assert "Read-Host" in PAIRING_SCRIPT and "-AsSecureString" in PAIRING_SCRIPT
    assert "password_state_requires_user_action" in PAIRING_SCRIPT


def test_pairing_generator_after_full_auth_and_secondary_isolation(tmp_path):
    store = VaultStore(root=tmp_path / "vault", encryption_key=b"Q" * 32)
    app = create_health_vault_app(store, production=True, bootstrap_password="Owner-Temporary-Password")
    client = TestClient(app)

    restricted = client.post("/api/auth/login", json={"user_id": "00000", "password": "Owner-Temporary-Password"})
    assert restricted.status_code == 200
    assert restricted.json()["scope"] == "password_change"
    blocked = client.post(
        "/api/companion/pair/start",
        headers={"Authorization": f"Bearer {restricted.json()['token']}"},
        json={},
    )
    assert blocked.status_code == 403

    changed = client.post("/api/auth/password/change", headers={
        "Authorization": f"Bearer {restricted.json()['token']}"
    }, json={"current_password": "Owner-Temporary-Password", "new_password": "Owner-Production-Password"})
    assert changed.status_code == 200
    owner_start = client.post("/api/companion/pair/start", headers={
        "Authorization": f"Bearer {changed.json()['token']}"
    }, json={})
    assert owner_start.status_code == 200
    assert owner_start.json()["ok"] is True
    assert owner_start.json()["pair_code"]
    assert "pairing_code" not in owner_start.json()

    auth = app.state.auth_service
    auth.create_user(
        user_id="secondary", name="Secondary", email_identifier="secondary",
        password="Secondary-Production-Password", must_change_password=False,
    )
    secondary_login = client.post("/api/auth/login", json={
        "user_id": "secondary", "password": "Secondary-Production-Password"
    })
    secondary_start = client.post("/api/companion/pair/start", headers={
        "Authorization": f"Bearer {secondary_login.json()['token']}"
    }, json={})
    assert secondary_start.status_code == 200

    sessions = list(store._read_index()["companion_pair_sessions"].values())
    assert {row["patient_id"] for row in sessions} == {"00000", "secondary"}
    assert all(row["pair_code_hash"] and "pair_code" not in row for row in sessions)
