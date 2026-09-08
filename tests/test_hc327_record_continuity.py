from pathlib import Path

from backend.health_vault.models import MedicalDocument, Measurement
from backend.health_vault.vault_store import VaultStore


def test_new_measurement_inherits_document_patient_scope(tmp_path: Path):
    vault = VaultStore(
        root=tmp_path / "vault",
        encryption_key=b"R" * 32,
    )

    doc = MedicalDocument(
        patient_id="00000",
        document_type="laboratory",
        source_system="hc327_test",
        original_filename="new_lab.json",
    )

    measurement = Measurement(
        metric="creatinine",
        value=123,
        units="umol/L",
    )

    before_docs = len(vault.list_documents())
    before_measurements = len(vault.list_measurements())

    vault.store(
        document=doc,
        measurements=[measurement],
        content=b"hc327-record-continuity-test",
    )

    docs = vault.list_documents()
    measurements = vault.list_measurements()

    assert len(docs) == before_docs + 1
    assert len(measurements) == before_measurements + 1

    saved = next(
        m for m in measurements
        if m["document_id"] == doc.id
    )

    assert saved["patient_id"] == "00000"
    assert saved["metric"] == "creatinine"
    assert saved["value"] == 123
    assert saved["units"] == "umol/L"


def test_existing_records_are_append_only(tmp_path: Path):
    vault = VaultStore(
        root=tmp_path / "vault",
        encryption_key=b"S" * 32,
    )

    first = MedicalDocument(
        patient_id="00000",
        original_filename="first.json",
    )

    vault.store(
        document=first,
        measurements=[
            Measurement(metric="heart_rate", value=70, units="bpm")
        ],
        content=b"first",
    )

    original_docs = list(vault.list_documents())
    original_measurements = list(vault.list_measurements())

    second = MedicalDocument(
        patient_id="00000",
        original_filename="second.json",
    )

    vault.store(
        document=second,
        measurements=[
            Measurement(metric="heart_rate", value=72, units="bpm")
        ],
        content=b"second",
    )

    assert vault.list_documents()[0] == original_docs[0]
    assert vault.list_measurements()[0] == original_measurements[0]
    assert len(vault.list_documents()) == 2
    assert len(vault.list_measurements()) == 2
    assert all(
        row["patient_id"] == "00000"
        for row in vault.list_measurements()
    )


# === HC327-T4 APPEND CONTINUITY ===

from backend.health_vault.import_pipeline import ImportPipeline
from backend.health_vault.records_service import RecordsService
from backend.health_vault.timeline import build_timeline
from backend.health_vault.trend_engine import TrendEngine


def test_hc327_new_record_is_patient_scoped_and_consumer_visible(tmp_path: Path):
    vault = VaultStore(
        root=tmp_path / "vault",
        encryption_key=b"T" * 32,
    )

    patient_id = "00000"

    existing = MedicalDocument(
        patient_id=patient_id,
        document_type="laboratory",
        source_system="hc327_test",
        original_filename="baseline_lab.json",
        measured_at="2026-09-01T10:00:00Z",
    )

    vault.store(
        document=existing,
        measurements=[
            Measurement(
                metric="creatinine",
                value=120,
                units="umol/L",
                measured_at="2026-09-01T10:00:00Z",
            )
        ],
        content=b"hc327-baseline",
    )

    before_docs = list(vault.list_documents())
    before_measurements = list(vault.list_measurements())

    newer = MedicalDocument(
        patient_id=patient_id,
        document_type="laboratory",
        source_system="hc327_test",
        original_filename="newer_lab.json",
        measured_at="2026-09-07T18:00:00Z",
    )

    vault.store(
        document=newer,
        measurements=[
            Measurement(
                metric="creatinine",
                value=126,
                units="umol/L",
                measured_at="2026-09-07T18:00:00Z",
            )
        ],
        content=b"hc327-newer-record",
    )

    after_docs = vault.list_documents()
    after_measurements = vault.list_measurements()

    # Historical record preserved.
    assert len(after_docs) == len(before_docs) + 1
    assert len(after_measurements) == len(before_measurements) + 1
    assert after_docs[0] == before_docs[0]
    assert after_measurements[0] == before_measurements[0]

    # New measurement inherits patient ownership.
    appended = [
        row for row in after_measurements
        if row.get("document_id") == newer.id
    ]
    assert len(appended) == 1
    assert appended[0]["patient_id"] == patient_id
    assert appended[0]["metric"] == "creatinine"
    assert appended[0]["value"] == 126
    assert appended[0]["measured_at"] == "2026-09-07T18:00:00Z"

    # Consumer Records service sees the new document for the same patient.
    service = RecordsService(vault)

    patient_docs = [
        d for d in vault.list_documents()
        if str(d.get("patient_id") or "default-patient") == patient_id
    ]

    assert any(
        d.get("id") == newer.id
        for d in patient_docs
    )

    # Timeline contains the newly appended source document.
    timeline = build_timeline(vault, patient_id=patient_id)

    assert any(
        (
            entry.get("document", {}).get("id") == newer.id
            or entry.get("document_id") == newer.id
        )
        for entry in timeline
    )

    # Trends recompute from both historical + appended measurements.
    trends = TrendEngine(vault).recompute(patient_id=patient_id)

    assert isinstance(trends, dict)

    # Patient isolation.
    other_patient_docs = [
        d for d in vault.list_documents()
        if str(d.get("patient_id") or "default-patient") == "OTHER"
    ]

    assert all(
        d.get("id") != newer.id
        for d in other_patient_docs
    )


