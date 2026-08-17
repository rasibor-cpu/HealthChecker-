"""HC-319A single-origin consumer runtime acceptance tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.health_vault.api import create_health_vault_app
from backend.health_vault.vault_store import VaultStore


def make_client(tmp_path) -> TestClient:
    store = VaultStore(root=tmp_path / "vault", encryption_key=b"R" * 32)
    return TestClient(create_health_vault_app(store))


def test_root_serves_consumer_index(tmp_path):
    response = make_client(tmp_path).get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "HealthChecker" in response.text
    assert 'href="style.css"' in response.text


def test_allowlisted_static_css_and_javascript_are_served(tmp_path):
    client = make_client(tmp_path)
    css = client.get("/style.css")
    script = client.get("/js/health_vault/dashboard.js")
    root_script = client.get("/app.js")
    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    assert root_script.status_code == 200


def test_api_routes_remain_available_and_same_origin_login_works(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/api/auth/session").status_code == 401
    login = client.post("/api/auth/login", json={"user_id": "00000", "password": "123456"})
    assert login.status_code == 200
    assert login.json()["must_change_password"] is True
    token = login.json()["token"]
    session = client.get("/api/auth/session", headers={"Authorization": f"Bearer {token}"})
    assert session.status_code == 200
    assert session.json()["user_id"] == "00000"


def test_repository_and_sensitive_paths_are_not_web_accessible(tmp_path):
    client = make_client(tmp_path)
    forbidden = (
        "/vault_storage/index.json",
        "/evidence/HC318C_CONSUMER_ACCEPTANCE_VALIDATION.md",
        "/scratch/run_s4u_install.ps1",
        "/hc313a_state/acquisition_state.json",
        "/hc_intake/completed/record.pdf",
        "/config/secret.json",
        "/credentials.json",
        "/backend/health_vault/api.py",
        "/.git/config",
    )
    for path in forbidden:
        response = client.get(path)
        assert response.status_code == 404, path


def test_static_mount_rejects_directory_traversal(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/js/../backend/health_vault/api.py").status_code == 404
    assert client.get("/css/%2e%2e/backend/health_vault/api.py").status_code == 404
