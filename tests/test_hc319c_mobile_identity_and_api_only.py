"""HC-319C authenticated mobile identity and API-only consumer security tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.health_vault.api import companion_observations_handler, create_health_vault_app
from backend.health_vault.companion.pairing import CompanionPairingService
from backend.health_vault.companion.security import generate_token, hash_token
from backend.health_vault.models import utc_now
from backend.health_vault.vault_store import VaultStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def mobile_app(tmp_path):
    store = VaultStore(root=tmp_path / "vault", encryption_key=b"M" * 32)
    app = create_health_vault_app(store)
    auth = app.state.auth_service
    auth.create_user(
        user_id="robert-test", name="Robert Test", email_identifier="robert@test.invalid",
        password="Robert-Test-Password", must_change_password=False,
    )
    auth.create_user(
        user_id="secondary-test", name="Secondary Test", email_identifier="secondary@test.invalid",
        password="Secondary-Test-Password", must_change_password=False,
    )
    return store, TestClient(app)


def login(client: TestClient, user_id: str, password: str) -> tuple[str, dict[str, str]]:
    result = client.post("/api/auth/login", json={"user_id": user_id, "password": password})
    assert result.status_code == 200
    token = result.json()["token"]
    return token, {"Authorization": f"Bearer {token}"}


def pair(client: TestClient, headers: dict[str, str], label: str = "Android") -> tuple[str, str]:
    started = client.post(
        "/api/companion/pair/start",
        headers=headers,
        json={"display_name": label, "patient_id": "forged-user"},
    )
    assert started.status_code == 200
    confirmed = client.post(
        "/api/companion/pair/confirm",
        json={"pair_code": started.json()["pair_code"], "device_label": label},
    )
    assert confirmed.status_code == 200
    return confirmed.json()["device_id"], confirmed.json()["device_token"]


def observation_body(batch_id: str) -> dict:
    return {
        "batch_id": batch_id,
        "nonce": f"nonce-{batch_id}",
        "sent_at": utc_now(),
        "observations": [{
            "observation_id": f"obs-{batch_id}",
            "source_record_id": f"source-{batch_id}",
            "metric_type": "heart_rate",
            "value": 72,
            "unit": "bpm",
            "measured_at": utc_now(),
            "acquisition_mode": "DELAYED",
        }],
    }


def test_authenticated_user_is_bound_through_pairing_and_delivery(mobile_app):
    store, client = mobile_app
    _, headers = login(client, "robert-test", "Robert-Test-Password")
    device_id, device_token = pair(client, headers)
    device = store.get_companion_device(device_id)
    assert device["patient_id"] == "robert-test"
    assert device["patient_id"] != "forged-user"

    delivered = companion_observations_handler(
        observation_body("bound"), authorization=f"Bearer {device_token}",
        store=store, local_dev=True,
    )
    assert delivered["ok"] is True
    rows = store.list_observations()
    assert rows and {row["patient_id"] for row in rows} == {"robert-test"}


def test_pairing_requires_full_hc318_identity_and_generic_identity_fails_closed(mobile_app):
    store, client = mobile_app
    assert client.post("/api/companion/pair/start", json={"display_name": "Phone"}).status_code == 401
    denied = CompanionPairingService(store=store).start_pairing(
        patient_id="default-patient", display_name="Legacy"
    )
    assert denied["status"] == "identity_required"


def test_secondary_user_cannot_list_or_revoke_prior_users_device(mobile_app):
    store, client = mobile_app
    _, robert_headers = login(client, "robert-test", "Robert-Test-Password")
    device_id, _ = pair(client, robert_headers, "Robert phone")
    _, secondary_headers = login(client, "secondary-test", "Secondary-Test-Password")
    listed = client.get("/api/companion/devices", headers=secondary_headers)
    assert listed.status_code == 200
    assert listed.json()["devices"] == []
    denied = client.delete(f"/api/companion/devices/{device_id}", headers=secondary_headers)
    assert denied.status_code == 403
    assert store.get_companion_device(device_id)["revoked"] is False


def test_logout_revokes_named_device_and_blocks_subsequent_sync(mobile_app):
    store, client = mobile_app
    token, headers = login(client, "robert-test", "Robert-Test-Password")
    device_id, device_token = pair(client, headers)
    logged_out = client.post("/api/auth/logout", headers=headers, json={"device_id": device_id})
    assert logged_out.status_code == 200
    assert logged_out.json()["device_revoked"] is True
    assert client.get("/api/auth/session", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    blocked = companion_observations_handler(
        observation_body("after-logout"), authorization=f"Bearer {device_token}",
        store=store, local_dev=True,
    )
    assert blocked["status"] == "unauthorized"


def test_missing_device_identity_never_falls_back_to_shared_patient(mobile_app):
    store, _ = mobile_app
    token = generate_token()
    store.upsert_companion_device({
        "device_id": "legacy-no-owner", "token_hash": hash_token(token, store_root=store.root),
        "revoked": False, "scopes": ["health_connect.observations"],
    })
    result = companion_observations_handler(
        observation_body("missing-owner"), authorization=f"Bearer {token}",
        store=store, local_dev=True,
    )
    assert result["status"] == "identity_required"
    assert store.list_observations() == []


def test_mobile_consumer_is_api_only_and_has_no_clinical_browser_store(mobile_app):
    _, client = mobile_app
    page = client.get("/mobile")
    script = client.get("/js/health_vault/mobile_consumer.js")
    assert page.status_code == script.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    csp = page.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "connect-src 'self'" in csp
    assert "object-src 'none'" in csp and "base-uri 'none'" in csp
    assert "mobile_consumer.js" in page.text
    forbidden_page_assets = ("vault_store.js", "import_engine.js", "timeline.js", "health_guardian.js", "service-worker.js")
    assert all(asset not in page.text for asset in forbidden_page_assets)
    forbidden_storage = ("localStorage", "indexedDB", "HCHealthVault", "caches.open", "cache.put")
    assert all(term not in script.text for term in forbidden_storage)
    assert "sessionStorage" in script.text
    assert "/api/dashboard/summary" in script.text
    assert "/api/records" in script.text
    assert "/api/records/upload" in script.text


def test_mobile_page_has_no_external_navigation_file_access_or_native_bridge(mobile_app):
    _, client = mobile_app
    combined = client.get("/mobile").text + client.get("/js/health_vault/mobile_consumer.js").text
    assert "file://" not in combined
    assert "http://" not in combined and "https://" not in combined
    assert "addJavascriptInterface" not in combined
    assert "window.open" not in combined
    assert "location.href" not in combined


def test_android_account_switch_cleanup_removes_all_user_bound_state():
    source = (ROOT / "android/app/src/main/java/com/healthchecker/companion/secure/SecurePrefs.kt").read_text(
        encoding="utf-8"
    )
    method = source.split("fun clearUserScopedState()", 1)[1].split("\n    }", 1)[0]
    for key in (
        "clearPairingCredentials", "KEY_CHANGES", "KEY_CHANGES_SCOPE", "KEY_PENDING_BATCH",
        "KEY_LAST_ATTEMPT", "KEY_LAST_SUCCESS", "KEY_LAST_ERROR", "KEY_QUEUED",
    ):
        assert key in method


def test_presentation_preferences_remain_server_scoped(mobile_app):
    _, client = mobile_app
    _, first = login(client, "robert-test", "Robert-Test-Password")
    _, second = login(client, "secondary-test", "Secondary-Test-Password")
    saved = client.post(
        "/api/dashboard/preferences", headers=first,
        json={"theme": "dark", "widget_order": ["records_summary"], "hidden_widgets": []},
    )
    assert saved.status_code == 200
    other = client.get("/api/dashboard/preferences", headers=second)
    assert other.status_code == 200
    assert other.json() != saved.json()
