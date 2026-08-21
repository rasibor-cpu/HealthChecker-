"""HC-321-C1 consumer functional completion tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.health_vault.auth import (
    AuthenticationError,
    AuthenticationService,
)
from backend.health_vault.dashboard_service import DashboardService, _merge_trend_planes, _monitoring_trend_snapshot
from backend.health_vault.metric_normalization import MONITORING_TREND_METRICS, TREND_METRICS
from backend.health_vault.models import create_measurement
from backend.health_vault.trend_engine import TrendEngine, _is_health_connect_context
from backend.health_vault.vault_store import VaultStore


@pytest.fixture
def store(tmp_path: Path):
    return VaultStore(root=tmp_path)


def test_monitoring_trend_metrics_include_health_connect_observables():
    assert "oxygen_saturation" in MONITORING_TREND_METRICS
    assert "steps" in MONITORING_TREND_METRICS
    assert "heart_rate" in MONITORING_TREND_METRICS
    assert "sleep_duration" in MONITORING_TREND_METRICS
    # Clinical-only lab metrics stay out of monitoring set
    assert "glucose" not in MONITORING_TREND_METRICS
    assert "egfr" not in MONITORING_TREND_METRICS
    assert "glucose" in TREND_METRICS


def test_health_connect_oxygen_saturation_is_trend_eligible(store: VaultStore):
    data = store._read_index()
    doc = {
        "id": "hc-spo2-doc",
        "patient_id": "00000",
        "document_type": "continuous_monitoring_observation",
        "source_system": "health_connect_companion",
        "connector_id": "health_connect",
        "status": "imported",
        "measured_at": "2026-08-18T11:00:00Z",
        "primary_category": None,
        "date_confidence": None,
        "tags": ["continuous_monitoring", "hc302"],
    }
    measurements = [
        create_measurement(
            document_id="hc-spo2-doc",
            metric="oxygen_saturation",
            value=v,
            units="%",
            measured_at=f"2026-08-18T11:0{i}:00Z",
        ).to_dict()
        for i, v in enumerate((91, 92, 93, 94))
    ]
    for row in measurements:
        row["source"] = "health_connect_companion"
        row["connector_id"] = "health_connect"
    data["documents"].append(doc)
    data["measurements"].extend(measurements)
    store._write_index(data)

    engine = TrendEngine(store)
    assert engine._eligible(measurements[0], engine._docs_by_id()) is True
    assert _is_health_connect_context(measurements[0], doc) is True
    trends = engine.recompute("00000")
    assert "oxygen_saturation" in trends
    assert trends["oxygen_saturation"]["provenance"] == "health_connect_observational"
    assert trends["oxygen_saturation"]["data_plane"] == "monitoring"
    assert trends["oxygen_saturation"]["sample_count"] == 4


def test_clinical_and_monitoring_series_are_not_merged(store: VaultStore):
    data = store._read_index()
    clinical_docs = []
    clinical_meas = []
    for i, value in enumerate((70, 72, 74)):
        did = f"lab-hr-{i}"
        clinical_docs.append(
            {
                "id": did,
                "patient_id": "00000",
                "document_type": "laboratory_pdf",
                "source_system": "lifelabs",
                "status": "imported",
                "measured_at": f"2026-08-1{i}T10:00:00Z",
                "date_confidence": 1.0,
                "primary_category": "cardiovascular",
            }
        )
        clinical_meas.append(
            create_measurement(
                document_id=did,
                metric="heart_rate",
                value=value,
                units="bpm",
                measured_at=f"2026-08-1{i}T10:00:00Z",
            ).to_dict()
        )
    hc_doc = {
        "id": "hc-hr",
        "patient_id": "00000",
        "document_type": "continuous_monitoring_observation",
        "source_system": "health_connect_companion",
        "connector_id": "health_connect",
        "status": "imported",
        "measured_at": "2026-08-19T10:00:00Z",
        "tags": ["continuous_monitoring"],
    }
    hc_meas = [
        create_measurement(
            document_id="hc-hr",
            metric="heart_rate",
            value=v,
            units="bpm",
            measured_at=f"2026-08-19T10:0{i}:00Z",
        ).to_dict()
        for i, v in enumerate((100, 101, 102))
    ]
    for row in hc_meas:
        row["connector_id"] = "health_connect"
    data["documents"].extend(clinical_docs + [hc_doc])
    data["measurements"].extend(clinical_meas + hc_meas)
    store._write_index(data)

    engine = TrendEngine(store)
    clinical = engine.series("heart_rate", "00000", plane="clinical")
    monitoring = engine.series("heart_rate", "00000", plane="monitoring")
    auto = engine.series("heart_rate", "00000", plane="auto")
    assert clinical == [70.0, 72.0, 74.0]
    assert monitoring == [100.0, 101.0, 102.0]
    assert auto == clinical  # clinical preferred; not merged with wearable


def test_monitoring_observation_snapshot_preserves_provenance(store: VaultStore):
    data = store._read_index()
    data.setdefault("observations", [])
    for i, value in enumerate((94, 93, 92)):
        data["observations"].append(
            {
                "patient_id": "00000",
                "metric_type": "oxygen_saturation",
                "value": value,
                "unit": "%",
                "measured_at": f"2026-08-18T12:0{i}:00Z",
                "source": "health_connect_companion",
                "connector_id": "health_connect",
            }
        )
    # Non-HC observation must not enter monitoring trends
    data["observations"].append(
        {
            "patient_id": "00000",
            "metric_type": "oxygen_saturation",
            "value": 50,
            "measured_at": "2026-08-18T13:00:00Z",
            "source": "manual_note",
        }
    )
    store._write_index(data)

    trends, sample_count = _monitoring_trend_snapshot(store, "00000")
    assert "oxygen_saturation" in trends
    assert trends["oxygen_saturation"]["provenance"] == "health_connect_observational"
    assert trends["oxygen_saturation"]["sample_count"] == 3
    assert sample_count == 3


def test_merge_prefers_explicit_clinical_and_labels_combined():
    merged = _merge_trend_planes(
        {"heart_rate": {"metric": "heart_rate", "latest": 70, "sample_count": 3, "provenance": "clinical"}},
        {
            "heart_rate": {
                "metric": "heart_rate",
                "latest": 110,
                "sample_count": 9,
                "provenance": "health_connect_observational",
            },
            "oxygen_saturation": {
                "metric": "oxygen_saturation",
                "latest": 94,
                "provenance": "health_connect_observational",
            },
        },
    )
    assert merged["heart_rate"]["provenance"] == "combined_clinical_and_health_connect"
    assert merged["heart_rate"]["data_plane"] == "combined"
    assert merged["oxygen_saturation"]["provenance"] == "health_connect_observational"


def test_merge_does_not_let_unprovenanced_cache_override_hc():
    merged = _merge_trend_planes(
        {"heart_rate": {"metric": "heart_rate", "latest": 70, "sample_count": 3}},
        {
            "heart_rate": {
                "metric": "heart_rate",
                "latest": 110,
                "sample_count": 9,
                "provenance": "health_connect_observational",
                "data_plane": "monitoring",
            },
            "steps": {
                "metric": "steps",
                "latest": 4000,
                "provenance": "health_connect_observational",
                "data_plane": "monitoring",
            },
        },
    )
    assert merged["heart_rate"]["provenance"] == "health_connect_observational"
    assert merged["heart_rate"]["latest"] == 110
    assert merged["steps"]["provenance"] == "health_connect_observational"


def test_dashboard_includes_monitoring_trends_alongside_attention(store: VaultStore):
    data = store._read_index()
    data.setdefault("observations", [])
    for i, value in enumerate((91, 92, 93)):
        data["observations"].append(
            {
                "patient_id": "patient-1",
                "metric_type": "oxygen_saturation",
                "value": value,
                "measured_at": f"2026-08-18T11:0{i}:00Z",
                "source": "health_connect_companion",
                "connector_id": "health_connect",
            }
        )
    store._write_index(data)

    # Generate classic missing-data attention items (glycemic/renal/cardiovascular).
    from backend.health_vault.health_intelligence import HealthIntelligenceEngine

    HealthIntelligenceEngine(store).generate_observations("patient-1")

    summary = DashboardService(store).get_summary("patient-1").to_dict()
    trends = next(w for w in summary["widgets"] if w["widget_id"] == "trends_widget")["payload"]["trends"]
    status = next(w for w in summary["widgets"] if w["widget_id"] == "status_summary")["payload"]
    assert "oxygen_saturation" in trends
    assert trends["oxygen_saturation"]["provenance"] == "health_connect_observational"
    assert status["health_connect_observation_count"] == 3
    assert status["active_warnings"] == 3
    sync = status["health_connect_sync"]
    assert sync["sync_state"] in {"observations_present", "synced", "paired_awaiting_data", "not_configured"}
    assert sync["observation_count"] == 3
    assert "label" in sync


def test_intentionally_excluded_hc_metrics_are_surfaced(store: VaultStore):
    data = store._read_index()
    data.setdefault("observations", [])
    # distance is observational HC data but not in MONITORING_TREND_METRICS
    data["observations"].append(
        {
            "patient_id": "patient-1",
            "metric_type": "distance_meters",
            "value": 1200,
            "measured_at": "2026-08-18T11:00:00Z",
            "source": "health_connect_companion",
            "connector_id": "health_connect",
        }
    )
    store._write_index(data)
    summary = DashboardService(store).get_summary("patient-1").to_dict()
    trends_payload = next(w for w in summary["widgets"] if w["widget_id"] == "trends_widget")["payload"]
    exclusions = trends_payload.get("exclusions") or []
    assert any(e.get("metric") == "distance_meters" for e in exclusions)
    assert any(e.get("reason") == "intentionally_excluded_from_trends" for e in exclusions)


def test_login_lockout_after_repeated_failures(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HC_AUTH_MIN_SECONDS_BETWEEN_ATTEMPTS", "1")
    monkeypatch.setenv("HC_AUTH_MAX_FAILED_LOGINS", "5")
    monkeypatch.setenv("HC_AUTH_LOCKOUT_MINUTES", "15")
    from backend.health_vault import auth as auth_mod

    vault = VaultStore(root=tmp_path / "vault", encryption_key=b"A" * 32)
    auth = AuthenticationService(vault, bootstrap_password="Correct-Password-1")
    threshold = auth_mod.max_failed_logins()
    for _ in range(threshold):
        with pytest.raises(AuthenticationError):
            auth.login("00000", "wrong")
        # Advance clock past inter-attempt rate limit without sleeping wall clock.
        data = auth._read()
        data["accounts"]["00000"]["last_failed_login_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=2)
        ).isoformat()
        auth._write(data)
    data = auth._read()
    assert data["accounts"]["00000"]["account_status"] == "locked"
    assert data["accounts"]["00000"]["locked_until"]
    with pytest.raises(AuthenticationError):
        auth.unlock_after_cooldown("00000", "Correct-Password-1")
    # Expire lockout and confirm recovery via explicit unlock entrypoint
    data["accounts"]["00000"]["locked_until"] = (
        datetime.now(timezone.utc) - timedelta(minutes=auth_mod.lockout_minutes() + 1)
    ).isoformat()
    auth._write(data)
    ok = auth.unlock_after_cooldown("00000", "Correct-Password-1")
    assert ok["patient_id"] == "00000"
    assert auth._read()["accounts"]["00000"]["failed_login_count"] == 0
    assert auth._read()["accounts"]["00000"].get("locked_until") in (None, "")
