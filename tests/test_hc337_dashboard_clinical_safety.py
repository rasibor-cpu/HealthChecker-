from backend.health_vault.dashboard_service import (
    DashboardService,
    _monitoring_observation_cards,
)
from backend.health_vault.health_intelligence import HealthIntelligenceEngine
from backend.health_vault.models import create_measurement
from backend.health_vault.monitoring.monitoring_engine import MonitoringEngine
from backend.health_vault.vault_store import VaultStore


def _document(document_id: str, *, source: str, document_type: str) -> dict:
    return {
        "id": document_id,
        "patient_id": "robert",
        "document_type": document_type,
        "source_system": source,
        "acquisition_method": "manual_upload",
        "status": "imported",
        "measured_at": "2026-09-18T00:00:00Z",
        "date_confidence": 1.0,
    }


def test_health_connect_cards_exclude_manual_clinical_rows(tmp_path, monkeypatch):
    store = VaultStore(root=tmp_path / "vault", encryption_key=b"M" * 32)
    monkeypatch.setattr(
        MonitoringEngine,
        "build_status",
        lambda self, **kwargs: {
            "latest_reading_by_metric": {
                "creatinine": {
                    "value": 252,
                    "unit": "umol/L",
                    "source": "healthchecker_plus",
                    "provenance": "manual_upload",
                },
                "heart_rate": {
                    "value": 79,
                    "unit": "bpm",
                    "source": "health_connect_companion",
                    "provenance": "health_connect_sync",
                },
            }
        },
    )
    cards, _latest = _monitoring_observation_cards(store, "robert")
    assert [card["metric"] for card in cards] == ["heart_rate"]
    assert all("Health Connect" in card["interpretation"] for card in cards)


def test_clinical_intelligence_deduplicates_repairs_and_excludes_health_connect(tmp_path):
    store = VaultStore(root=tmp_path / "vault", encryption_key=b"I" * 32)
    index = store._read_index()
    index["documents"].extend(
        [
            _document("legacy", source="healthchecker_plus", document_type="json_measurements"),
            _document("repair", source="healthchecker_plus", document_type="json_measurements"),
            _document(
                "wearable",
                source="health_connect_companion",
                document_type="continuous_monitoring_observation",
            ),
        ]
    )
    for document_id in ("legacy", "repair"):
        index["measurements"].append(
            create_measurement(
                document_id=document_id,
                patient_id="robert",
                metric="egfr",
                value=24,
                units="mL/min/1.73m2",
                measured_at="2023-11-29",
            ).to_dict()
        )
    index["measurements"].append(
        create_measurement(
            document_id="wearable",
            patient_id="robert",
            metric="egfr",
            value=99,
            units="mL/min/1.73m2",
            measured_at="2026-09-18T00:00:00Z",
        ).to_dict()
    )
    store._write_index(index)
    rows = HealthIntelligenceEngine(store)._get_metric_series("egfr", "robert")
    assert len(rows) == 1
    assert rows[0]["value"] == 24


def test_dashboard_flags_latest_abnormal_clinical_metric(tmp_path):
    store = VaultStore(root=tmp_path / "vault", encryption_key=b"A" * 32)
    index = store._read_index()
    index["documents"].append(
        _document("clinical", source="healthchecker_plus", document_type="json_measurements")
    )
    index["measurements"].append(
        create_measurement(
            document_id="clinical",
            patient_id="robert",
            metric="creatinine",
            value=252,
            units="umol/L",
            abnormal_flag="high",
            measured_at="2023-11-29",
        ).to_dict()
    )
    store._write_index(index)
    summary = DashboardService(store).get_summary("robert")
    status_widget = next(
        widget for widget in summary.widgets if widget.widget_id == "status_summary"
    )
    assert summary.overall_status == "warning"
    assert summary.active_warnings_count >= 1
    assert "creatinine" in status_widget.payload["clinical_attention_metrics"]


def test_dashboard_refreshes_legacy_intelligence_snapshot(tmp_path):
    store = VaultStore(root=tmp_path / "vault", encryption_key=b"R" * 32)
    index = store._read_index()
    index["documents"].append(
        _document("clinical", source="healthchecker_plus", document_type="json_measurements")
    )
    for measured_at, value in (
        ("2022-04-21", 34),
        ("2023-03-30", 27),
        ("2023-11-29", 24),
    ):
        index["measurements"].append(
            create_measurement(
                document_id="clinical",
                patient_id="robert",
                metric="egfr",
                value=value,
                units="mL/min/1.73m2",
                measured_at=measured_at,
            ).to_dict()
        )
    index["health_intelligence"] = {
        "observations": [
            {
                "patient_id": "robert",
                "metric": "egfr",
                "fact": "obsolete one-point observation",
                "interpretation": "stable",
            }
        ],
        "disclaimer": "legacy",
    }
    store._write_index(index)
    DashboardService(store).get_summary("robert")
    refreshed = store.health_intelligence()
    assert refreshed.get("clinical_revision")
    assert all(
        observation.get("fact") != "obsolete one-point observation"
        for observation in refreshed.get("observations", [])
    )
