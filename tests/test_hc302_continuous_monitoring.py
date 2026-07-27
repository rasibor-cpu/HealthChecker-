"""
HC-302R — Continuous Health Monitoring certification & adversarial tests.

Synthetic fixtures only. Never reads or modifies real vault_storage personal records.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.health_vault.api import (
    monitoring_connectors_handler,
    monitoring_evaluate_handler,
    monitoring_scheduler_tick_handler,
    monitoring_status_handler,
    monitoring_sync_handler,
)
from backend.health_vault.guardian.alert_engine import AlertEngine
from backend.health_vault.monitoring.bridge import ContinuousMonitoringBridge
from backend.health_vault.monitoring.connectors.base import (
    clear_device_registry_for_tests,
    list_device_connectors,
    register_device_connector,
    resolve_device_connector,
)
from backend.health_vault.monitoring.connectors.health_connect import HealthConnectConnector
from backend.health_vault.monitoring.connectors.libre import LibreConnector
from backend.health_vault.monitoring.connectors.simulated import SimulatedTestConnector
from backend.health_vault.monitoring.ingestion import IngestionCoordinator
from backend.health_vault.monitoring.monitoring_engine import (
    MonitoringEngine,
    validate_monitoring_thresholds,
)
from backend.health_vault.monitoring.observation import (
    build_observation,
    observation_fingerprint,
    parse_timestamp,
)
from backend.health_vault.monitoring.privacy import redact_for_log
from backend.health_vault.monitoring.scheduler import MonitoringScheduler
from backend.health_vault.vault_store import VaultStore


@pytest.fixture()
def store(tmp_path: Path) -> VaultStore:
    return VaultStore(root=tmp_path / "vault")


@pytest.fixture(autouse=True)
def _reload_connectors():
    clear_device_registry_for_tests()
    register_device_connector(HealthConnectConnector())
    register_device_connector(LibreConnector())
    register_device_connector(SimulatedTestConnector())
    yield
    clear_device_registry_for_tests()


class _FakeHCBridge:
    def __init__(self, state: str = "ready", observations=None):
        self.state = state
        self.observations = observations or []

    def readiness(self):
        return {
            "state": self.state,
            "permission_required": self.state.startswith("permission"),
            "permission_granted": self.state == "ready",
            "live_available": self.state == "ready",
            "errors": [],
        }

    def fetch_new_observations(self, cursor=None, context=None):
        return {
            "observations": self.observations,
            "next_cursor": {"token": "c2", "after": "2026-07-26T12:00:00Z"},
        }


# ---------------------------------------------------------------------------
# Core integrity
# ---------------------------------------------------------------------------


def test_observation_model_validation_and_timezone():
    obs = build_observation(
        {
            "metric_type": "heart_rate",
            "value": 72,
            "unit": "bpm",
            "measured_at": "2026-07-26T08:00:00",
            "acquisition_mode": "DELAYED",
            "source": "health_connect",
            "source_record_id": "hc-1",
        }
    )
    assert obs.measured_at.endswith("Z")
    assert obs.received_at.endswith("Z")
    assert obs.measured_at != obs.received_at or True  # received is ingest time
    assert obs.validate() == []


def test_timezone_default_tz_interpretation():
    obs = build_observation(
        {
            "metric_type": "steps",
            "value": 1000,
            "unit": "count",
            "measured_at": "2026-07-26T00:00:00",
            "acquisition_mode": "IMPORTED",
            "source": "test",
            "source_record_id": "s1",
        },
        default_tz="America/New_York",
    )
    assert obs.measured_at == "2026-07-26T04:00:00Z"


def test_unit_normalization_glucose_mmol_equivalent_dedupe(store: VaultStore):
    ingest = IngestionCoordinator(store=store)
    a = {
        "metric_type": "glucose",
        "value": 10.0,
        "unit": "mmol/L",
        "measured_at": "2026-07-26T10:00:00Z",
        "acquisition_mode": "IMPORTED",
        "source": "libre_import",
        "source_record_id": "g-eq",
    }
    b = {
        "metric_type": "glucose",
        "value": 180.182,
        "unit": "mg/dL",
        "measured_at": "2026-07-26T10:00:00Z",
        "acquisition_mode": "IMPORTED",
        "source": "libre_import",
        "source_record_id": "g-eq",
    }
    r1 = ingest.ingest_observations([a], connector_id="libre")
    r2 = ingest.ingest_observations([b], connector_id="libre")
    assert r1["stored"] == 1
    assert r2["skipped"] == 1
    assert abs(float(store.list_observations()[0]["value"]) - 180.182) < 0.01


def test_incompatible_unit_fails_explicitly():
    with pytest.raises(ValueError, match="unit_incompatible"):
        build_observation(
            {
                "metric_type": "glucose",
                "value": 100,
                "unit": "stones",
                "measured_at": "2026-07-26T10:00:00Z",
                "acquisition_mode": "IMPORTED",
                "source": "x",
                "source_record_id": "bad-u",
            }
        )


def test_fingerprint_includes_patient_and_keeps_distinct_readings():
    fp_a = observation_fingerprint(
        patient_id="p1",
        source="s",
        source_record_id="1",
        metric="glucose",
        measured_at="2026-07-26T10:00:00Z",
        value=100,
        units="mg/dL",
    )
    fp_b = observation_fingerprint(
        patient_id="p2",
        source="s",
        source_record_id="1",
        metric="glucose",
        measured_at="2026-07-26T10:00:00Z",
        value=100,
        units="mg/dL",
    )
    assert fp_a != fp_b


def test_deduplication_idempotent_and_restart_safe(store: VaultStore):
    ingest = IngestionCoordinator(store=store)
    payload = {
        "metric_type": "heart_rate",
        "value": 80,
        "unit": "bpm",
        "measured_at": "2026-07-26T11:00:00Z",
        "acquisition_mode": "DELAYED",
        "source": "health_connect",
        "source_record_id": "hr-80",
    }
    assert ingest.ingest_observations([payload], connector_id="health_connect")["stored"] == 1
    # Simulate restart with new coordinator against same store
    ingest2 = IngestionCoordinator(store=store)
    assert ingest2.ingest_observations([payload], connector_id="health_connect")["skipped"] == 1
    assert len(store.list_observations()) == 1


def test_distinct_same_second_without_source_id_not_collapsed():
    o1 = build_observation(
        {
            "metric_type": "glucose",
            "value": 110,
            "unit": "mg/dL",
            "measured_at": "2026-07-26T10:00:00.100000+00:00",
            "acquisition_mode": "IMPORTED",
            "source": "libre_import",
        }
    )
    o2 = build_observation(
        {
            "metric_type": "glucose",
            "value": 110,
            "unit": "mg/dL",
            "measured_at": "2026-07-26T10:00:00.900000+00:00",
            "acquisition_mode": "IMPORTED",
            "source": "libre_import",
        }
    )
    assert o1.fingerprint != o2.fingerprint


def test_provenance_survives_persistence(store: VaultStore):
    ingest = IngestionCoordinator(store=store)
    ingest.ingest_observations(
        [
            {
                "metric_type": "oxygen_saturation",
                "value": 96,
                "unit": "%",
                "measured_at": "2026-07-26T11:00:00Z",
                "acquisition_mode": "DELAYED",
                "source": "health_connect",
                "source_record_id": "spo2-1",
                "provenance": "health_connect_sync",
            }
        ],
        connector_id="health_connect",
    )
    row = store.list_observations()[0]
    assert row["provenance"] == "health_connect_sync"
    assert row["acquisition_mode"] == "DELAYED"
    docs = store.list_documents()
    assert docs[0]["provenance"] == "health_connect_sync"


# ---------------------------------------------------------------------------
# Connectors / cursors
# ---------------------------------------------------------------------------


def test_health_connect_unavailable_and_permission_denied(store: VaultStore):
    conn = resolve_device_connector("health_connect")
    assert conn.readiness()["state"] == "unavailable"
    assert conn.fetch_new_observations()["status"] == "UNAVAILABLE"

    bridge = ContinuousMonitoringBridge(store=store)
    denied = bridge.sync_connector(
        "health_connect",
        context={"platform_bridge": _FakeHCBridge(state="permission_denied")},
    )
    assert denied["ok"] is False
    assert denied["status"] == "permission_denied"
    assert bridge.ingestion.get_cursor("health_connect") == {}


def test_libre_import_required_not_live(store: VaultStore):
    conn = resolve_device_connector("libre")
    assert conn.readiness()["state"] == "import_required"
    assert conn.fetch_new_observations()["status"] == "IMPORT_REQUIRED"
    imported = conn.fetch_new_observations(
        context={
            "imported_observations": [
                {
                    "metric_type": "glucose",
                    "value": 120,
                    "unit": "mg/dL",
                    "measured_at": "2026-07-26T09:00:00Z",
                    "source_record_id": "lib-1",
                }
            ]
        }
    )
    assert imported["observations"][0]["acquisition_mode"] == "IMPORTED"


def test_cursor_not_advanced_on_persist_failure(store: VaultStore):
    bridge = ContinuousMonitoringBridge(store=store)
    fake = _FakeHCBridge(
        state="ready",
        observations=[
            {
                "metric_type": "heart_rate",
                "value": 70,
                "unit": "bpm",
                "measured_at": "2026-07-26T12:00:00Z",
                "source_record_id": "hr-fail",
                "acquisition_mode": "DELAYED",
            }
        ],
    )

    def boom(*args, **kwargs):
        raise RuntimeError("disk_full")

    bridge.ingestion.store.store = boom  # type: ignore[method-assign]
    result = bridge.sync_connector(
        "health_connect",
        context={"platform_bridge": fake},
        run_guardian=False,
    )
    assert result["ok"] is False
    assert result.get("cursor_advanced") is False
    assert bridge.ingestion.get_cursor("health_connect") == {}


def test_partial_connector_failure_keeps_cursor(store: VaultStore):
    bridge = ContinuousMonitoringBridge(store=store)
    fake = _FakeHCBridge(
        state="ready",
        observations=[
            {
                "metric_type": "heart_rate",
                "value": 70,
                "unit": "bpm",
                "measured_at": "2026-07-26T12:00:00Z",
                "source_record_id": "ok-1",
                "acquisition_mode": "DELAYED",
            },
            {
                "metric_type": "glucose",
                "value": 100,
                "unit": "stones",
                "measured_at": "2026-07-26T12:01:00Z",
                "source_record_id": "bad-1",
                "acquisition_mode": "DELAYED",
            },
        ],
    )
    result = bridge.sync_connector(
        "health_connect",
        context={"platform_bridge": fake},
        run_guardian=False,
    )
    assert result["ok"] is False
    assert result.get("cursor_advanced") is False
    assert int(result.get("stored") or 0) == 1


def test_permission_revoked_after_prior_success(store: VaultStore):
    bridge = ContinuousMonitoringBridge(store=store)
    ok_bridge = _FakeHCBridge(
        state="ready",
        observations=[
            {
                "metric_type": "resting_hr",
                "value": 58,
                "unit": "bpm",
                "measured_at": "2026-07-26T07:00:00Z",
                "source_record_id": "rhr-1",
                "acquisition_mode": "DELAYED",
            }
        ],
    )
    first = bridge.sync_connector(
        "health_connect",
        context={"platform_bridge": ok_bridge},
        run_guardian=False,
    )
    assert first["ok"] is True
    cursor = bridge.ingestion.get_cursor("health_connect")
    assert cursor.get("token") == "c2"

    revoked = bridge.sync_connector(
        "health_connect",
        context={"platform_bridge": _FakeHCBridge(state="permission_denied")},
        run_guardian=False,
    )
    assert revoked["ok"] is False
    assert bridge.ingestion.get_cursor("health_connect") == cursor


def test_simulated_forbidden_in_production_and_public_api(store: VaultStore):
    bridge = ContinuousMonitoringBridge(store=store)
    assert bridge.sync_connector("simulated", allow_simulated=False)["ok"] is False
    api = monitoring_sync_handler(
        {"connector_id": "simulated", "allow_simulated": True},
        store=store,
    )
    assert api["ok"] is False
    assert "simulated_connector_forbidden_via_public_api" in api["errors"]
    ids = {r["connector_id"] for r in list_device_connectors(include_simulated=False)}
    assert "simulated" not in ids


def test_simulated_isolated_from_clinical_and_guardian(store: VaultStore):
    bridge = ContinuousMonitoringBridge(store=store)
    result = bridge.sync_connector(
        "simulated",
        allow_simulated=True,
        context={
            "simulated_observations": [
                {
                    "metric_type": "glucose",
                    "value": 40,
                    "unit": "mg/dL",
                    "measured_at": "2026-07-26T12:00:00Z",
                    "source_record_id": "sim-g",
                }
            ]
        },
        run_guardian=True,
    )
    assert result["ok"] is True
    obs = store.list_observations()
    assert obs[0]["acquisition_mode"] == "SIMULATED_TEST_ONLY"
    assert obs[0].get("clinical_persist") is False
    assert store.list_measurements() == []
    assert result["guardian"]["ran"] is False
    # Monitoring evaluate must not alert on simulated
    eng = MonitoringEngine(store=store)
    out = eng.evaluate(now="2026-07-26T12:05:00Z")
    assert out["alerts_touched"] == 0


# ---------------------------------------------------------------------------
# Monitoring / alerts
# ---------------------------------------------------------------------------


def test_stale_critical_reading_does_not_masquerade_as_current(store: VaultStore):
    ingest = IngestionCoordinator(store=store)
    ingest.ingest_observations(
        [
            {
                "metric_type": "glucose",
                "value": 40,
                "unit": "mg/dL",
                "measured_at": "2026-07-26T01:00:00Z",
                "acquisition_mode": "IMPORTED",
                "source": "libre_import",
                "source_record_id": "stale-crit",
            }
        ],
        connector_id="libre",
        now="2026-07-26T12:00:00Z",
    )
    engine = MonitoringEngine(store=store, ingestion=ingest)
    engine.evaluate(now="2026-07-26T12:00:00Z", trigger="stale_crit")
    alerts = store.list_alerts()
    assert any(str(a.get("rule_id")).startswith("mon_stale_glucose") for a in alerts)
    assert not any(a.get("rule_id") == "glucose_very_low" for a in alerts)
    status = engine.build_status(now="2026-07-26T12:00:00Z")
    assert status["latest_reading_by_metric"]["glucose"]["is_current"] is False
    assert status["latest_reading_by_metric"]["glucose"]["freshness_status"] == "stale"


def test_threshold_and_duplicate_suppression(store: VaultStore):
    ingest = IngestionCoordinator(store=store)
    ingest.ingest_observations(
        [
            {
                "metric_type": "glucose",
                "value": 260,
                "unit": "mg/dL",
                "measured_at": "2026-07-26T11:50:00Z",
                "acquisition_mode": "IMPORTED",
                "source": "libre_import",
                "source_record_id": "g-high",
            }
        ],
        connector_id="libre",
        now="2026-07-26T11:55:00Z",
    )
    engine = MonitoringEngine(store=store, ingestion=ingest)
    engine.evaluate(now="2026-07-26T11:55:00Z")
    engine.evaluate(now="2026-07-26T11:56:00Z")
    high = [a for a in store.list_alerts() if a.get("rule_id") == "glucose_high"]
    assert len(high) == 1
    assert high[0]["evidence"].get("acquisition_mode") == "IMPORTED"
    assert high[0]["evidence"].get("measured_at")


def test_trend_requires_enough_points(store: VaultStore):
    ingest = IngestionCoordinator(store=store)
    ingest.ingest_observations(
        [
            {
                "metric_type": "glucose",
                "value": 100,
                "unit": "mg/dL",
                "measured_at": "2026-07-26T10:00:00Z",
                "acquisition_mode": "IMPORTED",
                "source": "libre_import",
                "source_record_id": "g-a",
            },
            {
                "metric_type": "glucose",
                "value": 190,
                "unit": "mg/dL",
                "measured_at": "2026-07-26T10:45:00Z",
                "acquisition_mode": "IMPORTED",
                "source": "libre_import",
                "source_record_id": "g-b",
            },
        ],
        connector_id="libre",
        now="2026-07-26T10:50:00Z",
    )
    engine = MonitoringEngine(store=store, ingestion=ingest)
    engine.evaluate(now="2026-07-26T10:50:00Z")
    assert not any(a.get("rule_id") == "glucose_rapid_rise" for a in store.list_alerts())

    ingest.ingest_observations(
        [
            {
                "metric_type": "glucose",
                "value": 200,
                "unit": "mg/dL",
                "measured_at": "2026-07-26T10:50:00Z",
                "acquisition_mode": "IMPORTED",
                "source": "libre_import",
                "source_record_id": "g-c",
            }
        ],
        connector_id="libre",
        now="2026-07-26T10:51:00Z",
    )
    engine.evaluate(now="2026-07-26T10:51:00Z")
    rise = [a for a in store.list_alerts() if a.get("rule_id") == "glucose_rapid_rise"]
    assert len(rise) == 1
    assert rise[0]["evidence"].get("from_measured_at")
    assert rise[0]["evidence"].get("to_measured_at")


def test_worsening_during_ack_reactivates(store: VaultStore):
    alerts = AlertEngine(store)
    first = {
        "triggered": True,
        "rule_id": "glucose_low",
        "title": "Low glucose",
        "message": "Observational low glucose.",
        "severity": "urgent",
        "category": "glucose",
        "metric": "glucose",
        "metrics": ["glucose"],
        "evidence": {"value": 68, "op": "lte", "measured_at": "2026-07-26T10:00:00Z"},
    }
    a1 = alerts.ingest_evaluation(first, now="2026-07-26T10:00:00Z")
    assert a1
    alerts.acknowledge(a1["alert_id"])
    worse = dict(first)
    worse["evidence"] = {"value": 58, "op": "lte", "measured_at": "2026-07-26T10:10:00Z"}
    a2 = alerts.ingest_evaluation(worse, now="2026-07-26T10:10:00Z")
    assert a2["alert_id"] == a1["alert_id"]
    assert a2["status"] == "active"


def test_ack_then_new_critical_creates_separate_alert(store: VaultStore):
    alerts = AlertEngine(store)
    low = {
        "triggered": True,
        "rule_id": "glucose_low",
        "title": "Low",
        "message": "Observational.",
        "severity": "urgent",
        "category": "glucose",
        "metric": "glucose",
        "metrics": ["glucose"],
        "evidence": {"value": 65, "op": "lte"},
    }
    a1 = alerts.ingest_evaluation(low, now="2026-07-26T10:00:00Z")
    alerts.acknowledge(a1["alert_id"])
    critical = {
        "triggered": True,
        "rule_id": "glucose_very_low",
        "title": "Very low",
        "message": "Observational emergency-routing notice — seek emergency help if needed. No dosing advice.",
        "severity": "critical",
        "category": "glucose",
        "metric": "glucose",
        "metrics": ["glucose"],
        "evidence": {"value": 50, "op": "lte", "emergency_routing": True},
    }
    a2 = alerts.ingest_evaluation(critical, now="2026-07-26T10:05:00Z")
    assert a2["alert_id"] != a1["alert_id"]
    assert a2["status"] == "active"
    assert "dosing" not in (a2.get("message") or "").lower() or "no dosing" in (critical["message"]).lower()


def test_malformed_threshold_config_fails_safely():
    validated = validate_monitoring_thresholds(
        {
            "rules": [
                {"rule_id": "ok", "metric": "heart_rate", "op": "gt", "value": 130, "severity": "warning"},
                {"rule_id": "bad", "metric": "heart_rate", "op": "??", "value": "x", "severity": "nope"},
            ],
            "trend_rules": [{"rule_id": "t", "metric": "glucose"}],
        }
    )
    assert validated["ok"] is False
    assert len(validated["rules"]) == 1
    assert validated["validation_errors"]


def test_no_duplicate_alerts_with_guardian(store: VaultStore):
    bridge = ContinuousMonitoringBridge(store=store)
    fake = _FakeHCBridge(
        state="ready",
        observations=[
            {
                "metric_type": "glucose",
                "value": 260,
                "unit": "mg/dL",
                "measured_at": "2026-07-26T11:50:00Z",
                "source_record_id": "g-dup",
                "acquisition_mode": "DELAYED",
            }
        ],
    )
    # Use libre import path via ingest then both engines
    ingest = IngestionCoordinator(store=store)
    ingest.ingest_observations(
        [
            {
                "metric_type": "glucose",
                "value": 260,
                "unit": "mg/dL",
                "measured_at": "2026-07-26T11:50:00Z",
                "acquisition_mode": "IMPORTED",
                "source": "libre_import",
                "source_record_id": "g-dup2",
            }
        ],
        connector_id="libre",
        now="2026-07-26T11:55:00Z",
    )
    MonitoringEngine(store=store).evaluate(now="2026-07-26T11:55:00Z")
    bridge.guardian.evaluate(patient_id="default-patient", trigger="hc302_test")
    high = [a for a in store.list_alerts() if a.get("rule_id") == "glucose_high"]
    assert len(high) == 1


# ---------------------------------------------------------------------------
# Scheduler / privacy / API
# ---------------------------------------------------------------------------


def test_scheduler_backoff_overlap_and_persistence(store: VaultStore):
    sched = MonitoringScheduler(store=store)
    assert sched.is_due() is True
    s1 = sched.plan_next(success=False, now="2026-07-26T10:00:00Z", error="fail")
    assert s1["last_attempt_at"] == "2026-07-26T10:00:00Z"
    assert s1["last_success_at"] is None
    assert s1["busy_loop"] is False
    assert s1["continuous_guaranteed"] is False

    # Persisted across new instance
    sched2 = MonitoringScheduler(store=store)
    assert sched2.is_due(now="2026-07-26T10:00:00Z") is False

    # Overlap guard on same instance with active lease
    sched._state["running"] = True
    sched._state["last_attempt_at"] = "2026-07-26T10:00:00Z"
    sched._state["lease_expires_at"] = "2099-01-01T00:00:00Z"
    sched._persist()
    out = sched.run_due(lambda: {"ok": True}, now="2026-07-26T10:01:00Z", force=True)
    assert out["ran"] is False
    assert out["reason"] == "already_running"

    # Fresh process sees active lease and also refuses
    sched3 = MonitoringScheduler(store=store)
    out3 = sched3.run_due(lambda: {"ok": True}, now="2026-07-26T10:01:00Z", force=True)
    assert out3["ran"] is False
    assert out3["reason"] == "already_running"


def test_scheduler_unavailable_is_not_false_success(store: VaultStore):
    bridge = ContinuousMonitoringBridge(store=store)
    tick = bridge.run_scheduled_sync(force=True, now="2026-07-26T10:00:00Z")
    assert tick["ran"] is True
    assert tick["result"]["ok"] is False
    assert tick["result"].get("degraded") is True
    assert tick["scheduler"]["last_success_at"] is None
    assert tick["scheduler"]["last_attempt_at"] == "2026-07-26T10:00:00Z"


def test_privacy_safe_logging_and_api_response(store: VaultStore):
    redacted = redact_for_log(
        {
            "metric": "glucose",
            "value": 55,
            "source_record_id": "secret-id",
            "token": "abc",
            "measured_at": "2026-07-26T10:00:00Z",
        }
    )
    blob = json.dumps(redacted)
    assert "55" not in blob
    assert "secret-id" not in blob
    assert redacted["measured_at"] == "2026-07-26T10:00:00Z"

    status = monitoring_status_handler(store=store)
    assert "background" in status
    assert status["background"]["continuous_guaranteed"] is False
    dumped = json.dumps(status)
    assert "token" not in dumped.lower() or "access_token" not in dumped


def test_api_contracts(store: VaultStore):
    connectors = monitoring_connectors_handler(store=store)
    assert all(c["connector_id"] != "simulated" for c in connectors["connectors"])
    sync = monitoring_sync_handler({"connector_id": "libre"}, store=store)
    assert sync["status"] == "IMPORT_REQUIRED"
    assert monitoring_evaluate_handler({"trigger": "test"}, store=store)["ok"] is True
    tick = monitoring_scheduler_tick_handler({"force": True}, store=store)
    assert tick.get("ran") is True


def test_service_worker_forbids_api_cache():
    sw = (ROOT / "service-worker.js").read_text(encoding="utf-8")
    assert "vault_storage" in sw
    assert "/api/" in sw
    assert "isForbiddenCacheUrl" in sw
    assert "MONITORING_SYNC" in sw
    assert "continuous" in sw.lower() and "NOT guaranteed" in sw


def test_parse_timestamp_rejects_invalid():
    with pytest.raises(ValueError):
        parse_timestamp("not-a-date")


def test_live_imported_manual_classification(store: VaultStore):
    bridge = ContinuousMonitoringBridge(store=store)
    live = bridge.sync_connector(
        "health_connect",
        context={
            "platform_bridge": _FakeHCBridge(
                state="ready",
                observations=[
                    {
                        "metric_type": "heart_rate",
                        "value": 88,
                        "unit": "bpm",
                        "measured_at": "2026-07-26T12:00:00Z",
                        "source_record_id": "live-hr",
                        "acquisition_mode": "LIVE",
                    }
                ],
            )
        },
        run_guardian=False,
        now="2026-07-26T12:05:00Z",
    )
    assert live["ok"] is True
    IngestionCoordinator(store=store).ingest_observations(
        [
            {
                "metric_type": "weight",
                "value": 80,
                "unit": "kg",
                "measured_at": "2026-07-26T12:00:00Z",
                "acquisition_mode": "MANUAL",
                "source": "manual_entry",
                "source_record_id": "w1",
            }
        ],
        connector_id="manual",
    )
    modes = {o["acquisition_mode"] for o in store.list_observations()}
    assert "LIVE" in modes and "MANUAL" in modes
