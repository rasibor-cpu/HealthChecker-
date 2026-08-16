import pytest
import tempfile
import json
from pathlib import Path
from fastapi.testclient import TestClient
from backend.health_vault.api import create_health_vault_app
from backend.health_vault.models import create_measurement, RecordStatus, RecordCategory
from backend.health_vault.vault_store import VaultStore
from backend.health_vault.health_intelligence import HealthIntelligenceEngine
from backend.health_vault.trend_engine import TrendEngine

@pytest.fixture
def temp_vault_with_app():
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        store = VaultStore(root=tdp)
        app = create_health_vault_app(store, test_users={
            "patient-A": "correct", "patient-B": "correct"
        })
        client = TestClient(app)
        yield store, client

def test_records_upload_and_lifecycle(temp_vault_with_app):
    store, client = temp_vault_with_app

    # Authenticate Patient A
    login_a = client.post("/api/auth/login", json={"patient_id": "patient-A", "password": "correct"}).json()
    token_a = login_a["token"]

    # Upload through the authenticated HC-317B multipart batch handoff.
    clinical_payload = {
        "source": "synthetic_lab",
        "patient_ref": "patient-A",
        "measured_at": "2026-08-16T10:00:00Z",
        "extracted_measurements": [
            {"metric": "glucose", "value": 5.8, "units": "mmol/L", "flag": "normal"}
        ]
    }
    file_bytes = json.dumps(clinical_payload).encode("utf-8")

    upload_resp = client.post(
        "/api/records/upload",
        headers={"Authorization": f"Bearer {token_a}"},
        files={"file": ("lab_report.json", file_bytes, "application/json")},
    )
    assert upload_resp.status_code == 200
    res = upload_resp.json()
    assert res["ok"] is True
    doc_id = res["document_id"]

    # Retrieve record list for Patient A
    list_resp = client.get("/api/records", headers={"Authorization": f"Bearer {token_a}"})
    assert list_resp.status_code == 200
    records = list_resp.json()["records"]
    assert len(records) == 1
    rec = records[0]
    assert rec["document_id"] == doc_id
    assert rec["original_filename"] == "lab_report.json"
    assert rec["status"] == RecordStatus.REQUIRES_REVIEW.value
    assert rec["primary_category"] == RecordCategory.GLUCOSE.value

    detail = client.get(f"/api/records/{doc_id}", headers={"Authorization": f"Bearer {token_a}"}).json()
    assert detail["lifecycle"]
    assert all(event["source"] in {"vault_audit", "intake_import", "intake_import_log"} for event in detail["lifecycle"])

    # Download decrypted document
    download_resp = client.get(
        f"/api/records/download/{doc_id}",
        headers={"Authorization": f"Bearer {token_a}"}
    )
    assert download_resp.status_code == 200
    assert download_resp.content == file_bytes
    assert "attachment; filename=\"lab_report.json\"" in download_resp.headers["Content-Disposition"]

def test_records_multi_user_isolation(temp_vault_with_app):
    store, client = temp_vault_with_app

    # Setup Patient A and Patient B records
    data = store._read_index()
    doc_a = {"id": "doc-a", "patient_id": "patient-A", "status": "imported", "original_filename": "report_a.pdf"}
    doc_b = {"id": "doc-b", "patient_id": "patient-B", "status": "imported", "original_filename": "report_b.pdf"}
    data["documents"].extend([doc_a, doc_b])
    store._write_index(data)

    # Login Patient A
    login_a = client.post("/api/auth/login", json={"patient_id": "patient-A", "password": "correct"}).json()
    token_a = login_a["token"]

    # Patient A listing only shows Patient A's records
    list_a = client.get("/api/records", headers={"Authorization": f"Bearer {token_a}"}).json()["records"]
    assert len(list_a) == 1
    assert list_a[0]["document_id"] == "doc-a"

    # Patient A detail fetch for Patient B's record returns 404
    detail_b = client.get("/api/records/doc-b", headers={"Authorization": f"Bearer {token_a}"})
    assert detail_b.status_code == 404

    # Patient A download for Patient B's record returns 404
    download_b = client.get("/api/records/download/doc-b", headers={"Authorization": f"Bearer {token_a}"})
    assert download_b.status_code == 404

