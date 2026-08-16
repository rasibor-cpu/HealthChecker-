import pytest
import os
import tempfile
import json
from pathlib import Path
from backend.health_vault.models import (
    EvidenceReference,
    ConfidenceScore,
    HealthMetric,
    HealthEvent,
    HealthObservation,
)
from backend.health_vault.vault_store import VaultStore
from backend.health_vault.health_intelligence import HealthIntelligenceEngine
from backend.health_vault.trend_engine import TrendEngine

@pytest.fixture
def temp_vault():
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        store = VaultStore(root=tdp)
        yield store

def test_evidence_traceability():
    # Verify EvidenceReference structures
    ev = EvidenceReference(
        source_type="measurement",
        document_id="doc-1",
        measurement_id="meas-1",
        sha256="fake-sha256"
    )
    assert ev.source_type == "measurement"
    assert ev.document_id == "doc-1"
    assert ev.measurement_id == "meas-1"
    assert ev.sha256 == "fake-sha256"

    # Verify to_dict serialization removes None
    ev_dict = ev.to_dict()
    assert ev_dict["source_type"] == "measurement"
    assert ev_dict["document_id"] == "doc-1"
    
    ev_none = EvidenceReference(source_type="wearable_sync")
    assert "document_id" not in ev_none.to_dict()

    # Evidence references must be present in HealthObservation
    with pytest.raises(TypeError):
        # Must pass arguments
        HealthObservation()

def test_confidence_handling():
    # Valid confidence value
    score = ConfidenceScore(value=0.85, method="rule_based", version="1.0.0")
    assert score.value == 0.85
    assert score.method == "rule_based"
    assert score.version == "1.0.0"

    # Verify serialization
    score_dict = score.to_dict()
    assert score_dict["value"] == 0.85
    assert score_dict["method"] == "rule_based"

def test_unsupported_inference_prevention():
    score = ConfidenceScore(value=0.9, method="rule_based", version="1.0.0")
    ev = EvidenceReference(source_type="measurement", measurement_id="m-1")
    
    obs = HealthObservation(
        patient_id="patient-1",
        observation_id="obs-1",
        category="renal",
        metric="egfr",
        fact="eGFR is 84 mL/min/1.73m2",
        interpretation="Decreased filtration rate",
        measured_at="2026-08-16T19:00:00Z",
        confidence=score,
        evidence=[ev]
    )

    # Observation must enforce safety boundary disclaimer
    assert "Consult a doctor" in obs.safety_boundary_disclaimer
    assert "not a medical diagnosis" in obs.safety_boundary_disclaimer

    obs_dict = obs.to_dict()
    assert "safety_boundary_disclaimer" in obs_dict
    assert obs_dict["safety_boundary_disclaimer"] == obs.safety_boundary_disclaimer

def test_user_isolation(temp_vault):
    # Setup data for two different patients
    # We will write observations and verify isolation filters on query
    data = temp_vault._read_index()
    
    score = {"value": 0.9, "method": "rule_based", "version": "1.0.0"}
    ev = [{"source_type": "measurement", "measurement_id": "m-1"}]
    
    data["health_intelligence"]["observations"] = [
        {
            "patient_id": "patient-A",
            "observation_id": "obs-A",
            "category": "renal",
            "metric": "egfr",
            "fact": "eGFR decreased",
            "interpretation": "worsening",
            "measured_at": "2026-08-16T19:00:00Z",
            "confidence": score,
            "evidence": ev,
            "safety_boundary_disclaimer": "Observational only"
        },
        {
            "patient_id": "patient-B",
            "observation_id": "obs-B",
            "category": "glycemic",
            "metric": "glucose",
            "fact": "Glucose stable",
            "interpretation": "normal",
            "measured_at": "2026-08-16T19:00:00Z",
            "confidence": score,
            "evidence": ev,
            "safety_boundary_disclaimer": "Observational only"
        }
    ]
    temp_vault._write_index(data)

    # Instantiate HealthIntelligenceEngine and verify patient-specific query filter
    engine = HealthIntelligenceEngine(temp_vault)
    obs = engine.get_patient_observations("patient-A")
    assert len(obs) == 1
    assert obs[0]["patient_id"] == "patient-A"
    assert obs[0]["observation_id"] == "obs-A"

    obs_b = engine.get_patient_observations("patient-B")
    assert len(obs_b) == 1
    assert obs_b[0]["patient_id"] == "patient-B"
    assert obs_b[0]["observation_id"] == "obs-B"

    # Patient C has no observations
    assert len(engine.get_patient_observations("patient-C")) == 0
