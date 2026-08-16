"""HC-317C consumer records UI, contract, and security acceptance."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.health_vault.api import create_health_vault_app
from backend.health_vault.models import create_measurement
from backend.health_vault.vault_store import VaultStore


@pytest.fixture
def records_app():
    with tempfile.TemporaryDirectory() as td:
        store = VaultStore(root=Path(td), encryption_key=b"R" * 32)
        client = TestClient(create_health_vault_app(store))
        yield store, client


def login(client: TestClient, patient_id: str) -> str:
    response = client.post(
        "/api/auth/login",
        json={"patient_id": patient_id, "password": "correct"},
    )
    assert response.status_code == 200
    return response.json()["token"]


def test_records_ui_assets_and_navigation_contract():
    html = Path("index.html").read_text(encoding="utf-8")
    js = Path("js/health_vault/records.js").read_text(encoding="utf-8")
    dashboard = Path("js/health_vault/dashboard.js").read_text(encoding="utf-8")
    css = Path("style.css").read_text(encoding="utf-8")
    service_worker = Path("service-worker.js").read_text(encoding="utf-8")

    assert 'href="style.css"' in html
    assert 'data="health_records_screen"' in html
    assert 'id="health_records_screen"' in html
    assert 'id="records_upload_panel"' in html
    assert 'id="records_list"' in html
    assert 'id="record_detail_dialog"' in html
    assert 'src="js/health_vault/records.js"' in html
    assert "HCRecordsUI.refreshRecords()" in html

    assert "class RecordsUI" in js
    assert '"/api/records/upload"' in js
    assert "`/api/records/${encodeURIComponent(documentId)}`" in js
    assert "`/api/records/download/${encodeURIComponent(documentId)}`" in js
    assert "getAuthorizationHeaders" in dashboard
    assert "data-open-health-records" in dashboard
    assert ".records-detail-dialog" in css
    assert "body.light-theme .records-detail-dialog" in css
    assert '"./js/health_vault/dashboard.js"' in service_worker
    assert '"./js/health_vault/records.js"' in service_worker


def test_records_ui_does_not_create_a_second_storage_or_vault_boundary():
    js = Path("js/health_vault/records.js").read_text(encoding="utf-8")
    assert "VaultStore" not in js
    assert "HCVaultUI" not in js
    assert "localStorage" not in js
    assert "encryption_key" not in js
    assert 'form.append("patient_id"' not in js
    assert 'form.append("file"' in js
    assert "Authorization" in js
    assert "URL.revokeObjectURL" in js
    assert "escape(value)" in js


def test_authenticated_upload_list_detail_and_encrypted_download(records_app):
    store, client = records_app
    token = login(client, "patient-A")
    headers = {"Authorization": f"Bearer {token}"}
    payload = json.dumps(
        {
            "measured_at": "2026-08-16T10:00:00Z",
            "extracted_measurements": [
                {"metric": "glucose", "value": 5.8, "units": "mmol/L"}
            ],
        }
    ).encode("utf-8")

    upload = client.post(
        "/api/records/upload",
        headers=headers,
        files={"file": ("consumer-record.json", payload, "application/json")},
    )
    assert upload.status_code == 200
    document_id = upload.json()["document_id"]
    assert document_id

    listing = client.get("/api/records", headers=headers)
    assert listing.status_code == 200
    summary = listing.json()["records"][0]
    assert summary["document_id"] == document_id
    assert summary["original_filename"] == "consumer-record.json"
    assert summary["metrics_count"] == 1

    detail = client.get(f"/api/records/{document_id}", headers=headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body["extracted_measurements"][0]["metric"] == "glucose"
    assert body["timeline_events"]
    assert body["trend_references"]
    assert body["ai_observations"]
    assert body["evidence_references"]
    assert body["lifecycle"]

    download = client.get(f"/api/records/download/{document_id}", headers=headers)
    assert download.status_code == 200
    assert download.content == payload
    stored = store.documents_dir / f"{document_id}.bin"
    assert stored.read_bytes() != payload


def test_records_ui_authentication_required_and_forged_tokens_rejected(records_app):
    _, client = records_app
    for path in ("/api/records", "/api/records/missing", "/api/records/download/missing"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer forged"}).status_code == 401
    assert client.post(
        "/api/records/upload",
        files={"file": ("private.json", b"{}", "application/json")},
    ).status_code == 401


def test_records_ui_patient_isolation_and_no_cross_user_phi(records_app):
    store, client = records_app
    data = store._read_index()
    data["documents"].extend(
        [
            {
                "id": "doc-a",
                "patient_id": "patient-A",
                "status": "parsed",
                "original_filename": "patient-a-private.pdf",
                "primary_category": "laboratory_report",
            },
            {
                "id": "doc-b",
                "patient_id": "patient-B",
                "status": "parsed",
                "original_filename": "patient-b-private.pdf",
                "primary_category": "kidney_renal",
            },
        ]
    )
    data["measurements"].extend(
        [
            create_measurement(document_id="doc-a", metric="glucose", value=5.2).to_dict(),
            create_measurement(document_id="doc-b", metric="egfr", value=42).to_dict(),
        ]
    )
    data["observations"].append(
        {
            "observation_id": "obs-b",
            "patient_id": "patient-B",
            "fact": "Patient B private observation",
            "evidence": [{"document_id": "doc-b"}],
        }
    )
    store._write_index(data)

    token_a = login(client, "patient-A")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    records_a = client.get("/api/records", headers=headers_a).json()["records"]
    serialized = json.dumps(records_a)
    assert [record["document_id"] for record in records_a] == ["doc-a"]
    assert "patient-b-private" not in serialized
    own_detail = client.get("/api/records/doc-a", headers=headers_a).json()
    assert all(
        measurement.get("metric") != "egfr" and measurement.get("value") != 42
        for measurement in own_detail["extracted_measurements"]
    )
    assert "Patient B private observation" not in json.dumps(own_detail)
    assert client.get("/api/records/doc-b", headers=headers_a).status_code == 404
    assert client.get("/api/records/download/doc-b", headers=headers_a).status_code == 404


def test_records_filters_match_consumer_controls(records_app):
    store, client = records_app
    data = store._read_index()
    data["documents"].extend(
        [
            {"id": "lab", "patient_id": "patient-A", "status": "parsed", "primary_category": "laboratory_report", "original_filename": "lab.pdf"},
            {"id": "sleep", "patient_id": "patient-A", "status": "partial", "requires_review": True, "primary_category": "sleep", "original_filename": "sleep.pdf"},
        ]
    )
    store._write_index(data)
    token = login(client, "patient-A")
    headers = {"Authorization": f"Bearer {token}"}
    response = client.get(
        "/api/records?category=sleep&status=requires_review", headers=headers
    )
    assert response.status_code == 200
    assert [row["document_id"] for row in response.json()["records"]] == ["sleep"]


def test_dashboard_records_widget_remains_compatible(records_app):
    store, client = records_app
    data = store._read_index()
    data["documents"].append(
        {
            "id": "dashboard-record",
            "patient_id": "patient-A",
            "status": "parsed",
            "original_filename": "dashboard.pdf",
        }
    )
    store._write_index(data)
    token = login(client, "patient-A")
    response = client.get(
        "/api/dashboard/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    widgets = {widget["widget_id"]: widget for widget in response.json()["widgets"]}
    payload = widgets["import_wizard"]["payload"]
    assert payload["records_count"] == 1
    assert payload["recent_records"][0]["document_id"] == "dashboard-record"