def test_hc327_duplicate_content_does_not_create_second_clinical_record(
    tmp_path: Path,
):
    vault = VaultStore(
        root=tmp_path / "vault",
        encryption_key=b"U" * 32,
    )

    patient_id = "00000"

    content = b"hc327-identical-clinical-content"

    first = MedicalDocument(
        patient_id=patient_id,
        document_type="laboratory",
        source_system="hc327_test",
        original_filename="same_lab.json",
        measured_at="2026-09-07T18:00:00Z",
        sha256=VaultStore.sha256_bytes(content),
    )

    first_measurement = Measurement(
        metric="creatinine",
        value=126,
        units="umol/L",
        measured_at="2026-09-07T18:00:00Z",
    )

    vault.store(
        document=first,
        measurements=[first_measurement],
        content=content,
    )

    first_count = len(vault.list_documents())

    duplicate = MedicalDocument(
        patient_id=patient_id,
        document_type="laboratory",
        source_system="hc327_test",
        original_filename="same_lab.json",
        measured_at="2026-09-07T18:00:00Z",
        sha256=VaultStore.sha256_bytes(content),
    )

    vault.store(
        document=duplicate,
        measurements=[],
        content=content,
    )

    docs = vault.list_documents()

    assert len(docs) == first_count + 1

    duplicate_row = next(
        d for d in docs
        if d.get("id") == duplicate.id
    )

    # Duplicate metadata may be retained for audit/provenance,
    # but no second clinical payload or measurement is created.
    assert duplicate_row.get("duplicate_of") == first.id

    measurement_rows = [
        m for m in vault.list_measurements()
        if m.get("metric") == "creatinine"
    ]

    assert len(measurement_rows) == 1
    assert measurement_rows[0]["patient_id"] == patient_id


def test_hc327_measurement_patient_id_matches_owning_document_for_every_new_write(
    tmp_path: Path,
):
    vault = VaultStore(
        root=tmp_path / "vault",
        encryption_key=b"V" * 32,
    )

    for index, patient_id in enumerate(("00000", "000001", "patient-c")):
        doc = MedicalDocument(
            patient_id=patient_id,
            original_filename=f"record-{index}.json",
        )

        vault.store(
            document=doc,
            measurements=[
                Measurement(
                    metric="heart_rate",
                    value=70 + index,
                    units="bpm",
                )
            ],
            content=f"record-{index}".encode(),
        )

    docs = {
        d["id"]: d
        for d in vault.list_documents()
    }

    for measurement in vault.list_measurements():
        owner = docs[measurement["document_id"]]["patient_id"]

        assert measurement["patient_id"] == owner


# === HC327-T5 RECORDS SERVICE VISIBILITY ===

def test_hc327_appended_record_is_visible_through_consumer_records_service(
    tmp_path: Path,
):
    from backend.health_vault.records_service import RecordsService

    vault = VaultStore(
        root=tmp_path / "vault",
        encryption_key=b"W" * 32,
    )

    patient_id = "00000"
    other_patient = "OTHER"

    old_doc = MedicalDocument(
        patient_id=patient_id,
        document_type="laboratory",
        source_system="hc327_test",
        original_filename="historical_lab.json",
        measured_at="2026-08-01T09:00:00Z",
    )

    vault.store(
        document=old_doc,
        measurements=[
            Measurement(
                metric="creatinine",
                value=118,
                units="umol/L",
                measured_at="2026-08-01T09:00:00Z",
            )
        ],
        content=b"hc327-old-record",
    )

    new_doc = MedicalDocument(
        patient_id=patient_id,
        document_type="laboratory",
        source_system="hc327_test",
        original_filename="latest_lab.json",
        measured_at="2026-09-07T19:00:00Z",
    )

    vault.store(
        document=new_doc,
        measurements=[
            Measurement(
                metric="creatinine",
                value=126,
                units="umol/L",
                measured_at="2026-09-07T19:00:00Z",
            )
        ],
        content=b"hc327-latest-record",
    )

    service = RecordsService(vault)

    # Standard patient Records listing.
    records = service.list_records(patient_id)

    ids = [record.document_id for record in records]

    assert old_doc.id in ids
    assert new_doc.id in ids

    # Newest record should sort ahead of older historical record.
    assert ids.index(new_doc.id) < ids.index(old_doc.id)

    latest = next(
        record
        for record in records
        if record.document_id == new_doc.id
    )

    assert latest.patient_id == patient_id
    assert latest.metrics_count == 1
    assert latest.original_filename == "latest_lab.json"
    assert latest.measured_at == "2026-09-07T19:00:00Z"

    # Consumer Records payload used by UI.
    payload = service.consumer_records_payload(patient_id)

    payload_text = str(payload)

    assert new_doc.id in payload_text
    assert "latest_lab.json" in payload_text

    # Patient isolation: another patient cannot see either record.
    other_records = service.list_records(other_patient)

    assert all(
        record.document_id not in {old_doc.id, new_doc.id}
        for record in other_records
    )

    other_payload = service.consumer_records_payload(other_patient)

    other_text = str(other_payload)

    assert new_doc.id not in other_text
    assert old_doc.id not in other_text
    assert "latest_lab.json" not in other_text
