"""HC321-UAT12I — Health Records information architecture and live-evidence mapping.

Presentation-layer grouping only. Vault documents are not deleted, collapsed,
or rewritten. Clinical Health Connect planes stay separate.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.health_vault.api import create_health_vault_app
from backend.health_vault.dashboard_service import DashboardService
from backend.health_vault.doctor_visit import DoctorVisitMode
from backend.health_vault.metric_normalization import canonicalize_metric
from backend.health_vault.models import create_measurement
from backend.health_vault.records_service import (
    RecordsService,
    existing_client_search_matches,
)
from backend.health_vault.trend_engine import TrendEngine
from backend.health_vault.vault_store import VaultStore

ROOT = Path(__file__).resolve().parents[1]

UAT_SEARCH_TERMS = (
    "creatinine",
    "eGFR",
    "HbA1c",
    "glucose",
    "blood pressure",
    "medication",
    "ECG",
)


@pytest.fixture
def vault_app():
    with tempfile.TemporaryDirectory() as td:
        store = VaultStore(root=Path(td), encryption_key=b"I" * 32)
        client = TestClient(
            create_health_vault_app(
                store,
                test_users={"patient-A": "correct", "patient-B": "correct"},
            )
        )
        yield store, client


def _login(client: TestClient, patient_id: str = "patient-A") -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"patient_id": patient_id, "password": "correct"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _hc_doc(doc_id: str, metric: str, uuid_token: str, patient_id: str = "patient-A") -> dict:
    return {
        "id": doc_id,
        "patient_id": patient_id,
        "document_type": "continuous_monitoring_observation",
        "source_system": "health_connect_companion",
        "provenance": "continuous_monitoring",
        "acquisition_method": "continuous_monitor:batch",
        "original_filename": f"health_connect_{metric}_{uuid_token}.json",
        "measured_at": "2026-08-18T10:00:00Z",
        "imported_at": "2026-08-18T10:05:00Z",
        "status": "imported",
        "tags": ["hc302", "continuous_monitoring"],
    }


def _seed_mixed_vault(store: VaultStore) -> None:
    data = store._read_index()
    specs = [
        ("hc-steps", "steps", "aaaaaaaa-1111-2222-3333-444444444441", 8123, "count"),
        ("hc-hr", "heart_rate", "bbbbbbbb-1111-2222-3333-444444444442", 72, "bpm"),
        ("hc-sleep", "sleep_duration", "cccccccc-1111-2222-3333-444444444443", 420, "min"),
        ("hc-spo2", "oxygen_saturation", "dddddddd-1111-2222-3333-444444444444", 94, "%"),
    ]
    for doc_id, metric, token, value, units in specs:
        data["documents"].append(_hc_doc(doc_id, metric, token))
        data["measurements"].append(
            create_measurement(
                document_id=doc_id,
                metric=metric,
                value=value,
                units=units,
                measured_at="2026-08-18T10:00:00Z",
            ).to_dict()
        )
        data["observations"].append(
            {
                "patient_id": "patient-A",
                "metric_type": metric,
                "value": value,
                "unit": units,
                "measured_at": "2026-08-18T10:00:00Z",
                "source": "health_connect_companion",
                "connector_id": "health_connect",
            }
        )
    data["documents"].append(
        {
            "id": "lab-glucose",
            "patient_id": "patient-A",
            "document_type": "laboratory_report",
            "primary_category": "laboratory_report",
            "source_system": "manual_upload",
            "provenance": "manual_upload",
            "acquisition_method": "manual_upload",
            "original_filename": "lab_chemistry_panel.json",
            "measured_at": "2026-08-10T09:00:00Z",
            "imported_at": "2026-08-10T09:05:00Z",
            "status": "imported",
        }
    )
    data["measurements"].append(
        create_measurement(
            document_id="lab-glucose",
            metric="glucose",
            value=5.8,
            units="mmol/L",
            measured_at="2026-08-10T09:00:00Z",
        ).to_dict()
    )
    data["documents"].append(
        {
            "id": "gmail-scan",
            "patient_id": "patient-A",
            "document_type": "unknown",
            "source_system": "gmail",
            "provenance": "gmail",
            "acquisition_method": "automatic_intake",
            "original_filename": "inbox_scan.pdf",
            "status": "imported",
            "imported_at": "2026-08-09T12:00:00Z",
        }
    )
    data["documents"].append(
        {
            "id": "other-patient",
            "patient_id": "patient-B",
            "document_type": "laboratory_report",
            "primary_category": "kidney_renal",
            "original_filename": "patient-b-creatinine.json",
            "status": "imported",
        }
    )
    data["measurements"].append(
        create_measurement(
            document_id="other-patient",
            metric="creatinine",
            value=110,
            units="umol/L",
        ).to_dict()
    )
    data["health_intelligence"] = {
        "observations": [
            {
                "patient_id": "patient-A",
                "category": "glycemic",
                "fact": "No glycemic measurements found in the vault.",
                "interpretation": "Missing data warning",
                "explanation": "No glucose or HbA1c metrics exist in the vault for glycemic analysis.",
            },
            {
                "patient_id": "patient-A",
                "category": "renal",
                "fact": "No renal measurements found in the vault.",
                "interpretation": "Missing data warning",
                "explanation": "No eGFR or creatinine metrics exist in the vault for renal analysis.",
            },
            {
                "patient_id": "patient-A",
                "category": "cardiovascular",
                "fact": "No cardiovascular measurements found in the vault.",
                "interpretation": "Missing data warning",
                "explanation": "No blood pressure or pulse metrics exist in the vault for cardiovascular analysis.",
            },
        ]
    }
    store._write_index(data)


def _seed_health_connect_only(store: VaultStore) -> None:
    data = store._read_index()
    for doc_id, metric, token, value, units in [
        ("hc-steps", "steps", "aaaaaaaa-1111-2222-3333-444444444441", 8123, "count"),
        ("hc-hr", "heart_rate", "bbbbbbbb-1111-2222-3333-444444444442", 72, "bpm"),
        ("hc-sleep", "sleep_duration", "cccccccc-1111-2222-3333-444444444443", 420, "min"),
        ("hc-spo2", "oxygen_saturation", "dddddddd-1111-2222-3333-444444444444", 94, "%"),
    ]:
        data["documents"].append(_hc_doc(doc_id, metric, token))
        data["measurements"].append(
            create_measurement(
                document_id=doc_id,
                metric=metric,
                value=value,
                units=units,
                measured_at="2026-08-18T10:00:00Z",
            ).to_dict()
        )
        data["observations"].append(
            {
                "patient_id": "patient-A",
                "metric_type": metric,
                "value": value,
                "unit": units,
                "measured_at": "2026-08-18T10:00:00Z",
                "source": "health_connect_companion",
                "connector_id": "health_connect",
            }
        )
    data["health_intelligence"] = {
        "observations": [
            {
                "patient_id": "patient-A",
                "category": "glycemic",
                "fact": "No glycemic measurements found in the vault.",
                "interpretation": "Missing data warning",
                "explanation": "No glucose or HbA1c metrics exist in the vault for glycemic analysis.",
            },
            {
                "patient_id": "patient-A",
                "category": "renal",
                "fact": "No renal measurements found in the vault.",
                "interpretation": "Missing data warning",
                "explanation": "No eGFR or creatinine metrics exist in the vault for renal analysis.",
            },
            {
                "patient_id": "patient-A",
                "category": "cardiovascular",
                "fact": "No cardiovascular measurements found in the vault.",
                "interpretation": "Missing data warning",
                "explanation": "No blood pressure or pulse metrics exist in the vault for cardiovascular analysis.",
            },
        ]
    }
    store._write_index(data)


def test_existing_client_search_misses_clinical_terms_on_health_connect_filenames(vault_app):
    """UAT required using the pre-change client search before IA work."""
    store, _client = vault_app
    _seed_mixed_vault(store)
    records = RecordsService(store).list_records("patient-A")
    counts = {
        term: sum(1 for record in records if existing_client_search_matches(record, term))
        for term in UAT_SEARCH_TERMS
    }
    assert counts == {
        "creatinine": 0,
        "eGFR": 0,
        "HbA1c": 0,
        "glucose": 0,
        "blood pressure": 0,
        "medication": 0,
        "ECG": 0,
    }
    assert any(existing_client_search_matches(record, "health_connect") for record in records)
    assert any(existing_client_search_matches(record, "heart") for record in records)


def test_consumer_surfaces_hide_device_json_without_deleting_vault_rows(vault_app):
    store, client = vault_app
    _seed_mixed_vault(store)
    before_ids = {doc["id"] for doc in store.list_documents()}
    headers = _login(client)

    default_all = client.get("/api/records", headers=headers).json()
    assert default_all["vault_record_count"] == 6
    assert {row["document_id"] for row in default_all["records"]} == {
        "hc-steps",
        "hc-hr",
        "hc-sleep",
        "hc-spo2",
        "lab-glucose",
        "gmail-scan",
    }

    clinical = client.get("/api/records?surface=clinical_document", headers=headers).json()
    assert [row["document_id"] for row in clinical["records"]] == ["lab-glucose"]
    assert clinical["records"][0]["metrics_count"] == 1
    assert clinical["surface_counts"]["device_data"] == 4
    assert clinical["device_data"]["preserved"] is True

    imported = client.get("/api/records?surface=imported_reports", headers=headers).json()
    assert [row["document_id"] for row in imported["records"]] == ["gmail-scan"]

    device = client.get("/api/records?surface=device_data", headers=headers).json()
    assert device["records"] == []
    labels = {row["label"] for row in device["device_data"]["summaries"]}
    assert labels == {"Heart Rate", "Steps", "Sleep", "Oxygen Saturation"}
    assert device["device_data"]["record_count"] == 4
    assert sum(row["record_count"] for row in device["device_data"]["summaries"]) == 4

    drill = client.get("/api/records?metric=heart_rate", headers=headers).json()
    assert [row["document_id"] for row in drill["records"]] == ["hc-hr"]
    assert drill["records"][0]["original_filename"].startswith("health_connect_heart_rate_")
    assert drill["records"][0]["metrics_count"] == 1
    assert drill["mode"] == "metric_drilldown"

    assert {doc["id"] for doc in store.list_documents()} == before_ids
    assert len(store.list_documents()) == 7  # includes other-patient
    assert len(RecordsService(store).list_records("patient-A")) == 6


def test_server_search_and_patient_scope(vault_app):
    store, client = vault_app
    _seed_mixed_vault(store)
    headers = _login(client)

    def names(term: str) -> list[str]:
        body = client.get("/api/records", params={"q": term}, headers=headers).json()
        return [row["document_id"] for row in body["records"]]

    assert names("creatinine") == []
    assert names("eGFR") == []
    assert names("HbA1c") == []
    assert names("glucose") == ["lab-glucose"]
    assert names("blood pressure") == []
    assert names("medication") == []
    assert names("ECG") == []
    assert "hc-hr" in names("heart_rate")
    serialized = json.dumps(client.get("/api/records", headers=headers).json())
    assert "patient-b-creatinine" not in serialized
    assert "other-patient" not in serialized


def test_canonicalization_patient_scope_trends_and_doctor_visit(vault_app):
    store, _client = vault_app
    _seed_health_connect_only(store)

    assert canonicalize_metric("pulse") == "heart_rate"
    assert canonicalize_metric("hr") == "heart_rate"
    assert canonicalize_metric("cgm_glucose") == "cgm_glucose"
    assert canonicalize_metric("systolic") == "systolic_bp"
    assert canonicalize_metric("spo2") == "oxygen_saturation"

    records = RecordsService(store).list_records("patient-A")
    assert all(record.patient_id == "patient-A" for record in records)
    assert {record.document_id for record in records} == {"hc-steps", "hc-hr", "hc-sleep", "hc-spo2"}

    trends = TrendEngine(store).recompute("patient-A")
    assert trends["heart_rate"].get("provenance") == "health_connect_observational"
    assert "glucose" not in trends
    assert "egfr" not in trends
    assert "systolic_bp" not in trends
    assert "hba1c" not in trends
    assert "sleep_score" not in trends
    assert "sleep_duration" in trends

    report = DoctorVisitMode(store).generate("patient-A")
    assert report["current_diagnoses"] == []
    assert report["current_medications"] == []
    assert report["recent_ecg"] == []
    assert report["kidney_trend"] == "n/a"
    assert report["blood_pressure_trend"] == "n/a / n/a"
    assert report["sleep_trend"] == "n/a"
    assert report["diabetes_trend"] == "n/a · HbA1c n/a"

    summary = DashboardService(store).get_summary("patient-A")
    widgets = {widget.widget_id: widget for widget in summary.widgets}
    assert widgets["import_wizard"].payload["records_count"] == 4
    observations = widgets["key_observations"].payload["observations"]
    facts = [row.get("fact") or "" for row in observations]
    assert any("Latest heart rate" in fact for fact in facts)
    assert any("No glycemic measurements found in the vault." in fact for fact in facts)
    assert any("No renal measurements found in the vault." in fact for fact in facts)
    assert any("No cardiovascular measurements found in the vault." in fact for fact in facts)


def test_ui_contract_does_not_list_raw_device_json_by_default():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "js" / "health_vault/records.js").read_text(encoding="utf-8")
    sw = (ROOT / "service-worker.js").read_text(encoding="utf-8")
    assert 'data-records-surface="clinical_document"' in html
    assert 'data-records-surface="imported_reports"' in html
    assert 'data-records-surface="device_data"' in html
    assert "Clinical Documents" in html
    assert "Device Data" in html
    assert 'params.set("surface"' in js
    assert "clinical_document" in js
    assert "data-device-metric" in js
    assert "VaultStore" not in js
    assert 'CACHE_REVISION = "hc321uat12i"' in sw
    assert "records" in sw
    assert "service-worker.js?v=hc321uat12i" in html
    assert "records.js?v=hc321uat12i" in html
