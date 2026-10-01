from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.health_vault.api import create_health_vault_app
from backend.health_vault.dashboard_service import (
    DashboardService,
    _health_connect_sync_summary,
)
from backend.health_vault.date_extraction import extract_measured_date, timeline_sort_key
from backend.health_vault.event_bus import EventBus
from backend.health_vault.health_snapshot import HealthSnapshotEngine
from backend.health_vault.import_pipeline import ImportPipeline
from backend.health_vault.models import utc_now
from backend.health_vault.parser_registry import ParserRegistry
from backend.health_vault.parsers import register_builtin_parsers
from backend.health_vault.records_service import RecordsService
from backend.health_vault.trend_engine import TrendEngine
from backend.health_vault.vault_store import VaultStore


def _pipeline(store: VaultStore) -> ImportPipeline:
    registry = ParserRegistry()
    register_builtin_parsers(registry)
    return ImportPipeline(store=store, registry=registry, bus=EventBus())


def _import(
    pipeline: ImportPipeline,
    *,
    name: str,
    measured_at: str | None,
    report_date: str | None = None,
    value: int = 24,
) -> dict:
    payload = {"name": name}
    return pipeline.run(
        {
            "patient_id": "patient-A",
            "content": json.dumps(payload).encode(),
            "filename": f"{name}.json",
            "mime_type": "application/json",
            "source_system": "manual_upload",
            "measured_at": measured_at,
            "report_date": report_date,
            "extracted_measurements": [
                {
                    "metric": "egfr",
                    "value": value,
                    "units": "mL/min/1.73m2",
                    "measured_at": measured_at,
                }
            ],
        }
    )


def _widget(summary, widget_id: str) -> dict:
    return next(
        widget.payload for widget in summary.widgets if widget.widget_id == widget_id
    )


