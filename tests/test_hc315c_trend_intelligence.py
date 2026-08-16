import pytest
import os
import tempfile
import json
from pathlib import Path
from backend.health_vault.models import MedicalDocument, create_measurement
from backend.health_vault.vault_store import VaultStore
from backend.health_vault.health_intelligence import HealthIntelligenceEngine
from backend.health_vault.trend_engine import TrendEngine

@pytest.fixture
def test_vault():
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        store = VaultStore(root=tdp)
        yield store

def test_glucose_variability_calculation(test_vault):
    # Add a document for patient-1
    data = test_vault._read_index()
    doc1 = {
        "id": "doc-1",
        "patient_id": "patient-1",
        "document_type": "libre_cgm_report",
        "source_system": "libre",
        "acquisition_method": "manual_upload",
        "status": "imported",
        "measured_at": "2026-08-16T10:00:00Z",
    }
    
    # 5 glucose measurements to test variability calculation
    meas_list = [
        create_measurement(document_id="doc-1", metric="glucose", value=80, measured_at="2026-08-16T10:00:00Z").to_dict(),
        create_measurement(document_id="doc-1", metric="glucose", value=120, measured_at="2026-08-16T10:05:00Z").to_dict(),
        create_measurement(document_id="doc-1", metric="glucose", value=90, measured_at="2026-08-16T10:10:00Z").to_dict(),
        create_measurement(document_id="doc-1", metric="glucose", value=140, measured_at="2026-08-16T10:15:00Z").to_dict(),
        create_measurement(document_id="doc-1", metric="glucose", value=85, measured_at="2026-08-16T10:20:00Z").to_dict(),
    ]
    
    data["documents"].append(doc1)
    data["measurements"].extend(meas_list)
    test_vault._write_index(data)

    engine = HealthIntelligenceEngine(test_vault)
    obs = engine.generate_observations("patient-1")
    
    assert len(obs) == 1
    glucose_obs = obs[0]
    assert glucose_obs["category"] == "glycemic"
    assert glucose_obs["metric"] == "glucose"
    assert "mean" in glucose_obs["fact"].lower()
    assert "standard deviation" in glucose_obs["fact"].lower()
    
    # Standard deviation of [80, 120, 90, 140, 85] is ~23.9 (> 20), so it should warn about high variability
    assert "high variability" in glucose_obs["interpretation"]
    assert "statistical_analysis" in glucose_obs["confidence"]["method"]
    assert glucose_obs["explanation"] is not None

def test_egfr_renal_trend_calculation(test_vault):
    data = test_vault._read_index()
    doc1 = {
        "id": "doc-egfr-1",
        "patient_id": "patient-1",
        "document_type": "laboratory_pdf",
        "source_system": "lifelabs",
        "measured_at": "2026-08-14T10:00:00Z",
        "status": "imported",
        "date_confidence": 1.0,
    }
    doc2 = {
        "id": "doc-egfr-2",
        "patient_id": "patient-1",
        "document_type": "laboratory_pdf",
        "source_system": "lifelabs",
        "measured_at": "2026-08-15T10:00:00Z",
        "status": "imported",
        "date_confidence": 1.0,
    }
    doc3 = {
        "id": "doc-egfr-3",
        "patient_id": "patient-1",
        "document_type": "laboratory_pdf",
        "source_system": "lifelabs",
        "measured_at": "2026-08-16T10:00:00Z",
        "status": "imported",
        "date_confidence": 1.0,
    }

    # Worsening trend: 95 -> 88 -> 80
    meas = [
        create_measurement(document_id="doc-egfr-1", metric="egfr", value=95.0, measured_at="2026-08-14T10:00:00Z").to_dict(),
        create_measurement(document_id="doc-egfr-2", metric="egfr", value=88.0, measured_at="2026-08-15T10:00:00Z").to_dict(),
        create_measurement(document_id="doc-egfr-3", metric="egfr", value=80.0, measured_at="2026-08-16T10:00:00Z").to_dict(),
    ]
    
    data["documents"].extend([doc1, doc2, doc3])
    data["measurements"].extend(meas)
    test_vault._write_index(data)

    engine = HealthIntelligenceEngine(test_vault)
    obs = engine.generate_observations("patient-1")
    
    egfr_obs = [o for o in obs if o["metric"] == "egfr"][0]
    assert egfr_obs["category"] == "renal"
    assert "worsening" in egfr_obs["interpretation"]
    assert len(egfr_obs["evidence"]) == 3
    assert egfr_obs["explanation"] is not None

def test_blood_pressure_hypertensive_classification(test_vault):
    data = test_vault._read_index()
    doc1 = {
        "id": "doc-bp",
        "patient_id": "patient-1",
        "document_type": "blood_pressure_screenshot",
        "measured_at": "2026-08-16T10:00:00Z",
        "status": "imported",
    }
    
    meas = [
        create_measurement(document_id="doc-bp", metric="systolic", value=135.0, measured_at="2026-08-16T10:00:00Z").to_dict(),
        create_measurement(document_id="doc-bp", metric="diastolic", value=85.0, measured_at="2026-08-16T10:00:00Z").to_dict(),
    ]
    
    data["documents"].append(doc1)
    data["measurements"].extend(meas)
    test_vault._write_index(data)

    engine = HealthIntelligenceEngine(test_vault)
    obs = engine.generate_observations("patient-1")
    
    bp_obs = [o for o in obs if o["metric"] == "systolic"][0]
    assert bp_obs["category"] == "cardiovascular"
    assert "elevated" in bp_obs["interpretation"]
    assert "135/85" in bp_obs["fact"]

def test_evidence_linkage_and_user_isolation(test_vault):
    data = test_vault._read_index()
    
    # Patient A data
    doc_a = {"id": "doc-a", "patient_id": "patient-A", "status": "imported"}
    meas_a = create_measurement(document_id="doc-a", metric="weight", value=70.0, measured_at="2026-08-16T10:00:00Z").to_dict()
    
    # Patient B data
    doc_b = {"id": "doc-b", "patient_id": "patient-B", "status": "imported"}
    meas_b = create_measurement(document_id="doc-b", metric="weight", value=90.0, measured_at="2026-08-16T10:00:00Z").to_dict()
    
    data["documents"].extend([doc_a, doc_b])
    data["measurements"].extend([meas_a, meas_b])
    test_vault._write_index(data)

    engine = HealthIntelligenceEngine(test_vault)
    
    # Generate for Patient A
    obs_a = engine.generate_observations("patient-A")
    assert len(obs_a) == 1
    assert obs_a[0]["patient_id"] == "patient-A"
    assert len(obs_a[0]["evidence"]) == 1
    assert obs_a[0]["evidence"][0]["document_id"] == "doc-a"
    
    # Generate for Patient B
    obs_b = engine.generate_observations("patient-B")
    assert len(obs_b) == 1
    assert obs_b[0]["patient_id"] == "patient-B"
    assert len(obs_b[0]["evidence"]) == 1
    assert obs_b[0]["evidence"][0]["document_id"] == "doc-b"
