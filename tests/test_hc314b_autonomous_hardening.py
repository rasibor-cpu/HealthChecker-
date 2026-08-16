import pytest
import os
import tempfile
import json
from pathlib import Path
from backend.health_vault.acquisition.watcher import AcquisitionWatcher
from backend.health_vault.acquisition.gmail_config import GmailAcquisitionConfig
from backend.health_vault.acquisition.acquisition_state import AcquisitionStateStore
from backend.health_vault.monitoring.scheduler import MonitoringScheduler

@pytest.fixture
def temp_workspace():
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        state_path = tdp / "state.json"
        cfg = GmailAcquisitionConfig(
            acquisition_state_path=state_path,
            intake_incoming_dir=tdp / "incoming"
        )
        yield tdp, state_path, cfg

def test_hc314b_telemetry_aggregation(temp_workspace):
    _, state_path, cfg = temp_workspace
    store = AcquisitionStateStore(state_path)
    
    summary = {
        "accept_count": 2,
        "review_count": 1,
        "reject_count": 0,
        "already_acquired_count": 5,
        "error": None
    }
    store.update_telemetry(summary)
    
    with open(state_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    t = data["telemetry"]
    assert t["total_accept_count"] == 2
    assert t["total_review_count"] == 1
    assert t["total_already_acquired_count"] == 5
    assert t.get("total_failure_count", 0) == 0

    # Test error accumulation
    store.update_telemetry({"error": "GoogleAuthError"})
    with open(state_path, "r", encoding="utf-8") as f:
        t = json.load(f)["telemetry"]
    assert t["total_failure_count"] == 1

def test_hc314b_lock_observability(temp_workspace):
    _, state_path, cfg = temp_workspace
    store = AcquisitionStateStore(state_path)
    scheduler = MonitoringScheduler(store=store, config=None, patient_id="test")
    
    res = scheduler.run_due(lambda: {"ok": True, "gmail_auth_success": True})
    
    with open(state_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    state = data["scheduler"]
    scheduler._state["running"] = True
    scheduler._state["pid"] = 1234
    scheduler._state["machine_name"] = "test-node"
    scheduler._persist()
    
    with open(state_path, "r", encoding="utf-8") as f:
        s = json.load(f)["scheduler"]
    assert s["pid"] == 1234
    assert s["machine_name"] == "test-node"

def test_hc314b_heartbeat_flags(temp_workspace):
    _, state_path, cfg = temp_workspace
    store = AcquisitionStateStore(state_path)
    scheduler = MonitoringScheduler(store=store, config=None, patient_id="test")
    
    # Test handoff_success sets timestamp
    scheduler.run_due(lambda: {"ok": True, "handoff_success": True})
    state = store.get_monitoring_scheduler_state("test")
    assert "last_hc312_handoff_success_at" in state
    assert "last_gmail_auth_success_at" not in state
    
    # Test gmail_auth_success sets timestamp
    scheduler.run_due(lambda: {"ok": True, "gmail_auth_success": True})
    state = store.get_monitoring_scheduler_state("test")
    assert "last_gmail_auth_success_at" in state
