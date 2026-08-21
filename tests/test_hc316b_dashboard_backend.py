import pytest
import tempfile
from pathlib import Path
from backend.health_vault.models import (
    UserDashboardPreferences,
    create_measurement,
)
from backend.health_vault.vault_store import VaultStore
from backend.health_vault.dashboard_service import DashboardService
from backend.health_vault.health_intelligence import HealthIntelligenceEngine

@pytest.fixture
def test_vault():
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        store = VaultStore(root=tdp)
        yield store

def test_dashboard_preferences_persistence(test_vault):
    service = DashboardService(test_vault)
    
    # Check defaults
    prefs = service.get_preferences("patient-1")
    assert prefs.theme == "light"
    assert "status_summary" in prefs.widget_order

    # Save customization preferences
    custom_prefs = UserDashboardPreferences(
        theme="dark",
        widget_order=["trends_widget", "key_observations"],
        visible_widgets=["trends_widget", "key_observations"],
        priority_metric="glucose",
    )
    service.save_preferences("patient-1", custom_prefs)

    # Retrieve and verify persistence
    saved = service.get_preferences("patient-1")
    assert saved.theme == "dark"
    assert saved.widget_order == ["trends_widget", "key_observations"]
    assert saved.priority_metric == "glucose"

def test_dashboard_widget_ordering_and_priorities(test_vault):
    service = DashboardService(test_vault)
    
    # Save a specific widget order
    custom_prefs = UserDashboardPreferences(
        theme="light",
        widget_order=["import_wizard", "status_summary"],
        visible_widgets=["import_wizard", "status_summary"],
    )
    service.save_preferences("patient-1", custom_prefs)
    
    # Synthesize dashboard summary
    summary = service.get_summary("patient-1")
    assert summary.patient_id == "patient-1"
    
    # The active widgets must match visible_widgets and be ordered accordingly
    widget_ids = [w["widget_id"] for w in summary.to_dict()["widgets"]]
    assert widget_ids == ["import_wizard", "status_summary"]

def test_dashboard_multi_user_isolation(test_vault):
    data = test_vault._read_index()
    
    # Patient A data
    doc_a = {"id": "doc-a", "patient_id": "patient-A", "status": "imported", "measured_at": "2026-08-16T10:00:00Z", "date_confidence": 1.0}
    meas_a = create_measurement(document_id="doc-a", metric="glucose", value=110.0, measured_at="2026-08-16T10:00:00Z").to_dict()
    
    # Patient B data
    doc_b = {"id": "doc-b", "patient_id": "patient-B", "status": "imported", "measured_at": "2026-08-16T10:00:00Z", "date_confidence": 1.0}
    meas_b = create_measurement(document_id="doc-b", metric="glucose", value=190.0, measured_at="2026-08-16T10:00:00Z").to_dict()
    
    data["documents"].extend([doc_a, doc_b])
    data["measurements"].extend([meas_a, meas_b])
    test_vault._write_index(data)

    # Run engines
    intel = HealthIntelligenceEngine(test_vault)
    intel.generate_observations("patient-A")
    intel.generate_observations("patient-B")

    service = DashboardService(test_vault)
    
    # Fetch Patient A dashboard
    summary_a = service.get_summary("patient-A")
    assert summary_a.patient_id == "patient-A"
    
    # Verify isolation: Patient A gets Patient A's observations widget payload
    widgets_a = {w["widget_id"]: w for w in summary_a.to_dict()["widgets"]}
    obs_widget_a = widgets_a["key_observations"]
    
    for obs in obs_widget_a["payload"]["observations"]:
        assert obs["patient_id"] == "patient-A"
        # Evidence linkage check for real measurements
        if obs.get("metric") is not None:
            assert len(obs["evidence"]) > 0
            assert obs["evidence"][0]["document_id"] == "doc-a"

    # Fetch Patient B dashboard
    summary_b = service.get_summary("patient-B")
    assert summary_b.patient_id == "patient-B"
    widgets_b = {w["widget_id"]: w for w in summary_b.to_dict()["widgets"]}
    obs_widget_b = widgets_b["key_observations"]
    
    for obs in obs_widget_b["payload"]["observations"]:
        assert obs["patient_id"] == "patient-B"
        if obs.get("metric") is not None:
            assert len(obs["evidence"]) > 0
            assert obs["evidence"][0]["document_id"] == "doc-b"


def test_dashboard_includes_health_connect_monitoring(test_vault):
    data = test_vault._read_index()
    data["observations"] = [
        {
            "patient_id": "patient-1",
            "metric_type": "heart_rate",
            "value": 72,
            "unit": "bpm",
            "measured_at": "2026-08-19T12:00:00Z",
            "ingested_at": "2026-08-19T12:01:00Z",
            "source": "health_connect_companion",
            "acquisition_mode": "LIVE",
        },
        {
            "patient_id": "patient-1",
            "metric_type": "steps",
            "value": 4200,
            "unit": "count",
            "measured_at": "2026-08-19T12:00:00Z",
            "ingested_at": "2026-08-19T12:01:00Z",
            "source": "health_connect_companion",
            "acquisition_mode": "LIVE",
        },
    ]
    test_vault._write_index(data)

    summary = DashboardService(test_vault).get_summary("patient-1")
    widgets = {w["widget_id"]: w for w in summary.to_dict()["widgets"]}
    obs_payload = widgets["key_observations"]["payload"]["observations"]
    assert any(o.get("metric") == "heart_rate" for o in obs_payload)
    trends = widgets["trends_widget"]["payload"]["trends"]
    assert trends["heart_rate"]["sample_count"] == 1
    assert widgets["status_summary"]["payload"]["health_connect_observation_count"] == 2