def test_provenance_linkage_to_clinical_systems(temp_vault_with_app):
    store, client = temp_vault_with_app

    # Login A
    login_a = client.post("/api/auth/login", json={"patient_id": "patient-A", "password": "correct"}).json()
    token_a = login_a["token"]

    # Write document with metrics, timeline, observations
    data = store._read_index()
    doc = {
        "id": "doc-trace",
        "patient_id": "patient-A",
        "status": "imported",
        "original_filename": "report_trace.json",
        "measured_at": "2026-08-16T10:00:00Z",
        "date_confidence": 1.0,
        "primary_category": "kidney_renal"
    }
    meas = create_measurement(
        document_id="doc-trace",
        metric="egfr",
        value=65.0,
        units="mL/min/1.73m2",
        measured_at="2026-08-16T10:00:00Z"
    ).to_dict()
    data["documents"].append(doc)
    data["measurements"].append(meas)
    store._write_index(data)

    # Re-calculate observations
    TrendEngine(store).recompute("patient-A")
    intel = HealthIntelligenceEngine(store)
    intel.generate_observations("patient-A")

    # Retrieve record details for doc-trace
    resp = client.get("/api/records/doc-trace", headers={"Authorization": f"Bearer {token_a}"})
    assert resp.status_code == 200
    rec = resp.json()

    # Verify linkages are fully populated
    # 1. Extracted metrics
    assert len(rec["extracted_measurements"]) == 1
    assert rec["extracted_measurements"][0]["metric"] == "egfr"

    # 2. Timeline events
    assert len(rec["timeline_events"]) == 1
    assert rec["timeline_events"][0]["entry_kind"] == "document"

    # 3. Observations
    assert len(rec["ai_observations"]) == 1
    assert rec["ai_observations"][0]["patient_id"] == "patient-A"
    assert rec["ai_observations"][0]["evidence"][0]["document_id"] == "doc-trace"

    # 4. Trends
    assert len(rec["trend_references"]) == 1
    assert rec["trend_references"][0]["trend"]["latest"] == 65.0
    assert rec["evidence_references"][0]["document_id"] == "doc-trace"

def test_records_dashboard_compatibility(temp_vault_with_app):
    store, client = temp_vault_with_app
    # Assert dashboard service remains functional alongside records service
    login = client.post("/api/auth/login", json={"patient_id": "patient-A", "password": "correct"}).json()
    token = login["token"]

    resp = client.get("/api/dashboard/summary", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    summary = resp.json()
    assert summary["patient_id"] == "patient-A"
    assert "widgets" in summary


def test_records_authentication_and_forged_tokens(temp_vault_with_app):
    _, client = temp_vault_with_app
    assert client.get("/api/records").status_code == 401
    assert client.get("/api/records", headers={"Authorization": "Bearer token-patient-A"}).status_code == 401
    assert client.post(
        "/api/records/upload",
        headers={"Authorization": "Bearer forged"},
        files={"file": ("x.json", b"{}", "application/json")},
    ).status_code == 401


def test_listing_filters_and_upload_identity_binding(temp_vault_with_app):
    store, client = temp_vault_with_app
    token = client.post("/api/auth/login", json={"patient_id": "patient-A", "password": "correct"}).json()["token"]
    data = store._read_index()
    data["documents"].extend([
        {"id": "lab-a", "patient_id": "patient-A", "status": "parsed", "primary_category": "laboratory_report", "original_filename": "lab.pdf"},
        {"id": "sleep-a", "patient_id": "patient-A", "status": "partial", "primary_category": "sleep", "requires_review": True, "original_filename": "sleep.pdf"},
    ])
    store._write_index(data)
    headers = {"Authorization": f"Bearer {token}"}
    labs = client.get("/api/records?category=laboratory_report&status=imported", headers=headers).json()["records"]
    assert [row["document_id"] for row in labs] == ["lab-a"]

    payload = json.dumps({"patient_id": "patient-B", "measured_at": "2026-08-16T10:00:00Z"}).encode()
    uploaded = client.post(
        "/api/records/upload",
        headers=headers,
        files={"file": ("../identity.json", payload, "application/json")},
    )
    assert uploaded.status_code == 200
    doc_id = uploaded.json()["document_id"]
    stored = next(doc for doc in store.list_documents() if doc["id"] == doc_id)
    assert stored["patient_id"] == "patient-A"
    assert stored["original_filename"] == "identity.json"


def test_gmail_provenance_and_linkage_do_not_leak(temp_vault_with_app):
    store, client = temp_vault_with_app
    data = store._read_index()
    data["documents"].extend([
        {
            "id": "gmail-a", "patient_id": "patient-A", "status": "parsed",
            "original_filename": "gmail.pdf", "source_system": "gmail",
            "acquisition_method": "automatic_intake", "provenance": "gmail",
            "tags": ["gmail_message_id:msg-a", "gmail_attachment_id:att-a"],
        },
        {"id": "private-b", "patient_id": "patient-B", "status": "parsed", "original_filename": "private.pdf"},
    ])
    data["observations"].extend([
        {"observation_id": "obs-a", "patient_id": "patient-A", "evidence": [{"document_id": "gmail-a"}]},
        {"observation_id": "obs-b", "patient_id": "patient-B", "evidence": [{"document_id": "gmail-a"}, {"document_id": "private-b"}]},
    ])
    store._write_index(data)
    token = client.post("/api/auth/login", json={"patient_id": "patient-A", "password": "correct"}).json()["token"]
    detail = client.get("/api/records/gmail-a", headers={"Authorization": f"Bearer {token}"}).json()
    assert detail["source_provenance"]["gmail"] == {"source": "gmail", "message_id": "msg-a", "attachment_id": "att-a"}
    assert [item["observation_id"] for item in detail["ai_observations"]] == ["obs-a"]
    assert client.get("/api/records/private-b", headers={"Authorization": f"Bearer {token}"}).status_code == 404
