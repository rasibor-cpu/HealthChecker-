"""HC323 — consumer Health Records IA, titles, categories, dedupe, freshness."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.health_vault.api import create_health_vault_app
from backend.health_vault.consumer_records import display_title, metric_from_health_connect_filename
from backend.health_vault.freshness_path import build_freshness_path
from backend.health_vault.models import create_measurement
from backend.health_vault.monitoring.ingestion import IngestionCoordinator
from backend.health_vault.records_service import RecordsService
from backend.health_vault.vault_store import VaultStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def vault_app():
    with tempfile.TemporaryDirectory() as td:
        store = VaultStore(root=Path(td), encryption_key=b"3" * 32)
        client = TestClient(
            create_health_vault_app(store, test_users={"patient-A": "correct", "patient-B": "correct"})
        )
        yield store, client


def _login(client: TestClient, patient_id: str = "patient-A") -> dict[str, str]:
    response = client.post("/api/auth/login", json={"patient_id": patient_id, "password": "correct"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _seed(store: VaultStore, duplicates: bool = True) -> None:
    data = store._read_index()
    token = "83b902b6-cb51-3b2b-924f-a6aead2d0cb4"
    specs = [
        ("hc-hr", "heart_rate", token, 72, "bpm", "2026-08-18T10:00:00Z"),
        ("hc-steps", "steps", "aaaaaaaa-1111-2222-3333-444444444441", 8123, "count", "2026-08-18T10:00:00Z"),
        ("hc-sleep", "sleep_duration", "cccccccc-1111-2222-3333-444444444443", 420, "min", "2026-08-18T10:00:00Z"),
        ("hc-spo2", "oxygen_saturation", "dddddddd-1111-2222-3333-444444444444", 94, "%", "2026-08-18T10:00:00Z"),
    ]
    if duplicates:
        specs.append(("hc-hr-dup", "heart_rate", token, 72, "bpm", "2026-08-18T10:00:00Z"))
    for doc_id, metric, uuid_token, value, units, when in specs:
        data["documents"].append(
            {
                "id": doc_id,
                "patient_id": "patient-A",
                "document_type": "continuous_monitoring_observation",
                "source_system": "health_connect_companion",
                "original_filename": f"health_connect_{metric}_{uuid_token}.json",
                "measured_at": when,
                "imported_at": when,
                "status": "imported",
                "tags": ["hc302", "continuous_monitoring"],
            }
        )
        data["measurements"].append(
            create_measurement(
                document_id=doc_id, metric=metric, value=value, units=units, measured_at=when
            ).to_dict()
        )
        data["observations"].append(
            {
                "patient_id": "patient-A",
                "observation_id": uuid_token if metric == "heart_rate" else doc_id,
                "metric_type": metric,
                "value": value,
                "unit": units,
                "measured_at": when,
                "source": "health_connect_companion",
                "connector_id": "health_connect",
                "source_record_id": f"src-{uuid_token}",
                "device": {"data_origin": "com.sec.android.app.shealth"},
            }
        )
    data["documents"].append(
        {
            "id": "lab-1",
            "patient_id": "patient-A",
            "document_type": "laboratory_report",
            "primary_category": "laboratory_report",
            "source_system": "manual_upload",
            "original_filename": "LifeLabs_chemistry_panel.pdf",
            "measured_at": "2026-08-10T09:00:00Z",
            "imported_at": "2026-08-10T09:05:00Z",
            "status": "imported",
        }
    )
    data["measurements"].append(
        create_measurement(
            document_id="lab-1", metric="glucose", value=5.8, units="mmol/L", measured_at="2026-08-10T09:00:00Z"
        ).to_dict()
    )
    data["documents"].append(
        {
            "id": "other-patient",
            "patient_id": "patient-B",
            "document_type": "laboratory_report",
            "original_filename": "patient-b-secret.json",
            "status": "imported",
        }
    )
    data["documents"].append(
        {
            "id": "b-hc-hr",
            "patient_id": "patient-B",
            "document_type": "continuous_monitoring_observation",
            "source_system": "health_connect_companion",
            "original_filename": "health_connect_heart_rate_bbbbbbbb-1111-2222-3333-444444444444.json",
            "measured_at": "2026-08-24T18:00:00Z",
            "status": "imported",
        }
    )
    data["measurements"].append(
        create_measurement(
            document_id="b-hc-hr", metric="heart_rate", value=999, units="bpm", measured_at="2026-08-24T18:00:00Z"
        ).to_dict()
    )
    data["observations"].append(
        {
            "patient_id": "patient-B",
            "observation_id": "b-obs-hr",
            "metric_type": "heart_rate",
            "value": 999,
            "unit": "bpm",
            "measured_at": "2026-08-24T18:00:00Z",
            "source": "health_connect_companion",
            "connector_id": "health_connect",
            "source_record_id": "src-patient-b-only",
            "device": {"data_origin": "com.other.patient.leak"},
        }
    )
    store._write_index(data)


def test_health_connect_json_not_listed_as_top_level_records(vault_app):
    store, client = vault_app
    _seed(store)
    headers = _login(client)
    clinical = client.get("/api/records?surface=clinical_document", headers=headers).json()
    names = [row["original_filename"] for row in clinical["records"]]
    assert names == ["LifeLabs_chemistry_panel.pdf"]
    assert all("health_connect_" not in name for name in names)
    titles = [row["display_title"] for row in clinical["records"]]
    assert titles == ["LifeLabs Laboratory Results — 10 Aug 2026"]
    source = clinical["health_connect_source"]
    assert source["observation_count"] >= 4
    assert "Health Connect" in source["label"]
    assert "Samsung Health" in source["label"]
    assert clinical["counts"]["documents"] >= 1
    assert clinical["counts"]["health_connect_observations"] >= 4


def test_human_readable_titles_and_categories(vault_app):
    store, client = vault_app
    _seed(store)
    headers = _login(client)
    drill = client.get("/api/records?metric=heart_rate", headers=headers).json()
    assert drill["records"]
    assert drill["records"][0]["display_title"] == "Heart rate observation"
    assert drill["records"][0]["consumer_category_label"] == "Cardiovascular"
    assert "83b902b6" in drill["records"][0]["technical_filename"]
    assert drill["records"][0]["original_filename"].startswith("health_connect_heart_rate_")
    device = client.get("/api/records?surface=device_data", headers=headers).json()
    labels = {row["label"]: row for row in device["device_data"]["summaries"]}
    assert "Heart Rate" in labels
    assert labels["Heart Rate"]["consumer_category_label"] == "Cardiovascular"
    assert labels["Steps"]["consumer_category_label"] == "Activity"
    assert labels["Sleep"]["consumer_category_label"] == "Sleep"
    assert labels["Oxygen Saturation"]["consumer_category_label"] == "Respiratory / Oxygen"


def test_presentation_dedupes_exact_health_connect_filename(vault_app):
    store, client = vault_app
    _seed(store, duplicates=True)
    headers = _login(client)
    drill = client.get("/api/records?metric=heart_rate", headers=headers).json()
    filenames = [row["original_filename"] for row in drill["records"]]
    assert filenames.count(f"health_connect_heart_rate_83b902b6-cb51-3b2b-924f-a6aead2d0cb4.json") == 1
    assert len(store.list_documents()) >= 6  # underlying duplicate row remains


def test_ingest_source_identity_is_idempotent_but_keeps_distinct_records(tmp_path):
    store = VaultStore(root=tmp_path / "vault", encryption_key=b"3" * 32)
    coord = IngestionCoordinator(store=store)
    row = {
        "metric_type": "heart_rate",
        "value": 72,
        "unit": "bpm",
        "measured_at": "2026-08-18T10:00:00Z",
        "source_record_id": "hc-src-83b902b6-cb51-3b2b-924f-a6aead2d0cb4",
        "observation_id": "83b902b6-cb51-3b2b-924f-a6aead2d0cb4",
        "acquisition_mode": "DELAYED",
        "source": "health_connect",
        "patient_id": "patient-A",
    }
    first = coord.ingest_observations([row], connector_id="health_connect", patient_id="patient-A", batch_persist=True)
    changed = dict(row)
    changed["value"] = 73  # same originating record id must not create a second document
    second = coord.ingest_observations([changed], connector_id="health_connect", patient_id="patient-A", batch_persist=True)
    assert int(first["stored"]) == 1
    assert int(second["skipped"]) == 1
    other = dict(row)
    other["source_record_id"] = "hc-src-other"
    other["observation_id"] = "other-obs"
    other["measured_at"] = "2026-08-18T10:01:00Z"
    third = coord.ingest_observations([other], connector_id="health_connect", patient_id="patient-A", batch_persist=True)
    assert int(third["stored"]) == 1
    hr_docs = [
        doc for doc in store.list_documents()
        if str(doc.get("original_filename") or "").startswith("health_connect_heart_rate_")
    ]
    assert len(hr_docs) == 2


def test_freshness_path_distinguishes_sync_from_measurement(vault_app):
    store, client = vault_app
    _seed(store, duplicates=False)
    store.save_companion_status(
        {
            "last_success_at": "2026-08-24T11:43:00Z",
            "last_attempt_at": "2026-08-24T11:43:00Z",
            "health_connect": {"latest_by_metric": {"heart_rate": "2026-08-18T10:00:00Z"}},
        }
    )
    path = build_freshness_path(store, "patient-A")
    assert path["latest_measurement_at"].startswith("2026-08-18")
    assert path["last_health_connect_sync_at"].startswith("2026-08-24")
    assert path["last_ui_refresh_is_not_measurement_time"] is True
    assert path["by_metric"]["heart_rate"]["vault_latest_at"].startswith("2026-08-18")
    assert path["break"]["boundary"] == "health_connect_or_source_app_not_writing_newer_samples"
    headers = _login(client)
    body = client.get("/api/records?surface=clinical_document", headers=headers).json()
    assert body["freshness_path"]["latest_measurement_at"].startswith("2026-08-18")
    summary = client.get("/api/dashboard/summary", headers=headers).json()
    widgets = {row["widget_id"]: row for row in summary["widgets"]}
    freshness = widgets["status_summary"]["payload"]["freshness_path"]
    assert freshness["last_ui_refresh_is_not_measurement_time"] is True


def test_pagination_does_not_emit_thousands_of_cards(vault_app):
    store, client = vault_app
    data = store._read_index()
    for i in range(120):
        doc_id = f"hc-{i}"
        data["documents"].append(
            {
                "id": doc_id,
                "patient_id": "patient-A",
                "document_type": "continuous_monitoring_observation",
                "source_system": "health_connect_companion",
                "original_filename": f"health_connect_heart_rate_{i:08d}-0000-0000-0000-000000000000.json",
                "measured_at": f"2026-08-18T10:{i % 60:02d}:00Z",
                "status": "imported",
            }
        )
        data["measurements"].append(
            create_measurement(
                document_id=doc_id,
                metric="heart_rate",
                value=60 + (i % 30),
                units="bpm",
                measured_at=f"2026-08-18T10:{i % 60:02d}:00Z",
            ).to_dict()
        )
    store._write_index(data)
    headers = _login(client)
    page = client.get("/api/records?metric=heart_rate&limit=40&offset=0", headers=headers).json()
    assert len(page["records"]) == 40
    assert page["page"]["has_more"] is True
    assert page["page"]["total"] == 120
    page2 = client.get("/api/records?metric=heart_rate&limit=40&offset=40", headers=headers).json()
    assert len(page2["records"]) == 40
    assert page["records"][0]["document_id"] != page2["records"][0]["document_id"]


def test_search_heart_rate_does_not_require_uuid(vault_app):
    store, client = vault_app
    _seed(store, duplicates=False)
    headers = _login(client)
    body = client.get("/api/records?q=heart%20rate", headers=headers).json()
    assert any(row["display_title"] == "Heart rate observation" for row in body["records"])
    leaked = json.dumps(body)
    assert "patient-b-secret" not in leaked


def test_patient_scope_and_unfiltered_api_compat(vault_app):
    store, client = vault_app
    _seed(store, duplicates=False)
    headers = _login(client)
    full = client.get("/api/records", headers=headers).json()
    ids = {row["document_id"] for row in full["records"]}
    assert "lab-1" in ids
    assert "hc-hr" in ids
    assert "other-patient" not in ids
    assert "b-hc-hr" not in ids
    other = _login(client, "patient-B")
    other_body = client.get("/api/records", headers=other).json()
    other_ids = {row["document_id"] for row in other_body["records"]}
    assert "other-patient" in other_ids
    assert "lab-1" not in other_ids
    leaked = json.dumps(full)
    assert "patient-b-secret" not in leaked
    assert "com.other.patient.leak" not in leaked
    assert "src-patient-b-only" not in leaked
    source = full["health_connect_source"]
    assert "Samsung Health" in source["label"]
    assert source["observation_count"] >= 4
    device = client.get("/api/records?surface=device_data", headers=headers).json()
    hr = next(row for row in device["device_data"]["summaries"] if row["metric"] == "heart_rate")
    assert hr["latest_value"] != 999


def test_ui_and_screenshot_contracts():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "js" / "health_vault" / "records.js").read_text(encoding="utf-8")
    dash = (ROOT / "js" / "health_vault" / "dashboard.js").read_text(encoding="utf-8")
    snap = (ROOT / "js" / "health_vault" / "health_snapshot.js").read_text(encoding="utf-8")
    sw = (ROOT / "service-worker.js").read_text(encoding="utf-8")
    android = (ROOT / "android/app/src/main/java/com/healthchecker/companion/ui/ScreenshotPolicy.kt").read_text(
        encoding="utf-8"
    )
    assert "Last refreshed" in js
    assert "Last Health Connect sync" in js
    assert "Latest measurement" in js
    assert "Latest in Health Connect" in js
    assert "display_title" in js
    assert "health_connect_source" in js
    assert "data-records-more" in js
    assert "Last refreshed just now" in dash
    assert "Last measured" in snap
    assert 'CACHE_REVISION = "hc324a"' in sw
    assert "service-worker.js?v=hc324a" in html
    assert "FLAG_SECURE" in android
    assert "clearFlags" in android
    assert metric_from_health_connect_filename(
        "health_connect_heart_rate_83b902b6-cb51-3b2b-924f-a6aead2d0cb4.json"
    ) == "heart_rate"
    assert display_title(
        filename="health_connect_heart_rate_83b902b6-cb51-3b2b-924f-a6aead2d0cb4.json",
        metric="heart_rate",
    ) == "Heart rate observation"
    assert display_title(
        filename="LifeLabs_chemistry_panel.pdf",
        document_type="laboratory_report",
        measured_at="2026-08-10T09:00:00Z",
    ) == "LifeLabs Laboratory Results — 10 Aug 2026"


def test_imported_document_remains_coherent(vault_app):
    store, client = vault_app
    _seed(store, duplicates=False)
    headers = _login(client)
    detail = client.get("/api/records/lab-1", headers=headers).json()
    assert detail["document_id"] == "lab-1"
    assert detail["display_title"].startswith("LifeLabs")
    assert detail["extracted_measurements"]
    assert detail["extracted_measurements"][0]["metric"] in {"glucose", "glucose_serum_plasma", "glucose_random"}
