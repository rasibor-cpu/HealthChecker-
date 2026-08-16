import pytest
import tempfile
from pathlib import Path
from backend.health_vault.models import (
    ConfidenceScore,
    EvidenceReference,
    HealthObservation,
)
from backend.health_vault.vault_store import VaultStore
from backend.health_vault.health_intelligence import (
    HealthIntelligenceEngine,
    _validate_safety_boundaries,
)

@pytest.fixture
def test_vault():
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        store = VaultStore(root=tdp)
        yield store

def test_safety_boundary_assertion():
    # Test that forbidden diagnoses trigger ValueError
    score = ConfidenceScore(1.0, "rule_based", "1.0")
    
    bad_obs_1 = HealthObservation(
        patient_id="p-1",
        observation_id="o-1",
        category="glycemic",
        metric="glucose",
        fact="The user has diabetes.",  # Forbidden term
        interpretation="Elevated",
        measured_at="2026-08-16T10:00:00Z",
        confidence=score,
    )
    with pytest.raises(ValueError) as exc:
        _validate_safety_boundaries(bad_obs_1)
    assert "Safety Boundary Violation" in str(exc.value)
    assert "diabetes" in str(exc.value)

    # Test that medication recommendation triggers ValueError
    bad_obs_2 = HealthObservation(
        patient_id="p-1",
        observation_id="o-2",
        category="glycemic",
        metric="glucose",
        fact="Elevated glucose levels.",
        interpretation="Suggest starting Metformin.",  # Forbidden medication
        measured_at="2026-08-16T10:00:00Z",
        confidence=score,
    )
    with pytest.raises(ValueError) as exc:
        _validate_safety_boundaries(bad_obs_2)
    assert "Safety Boundary Violation" in str(exc.value)
    assert "metformin" in str(exc.value)

def test_missing_data_warnings(test_vault):
    # Patient A has no documents or measurements
    engine = HealthIntelligenceEngine(test_vault)
    obs = engine.generate_observations("patient-empty")
    
    # Must generate 3 warnings: glycemic, renal, and cardiovascular
    assert len(obs) == 3
    
    glycemic_warn = [o for o in obs if o["category"] == "glycemic"][0]
    assert glycemic_warn["interpretation"] == "Missing data warning"
    assert len(glycemic_warn["evidence"]) == 0
    assert glycemic_warn["confidence"]["value"] == 0.0
    assert "No glucose or HbA1c" in glycemic_warn["explanation"]
    
    renal_warn = [o for o in obs if o["category"] == "renal"][0]
    assert renal_warn["interpretation"] == "Missing data warning"
    assert len(renal_warn["evidence"]) == 0
    
    cv_warn = [o for o in obs if o["category"] == "cardiovascular"][0]
    assert cv_warn["interpretation"] == "Missing data warning"
    assert len(cv_warn["evidence"]) == 0
