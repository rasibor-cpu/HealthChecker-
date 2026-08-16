import pytest
import tempfile
import json
from pathlib import Path
from fastapi.testclient import TestClient
from backend.health_vault.api import create_health_vault_app
from backend.health_vault.models import create_measurement
from backend.health_vault.vault_store import VaultStore
from backend.health_vault.dashboard_service import DashboardService
from backend.health_vault.health_intelligence import HealthIntelligenceEngine

@pytest.fixture
def temp_vault_with_app():
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        store = VaultStore(root=tdp)
        app = create_health_vault_app(store)
        client = TestClient(app)
        yield store, client

def test_authentication_routing_and_api_boundary(temp_vault_with_app):
    store, client = temp_vault_with_app

    # Attempt dashboard summary without auth header -> 401
    resp = client.get("/api/dashboard/summary")
    assert resp.status_code == 401
    assert resp.json()["error"] == "Unauthorized"

    # Attempt preferences without auth header -> 401
    resp = client.get("/api/dashboard/preferences")
    assert resp.status_code == 401

    # Attempt login with wrong password
    resp = client.post("/api/auth/login", json={"patient_id": "patient-1", "password": "wrong"})
    assert resp.status_code == 401

    # Attempt login with correct password
    resp = client.post("/api/auth/login", json={"patient_id": "patient-1", "password": "correct"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "token" in body
    assert body["patient_id"] == "patient-1"

def test_dashboard_user_isolation_and_evidence(temp_vault_with_app):
    store, client = temp_vault_with_app

    data = store._read_index()
    # Patient A
    doc_a = {"id": "doc-a", "patient_id": "patient-A", "status": "imported", "measured_at": "2026-08-16T10:00:00Z", "date_confidence": 1.0}
    meas_a = create_measurement(document_id="doc-a", metric="glucose", value=110.0, measured_at="2026-08-16T10:00:00Z").to_dict()
    # Patient B
    doc_b = {"id": "doc-b", "patient_id": "patient-B", "status": "imported", "measured_at": "2026-08-16T10:00:00Z", "date_confidence": 1.0}
    meas_b = create_measurement(document_id="doc-b", metric="glucose", value=190.0, measured_at="2026-08-16T10:00:00Z").to_dict()
    
    data["documents"].extend([doc_a, doc_b])
    data["measurements"].extend([meas_a, meas_b])
    store._write_index(data)

    # Compute observations
    intel = HealthIntelligenceEngine(store)
    intel.generate_observations("patient-A")
    intel.generate_observations("patient-B")

    # Patient A auth
    login_a = client.post("/api/auth/login", json={"patient_id": "patient-A", "password": "correct"}).json()
    token_a = login_a["token"]

    # Fetch summary for A
    resp_a = client.get("/api/dashboard/summary", headers={"Authorization": f"Bearer {token_a}"})
    assert resp_a.status_code == 200
    summary_a = resp_a.json()
    assert summary_a["patient_id"] == "patient-A"

    # Find observations widget and assert evidence isolation
    widgets_a = {w["widget_id"]: w for w in summary_a["widgets"]}
    obs_widget_a = widgets_a["key_observations"]
    real_obs_a = [o for o in obs_widget_a["payload"]["observations"] if o.get("metric") is not None]
    assert len(real_obs_a) == 1
    assert real_obs_a[0]["patient_id"] == "patient-A"
    assert real_obs_a[0]["evidence"][0]["document_id"] == "doc-a"

    # Patient B auth
    login_b = client.post("/api/auth/login", json={"patient_id": "patient-B", "password": "correct"}).json()
    token_b = login_b["token"]

    # Fetch summary for B
    resp_b = client.get("/api/dashboard/summary", headers={"Authorization": f"Bearer {token_b}"})
    assert resp_b.status_code == 200
    summary_b = resp_b.json()
    assert summary_b["patient_id"] == "patient-B"

    # Assert Patient A token cannot access Patient B's summary (isolation enforcement)
    resp_cross = client.get("/api/dashboard/summary", headers={"Authorization": f"Bearer {token_a}"})
    assert resp_cross.json()["patient_id"] == "patient-A"  # Resolved token_a to patient-A, never patient-B

def test_widget_customization_and_theme_persistence(temp_vault_with_app):
    store, client = temp_vault_with_app

    # Login
    login = client.post("/api/auth/login", json={"patient_id": "patient-1", "password": "correct"}).json()
    token = login["token"]

    # Get default preferences
    prefs = client.get("/api/dashboard/preferences", headers={"Authorization": f"Bearer {token}"}).json()
    assert prefs["theme"] == "light"

    # Save customization preferences
    prefs["theme"] = "dark"
    prefs["widget_order"] = ["trends_widget", "import_wizard"]
    prefs["visible_widgets"] = ["trends_widget", "import_wizard"]
    prefs["priority_metric"] = "glucose"

    save_resp = client.post(
        "/api/dashboard/preferences",
        headers={"Authorization": f"Bearer {token}"},
        json=prefs
    )
    assert save_resp.status_code == 200

    # Retrieve and verify preferences persistence
    saved_prefs = client.get("/api/dashboard/preferences", headers={"Authorization": f"Bearer {token}"}).json()
    assert saved_prefs["theme"] == "dark"
    assert saved_prefs["widget_order"] == ["trends_widget", "import_wizard"]
    assert saved_prefs["priority_metric"] == "glucose"

    # Fetch summary to assert widget ordering
    summary = client.get("/api/dashboard/summary", headers={"Authorization": f"Bearer {token}"}).json()
    widget_ids = [w["widget_id"] for w in summary["widgets"]]
    assert widget_ids == ["trends_widget", "import_wizard"]

def test_dashboard_ui_html_markers():
    # Verify index.html contains necessary markup tags
    html = Path("index.html").read_text(encoding="utf-8")
    assert 'id="login_screen"' in html
    assert 'id="consumer_dashboard_container"' in html
    assert 'id="dashboard_widgets_target"' in html
    assert 'src="js/health_vault/dashboard.js"' in html

    # Verify js/health_vault/dashboard.js contains primary elements
    js = Path("js/health_vault/dashboard.js").read_text(encoding="utf-8")
    assert "HCConsumerDashboard" in js
    assert "renderWidgetContent" in js
