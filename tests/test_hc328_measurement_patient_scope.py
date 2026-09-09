from pathlib import Path

from backend.health_vault.models import create_measurement
from backend.health_vault.monitoring.ingestion import IngestionCoordinator
from backend.health_vault.vault_store import VaultStore


def test_create_measurement_preserves_explicit_patient_id():
    measurement = create_measurement(
        metric="heart_rate",
        value=72,
        units="bpm",
        measured_at="2026-09-08T00:00:00Z",
        document_id="hc328-doc",
        patient_id="hc328-patient",
    )

    assert measurement.patient_id == "hc328-patient"
    assert measurement.to_dict()["patient_id"] == "hc328-patient"


def test_health_connect_batched_ingestion_preserves_patient_scope(tmp_path: Path):
    store = VaultStore(root=tmp_path / "vault")
    coordinator = IngestionCoordinator(store=store)

    patient_id = "hc328-patient"

    payload = {
        "metric_type": "heart_rate",
        "value": 72,
        "unit": "bpm",
        "measured_at": "2026-09-08T00:00:00Z",
        "acquisition_mode": "DELAYED",
        "source": "health_connect_companion",
        "source_record_id": "hc328-patient-scope-1",
        "provenance": "health_connect_sync",
    }

    result = coordinator.ingest_observations(
        [payload],
        connector_id="health_connect",
        patient_id=patient_id,
        evaluate_freshness=False,
        batch_persist=True,
    )

    assert result["durable_success"] is True
    assert int(result["stored"]) == 1
    assert int(result["skipped"]) == 0

    observations = store.list_observations()
    documents = store.list_documents()
    measurements = store.list_measurements()

    assert len(observations) == 1
    assert len(documents) == 1
    assert len(measurements) == 1

    assert observations[0]["patient_id"] == patient_id
    assert documents[0]["patient_id"] == patient_id
    assert measurements[0]["patient_id"] == patient_id

    assert observations[0]["document_id"] == documents[0]["id"]
    assert measurements[0]["document_id"] == documents[0]["id"]

    assert observations[0]["metric_type"] == "heart_rate"
    assert measurements[0]["metric"] == "heart_rate"