def test_recent_import_without_measurement_time_does_not_create_current_egfr(tmp_path):
    store = VaultStore(root=tmp_path / "vault", allow_plaintext=True)
    imported = _import(
        _pipeline(store),
        name="undated-report",
        measured_at=None,
        report_date="2024-01-15",
    )

    document = imported["document"]
    measurement = imported["measurements"][0]
    record = RecordsService(store).list_records("patient-A")[0].to_summary_dict()
    trends = _widget(DashboardService(store).get_summary("patient-A"), "trends_widget")["trends"]
    snapshot = HealthSnapshotEngine(store).generate(patient_id="patient-A", as_of="2026-01-01T00:00:00Z")

    client = TestClient(
        create_health_vault_app(
            store,
            production=False,
            test_users={"patient-A": "correct"},
        )
    )
    login = client.post(
        "/api/auth/login",
        json={"patient_id": "patient-A", "password": "correct"},
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    dashboard_response = client.get("/api/dashboard/summary", headers=headers)
    records_response = client.get("/api/records", headers=headers)

    assert document["measured_at"] is None
    assert document["source_document_date"] == "2024-01-15T00:00:00Z"
    assert document["source_document_date_source"] == "report_date"
    assert document["imported_at"]
    assert measurement["measured_at"] is None
    assert record["measured_at"] is None
    assert record["source_document_date"] == "2024-01-15T00:00:00Z"
    assert record["imported_at"] == document["imported_at"]
    assert "egfr" not in trends
    assert snapshot["cards"] == []
    assert dashboard_response.status_code == 200
    dashboard_trends = next(
        widget["payload"]["trends"]
        for widget in dashboard_response.json()["widgets"]
        if widget["widget_id"] == "trends_widget"
    )
    assert "egfr" not in dashboard_trends
    assert records_response.status_code == 200
    api_record = records_response.json()["records"][0]
    assert api_record["measured_at"] is None
    assert api_record["source_document_date"] == "2024-01-15T00:00:00Z"
    assert api_record["imported_at"] == document["imported_at"]


def test_source_document_date_remains_distinct_and_orders_history():
    result = extract_measured_date(
        source_metadata_date="2024-03-15",
        imported_at="2026-01-01T00:00:00Z",
    )

    assert result["measured_at"] is None
    assert result["source_document_date"] == "2024-03-15T00:00:00Z"
    assert result["source_document_date_source"] == "source_metadata"
    assert timeline_sort_key(result) == "2024-03-15T00:00:00Z"


def test_newer_eligible_measurement_reaches_dashboard_and_survives_restart(tmp_path):
    root = tmp_path / "vault"
    store = VaultStore(root=root, allow_plaintext=True)
    pipeline = _pipeline(store)
    _import(
        pipeline,
        name="older-result",
        measured_at="2024-01-15T00:00:00Z",
        value=24,
    )
    _import(
        pipeline,
        name="newer-result",
        measured_at="2025-12-15T00:00:00Z",
        value=28,
    )

    trend = store.get_trends(patient_id="patient-A")["egfr"]
    assert trend["latest"] == 28.0
    assert trend["latest_measured_at"] == "2025-12-15T00:00:00Z"
    store.save_trends(
        {
            "egfr": {
                "metric": "egfr",
                "latest": 99.0,
                "sample_count": 1,
                "updated_at": utc_now(),
            }
        },
        patient_id="patient-A",
    )

    refreshed = _widget(
        DashboardService(store).get_summary("patient-A"), "trends_widget"
    )["trends"]["egfr"]
    assert refreshed["latest"] == 28.0
    assert refreshed["latest_measured_at"] == "2025-12-15T00:00:00Z"

    reopened = VaultStore(root=root, allow_plaintext=True)
    persisted = _widget(DashboardService(reopened).get_summary("patient-A"), "trends_widget")["trends"]["egfr"]
    assert persisted["latest"] == 28.0
    assert persisted["latest_measured_at"] == "2025-12-15T00:00:00Z"


def test_dashboard_freshness_uses_measurement_time_not_trend_recompute_time(tmp_path):
    store = VaultStore(root=tmp_path / "vault", allow_plaintext=True)
    measured_at = utc_now()
    _import(
        _pipeline(store),
        name="older-but-valid-result",
        measured_at=measured_at,
        value=28,
    )
    TrendEngine(store).recompute("patient-A")

    trends = _widget(DashboardService(store).get_summary("patient-A"), "trends_widget")["trends"]

    assert trends["egfr"]["latest_measured_at"] == measured_at
    assert trends["egfr"]["currentness"] == "current"


def test_health_connect_measurement_and_receipt_times_are_separate():
    class Store:
        def list_observations(self):
            return [
                {
                    "patient_id": "patient-A",
                    "source": "health_connect_companion",
                    "connector_id": "health_connect",
                    "measured_at": "2024-01-15T00:00:00Z",
                    "ingested_at": "2026-01-01T00:00:00Z",
                }
            ]

        def get_companion_status(self):
            return {}

    result = _health_connect_sync_summary(Store(), "patient-A")

    assert result["last_measurement_at"] == "2024-01-15T00:00:00Z"
    assert result["last_observation_at"] == "2024-01-15T00:00:00Z"
    assert result["last_data_received_at"] == "2026-01-01T00:00:00Z"


def test_legacy_import_time_fallback_is_not_a_clinical_measurement(tmp_path):
    from backend.health_vault.models import MedicalDocument, create_measurement

    store = VaultStore(root=tmp_path / "vault", allow_plaintext=True)
    measured_at = utc_now()
    document = MedicalDocument(
        id="legacy-fallback",
        patient_id="patient-A",
        measured_at=measured_at,
        date_source="imported_at_fallback",
        date_confidence=0.25,
    )
    measurement = create_measurement(
        document_id=document.id,
        patient_id="patient-A",
        metric="egfr",
        value=28,
        units="mL/min/1.73m2",
        measured_at=measured_at,
        abnormal_flag="low",
    )
    store.store(document=document, measurements=[measurement])

    assert "egfr" not in TrendEngine(store).recompute("patient-A")
    snapshot = HealthSnapshotEngine(store).generate(patient_id="patient-A")
    assert all(card.get("metric_id") != "egfr" for card in snapshot["cards"])
    summary = DashboardService(store).get_summary("patient-A")
    assert "egfr" not in _widget(summary, "status_summary")["clinical_attention_metrics"]


def test_consumer_freshness_labels_use_observation_and_receipt_timestamps():
    root = Path(__file__).parents[1]
    dashboard = (root / "js" / "health_vault" / "dashboard.js").read_text(encoding="utf-8")
    records = (root / "js" / "health_vault" / "records.js").read_text(encoding="utf-8")

    assert "tr.currentness !== \"current\"" in dashboard
    assert "tr.updated_at || tr.measured_at" not in dashboard
    assert "sync.last_data_received_at" in dashboard
    assert "Most recent data received" in dashboard
    assert "Source document date" in records
    assert "card.last_data_received_at" in records
    assert "36 * 3600000" not in records
