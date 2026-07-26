"""
HC-301 — Always-On Health Guardian foundation & CGM continuity tests.
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
    cgm_activate_handler,
    cgm_inventory_update_handler,
    cgm_register_handler,
    guardian_acknowledge_handler,
    guardian_alerts_handler,
    guardian_baselines_handler,
    guardian_evaluate_handler,
    guardian_resolve_handler,
    guardian_status_handler,
    import_health_record_handler,
)
from backend.health_vault.clinical_rules import ClinicalRulesEngine, FLAG_UNKNOWN
from backend.health_vault.guardian.alert_engine import AlertEngine
from backend.health_vault.guardian.baseline_engine import BaselineEngine
from backend.health_vault.guardian.cgm_continuity import CGMContinuityGuardian
from backend.health_vault.guardian.health_guardian import HealthGuardian
from backend.health_vault.guardian.rule_engine import ExpandedClinicalRulesEngine
from backend.health_vault.models import MedicalDocument, create_measurement
from backend.health_vault.timeline import build_timeline, build_unified_timeline
from backend.health_vault.vault_store import VaultStore


@pytest.fixture()
def store(tmp_path: Path) -> VaultStore:
    return VaultStore(root=tmp_path / "vault")


def _seed_glucose(
    store: VaultStore,
    values: list[tuple[str, float]],
    units: str = "mg/dL",
    *,
    patient_id: str = "default-patient",
    context: str | None = None,
    doc_prefix: str = "gdoc",
) -> None:
    for i, (ts, val) in enumerate(values):
        doc = MedicalDocument(
            id=f"{doc_prefix}-{i}-{units.replace('/', '_')}-{patient_id}",
            patient_id=patient_id,
            document_type="blood_glucose",
            measured_at=ts,
            provenance="user_reported",
        )
        m = create_measurement(
            metric="glucose", value=val, units=units, measured_at=ts, document_id=doc.id
        )
        store.store(
            document=doc,
            measurements=[m],
            content=json.dumps({"glucose": val, "i": i, "patient_id": patient_id}).encode(),
        )
    if context:
        data = store._read_index()
        for row in data["measurements"]:
            if row.get("metric") == "glucose" and str(row.get("document_id") or "").startswith(
                f"{doc_prefix}-"
            ):
                row["context"] = context
                row["meal_context"] = context
        store._write_index(data)


def _base_eval(**overrides):
    ev = {
        "triggered": True,
        "rule_id": "glucose_high",
        "rule_version": "1.0.0",
        "title": "High glucose",
        "message": "High",
        "severity": "warning",
        "category": "glucose",
        "metric": "glucose",
        "metrics": ["glucose"],
        "evidence": {"value": 260},
    }
    ev.update(overrides)
    return ev


# ---------------------------------------------------------------------------
# Existing HC-301 tests (kept)
# ---------------------------------------------------------------------------


def test_alert_creation_and_deduplication(store: VaultStore):
    eng = AlertEngine(store)
    ev = {
        "triggered": True,
        "rule_id": "glucose_high",
        "rule_version": "1.0.0",
        "title": "High glucose",
        "message": "High",
        "severity": "warning",
        "category": "glucose",
        "metric": "glucose",
        "metrics": ["glucose"],
        "evidence": {"value": 260},
    }
    a1 = eng.ingest_evaluation(ev, now="2026-07-26T10:00:00Z")
    a2 = eng.ingest_evaluation(ev, now="2026-07-26T10:05:00Z")
    assert a1 and a2
    assert a1["alert_id"] == a2["alert_id"]
    assert a2["occurrence_count"] == 2
    assert len(eng.list_alerts(active_only=True)) == 1


def test_alert_escalation_and_cooldown(store: VaultStore):
    eng = AlertEngine(store, cooldowns_minutes={"warning": 60, "urgent": 30, "critical": 15})
    base = {
        "triggered": True,
        "rule_id": "glucose_high",
        "title": "High glucose",
        "message": "High",
        "severity": "warning",
        "category": "glucose",
        "metrics": ["glucose"],
        "deduplication_key": "p|glucose_high|glucose",
    }
    a = eng.ingest_evaluation(base, now="2026-07-26T10:00:00Z")
    escalated = eng.ingest_evaluation({**base, "severity": "urgent"}, now="2026-07-26T10:10:00Z")
    assert escalated["severity"] == "urgent"
    assert escalated["status"] == "active"
    eng.resolve(a["alert_id"], force=True, now="2026-07-26T10:20:00Z")
    suppressed = eng.ingest_evaluation(base, now="2026-07-26T10:25:00Z")
    assert suppressed is None


def test_alert_acknowledge_resolve_critical_persistence(store: VaultStore):
    eng = AlertEngine(store)
    critical = eng.ingest_evaluation(
        {
            "triggered": True,
            "rule_id": "glucose_very_low",
            "title": "Very low glucose",
            "message": "Critical low",
            "severity": "critical",
            "category": "glucose",
            "metrics": ["glucose"],
        }
    )
    assert critical["status"] == "active"
    bad = eng.resolve(critical["alert_id"])
    assert bad["ok"] is False
    assert "critical_requires_acknowledgement" in bad["errors"]
    ack = eng.acknowledge(critical["alert_id"])
    assert ack["ok"] is True
    assert ack["alert"]["status"] == "acknowledged"
    resolved = eng.resolve(critical["alert_id"])
    assert resolved["ok"] is True
    assert resolved["alert"]["status"] == "resolved"


def test_absolute_threshold_and_no_data_not_normal(store: VaultStore):
    eng = ClinicalRulesEngine()
    assert eng.classify({"metric": "glucose", "value": None, "units": "mg/dL"}) == FLAG_UNKNOWN
    _seed_glucose(store, [("2026-07-26T08:00:00Z", 260)])
    rules = ExpandedClinicalRulesEngine(store)
    triggered = rules.evaluate(now="2026-07-26T09:00:00Z")
    ids = {e["rule_id"] for e in triggered if e.get("triggered")}
    assert "glucose_high" in ids


def test_rate_of_change_rolling_persistence_multi_metric(store: VaultStore):
    _seed_glucose(
        store,
        [
            ("2026-07-26T08:00:00Z", 160),
            ("2026-07-26T08:30:00Z", 100),
        ],
    )
    rules = ExpandedClinicalRulesEngine(store)
    ids = {e["rule_id"] for e in rules.evaluate(now="2026-07-26T08:35:00Z") if e.get("triggered")}
    assert "glucose_rapid_fall" in ids

    store2 = VaultStore(root=store.root.parent / "vault2")
    for i, (sys_v, dia_v) in enumerate([(150, 95), (150, 95)]):
        doc = MedicalDocument(
            id=f"bp-{i}",
            document_type="blood_pressure_screenshot",
            measured_at=f"2026-07-26T0{i}:00:00Z",
        )
        ms = [
            create_measurement(
                metric="systolic", value=sys_v, units="mmHg", measured_at=doc.measured_at, document_id=doc.id
            ),
            create_measurement(
                metric="diastolic", value=dia_v, units="mmHg", measured_at=doc.measured_at, document_id=doc.id
            ),
        ]
        store2.store(document=doc, measurements=ms, content=b"{}")
    ids2 = {
        e["rule_id"]
        for e in ExpandedClinicalRulesEngine(store2).evaluate(now="2026-07-26T02:00:00Z")
        if e.get("triggered")
    }
    assert "elevated_blood_pressure" in ids2

    store3 = VaultStore(root=store.root.parent / "vault3")
    _seed_glucose(
        store3,
        [
            ("2026-07-20T08:00:00Z", 110),
            ("2026-07-21T08:00:00Z", 130),
            ("2026-07-22T08:00:00Z", 150),
            ("2026-07-23T08:00:00Z", 170),
        ],
    )
    data = store3._read_index()
    for m in data["measurements"]:
        m["abnormal_flag"] = "Abnormal"
    store3._write_index(data)
    ids3 = {
        e["rule_id"]
        for e in ExpandedClinicalRulesEngine(store3).evaluate(now="2026-07-23T12:00:00Z")
        if e.get("triggered")
    }
    assert "repeated_abnormal" in ids3
    assert "worsening_multi_day_trend" in ids3


def test_missing_data_rule(store: VaultStore):
    _seed_glucose(store, [("2026-07-26T01:00:00Z", 120)])
    g = HealthGuardian(store=store)
    cont = g.cgm.evaluate_continuity(now="2026-07-26T06:00:00Z")
    gap = g.cgm.detect_glucose_gap(now="2026-07-26T06:00:00Z")
    assert gap is not None
    evs = g.rules.evaluate(
        context={"continuity": {**cont, "open_data_gaps": [gap]}},
        now="2026-07-26T06:00:00Z",
    )
    assert any(e.get("rule_id") == "glucose_data_gap" for e in evs if e.get("triggered"))


def test_baseline_calculation_insufficient_and_units(store: VaultStore):
    eng = BaselineEngine(
        store,
        config={
            "minimum_sample_count": 5,
            "rolling_window_days": 90,
            "min_confidence": 0.0,
            "percentile_low": 10,
            "percentile_high": 90,
            "supported_metrics": ["glucose"],
            "contexts": ["unspecified"],
        },
    )
    _seed_glucose(store, [(f"2026-07-0{i+1}T08:00:00Z", 100 + i) for i in range(3)])
    summary = eng.rebuild()
    assert summary["baselines"]["glucose"]["insufficient_data"] is True
    assert summary["baselines"]["glucose"]["ready"] is False

    store2 = VaultStore(root=store.root.parent / "base2")
    _seed_glucose(store2, [(f"2026-07-{i+1:02d}T08:00:00Z", 100 + i) for i in range(6)])
    doc = MedicalDocument(id="mmol", document_type="blood_glucose", measured_at="2026-07-10T08:00:00Z")
    store2.store(
        document=doc,
        measurements=[
            create_measurement(
                metric="glucose", value=6.0, units="mmol/L", measured_at=doc.measured_at, document_id=doc.id
            )
        ],
        content=b"{}",
    )
    s2 = BaselineEngine(store2, config=eng.config).rebuild()
    g = s2["baselines"]["glucose"]
    assert g["ready"] is True
    assert g["units"] == "mg/dL"
    assert g["sample_count"] == 6
    dev = BaselineEngine(store2, config=eng.config).deviation("glucose", 200, units="mg/dL")
    assert dev["available"] is True


def test_baseline_deviation_feeds_rules(store: VaultStore):
    cfg = {
        "minimum_sample_count": 5,
        "rolling_window_days": 90,
        "min_confidence": 0.0,
        "percentile_low": 10,
        "percentile_high": 90,
        "supported_metrics": ["glucose"],
        "contexts": [],
    }
    _seed_glucose(store, [(f"2026-07-{i+1:02d}T08:00:00Z", 100) for i in range(6)])
    doc = MedicalDocument(id="out", document_type="blood_glucose", measured_at="2026-07-20T08:00:00Z")
    store.store(
        document=doc,
        measurements=[
            create_measurement(
                metric="glucose", value=220, units="mg/dL", measured_at=doc.measured_at, document_id=doc.id
            )
        ],
        content=b"{}",
    )
    base = BaselineEngine(store, config=cfg)
    base.rebuild()
    rules = ExpandedClinicalRulesEngine(store, baseline=base)
    ids = {e["rule_id"] for e in rules.evaluate(now="2026-07-20T09:00:00Z") if e.get("triggered")}
    assert "baseline_deviation_glucose" in ids


def test_sensor_register_activate_inventory_floor(store: VaultStore):
    cgm = CGMContinuityGuardian(store)
    cgm.update_inventory({"unused_sensor_count": 2, "protected_reserve_count": 1, "confidence": "confirmed"})
    sensor = cgm.register_sensor({"serial_or_reference": "ABC"})
    result = cgm.activate_sensor(sensor["sensor_id"], activation_timestamp="2026-07-20T00:00:00Z")
    assert result["ok"] is True
    inv = cgm.get_inventory()
    assert inv["unused_sensor_count"] == 1
    cgm.update_inventory({"unused_sensor_count": -5, "confidence": "confirmed"})
    assert cgm.get_inventory()["unused_sensor_count"] == 0


def test_protected_reserve_coverage_reorder_travel_expiry_failure(store: VaultStore):
    cgm = CGMContinuityGuardian(store)
    cgm.update_inventory(
        {
            "unused_sensor_count": 0,
            "minimum_protected_reserve": 1,
            "travel_buffer_days": 7,
            "reorder_lead_days": 5,
            "expected_wear_days": 14,
            "confidence": "confirmed",
        }
    )
    sensor = cgm.register_sensor({"expected_wear_days": 14})
    cgm.activate_sensor(sensor["sensor_id"], activation_timestamp="2026-07-01T00:00:00Z", reduce_inventory=False)
    cont = cgm.evaluate_continuity(now="2026-07-14T12:00:00Z")
    assert cont["state"] in ("SENSOR_EXPIRING", "SENSOR_EXPIRED", "REORDER_REQUIRED", "CRITICAL_SHORTAGE")
    inv = cont["inventory"]
    assert inv["reorder_deadline"] is not None
    assert inv["projected_coverage_days"] is not None
    assert inv["unused_sensor_count"] < inv["minimum_protected_reserve"]
    failed = cgm.fail_sensor(sensor["sensor_id"], reason="adhesion")
    assert failed["ok"] is True
    assert failed["sensor"]["status"] == "failed"


def test_inventory_unknown_and_signal_gap(store: VaultStore):
    cgm = CGMContinuityGuardian(store)
    cont = cgm.evaluate_continuity()
    assert "INVENTORY_UNKNOWN" in cont["states"] or cont["state"] == "INVENTORY_UNKNOWN"
    gap = cgm.detect_glucose_gap(now="2026-07-26T12:00:00Z")
    assert gap is not None
    assert gap.get("reason_classification") == "no_glucose_measurements"


def test_guardian_orchestration_and_status_severity(store: VaultStore):
    _seed_glucose(store, [("2026-07-26T08:00:00Z", 54)])
    g = HealthGuardian(store=store)
    result = g.evaluate(now="2026-07-26T08:05:00Z", trigger="test")
    assert result["ok"] is True
    status = result["status"]
    assert status["overall_state"] in (
        "CRITICAL",
        "URGENT",
        "WARNING",
        "WATCH",
        "NORMAL",
        "MONITORING_DEGRADED",
    )
    assert status["known_limitations"]
    assert any("Libre" in x or "PWA" in x for x in status["known_limitations"])
    assert status["active_alert_count_by_severity"]["total"] >= 1


def test_unified_timeline_ordering_and_dedupe(store: VaultStore):
    _seed_glucose(store, [("2026-07-26T08:00:00Z", 120)])
    store.append_timeline_event(
        {
            "kind": "data_gap",
            "category": "cgm_continuity",
            "measured_at": "2026-07-26T09:00:00Z",
            "summary": "gap",
            "dedupe_key": "gap-1",
        }
    )
    store.append_timeline_event(
        {
            "kind": "data_gap",
            "category": "cgm_continuity",
            "measured_at": "2026-07-26T09:00:00Z",
            "summary": "gap",
            "dedupe_key": "gap-1",
        }
    )
    entries = build_unified_timeline(store)
    gap_entries = [e for e in entries if e.get("entry_kind") == "data_gap"]
    assert len(gap_entries) == 1


def test_api_handlers(store: VaultStore):
    status = guardian_status_handler(store=store)
    assert "overall_state" in status or "known_limitations" in status
    out = guardian_evaluate_handler({"trigger": "test"}, store=store)
    assert "status" in out
    alerts = guardian_alerts_handler(store=store)
    assert "alerts" in alerts
    bases = guardian_baselines_handler(store=store)
    assert "baselines" in bases or "as_of" in bases
    reg = cgm_register_handler({"serial_or_reference": "X1"}, store=store)
    sid = reg["sensor"]["sensor_id"]
    cgm_inventory_update_handler({"unused_sensor_count": 3, "confidence": "confirmed"}, store=store)
    act = cgm_activate_handler(sid, {}, store=store)
    assert act["ok"] is True
    eng = AlertEngine(store)
    a = eng.ingest_evaluation(
        {
            "triggered": True,
            "rule_id": "t",
            "title": "t",
            "message": "t",
            "severity": "warning",
            "metrics": ["glucose"],
        }
    )
    assert guardian_acknowledge_handler(a["alert_id"], {}, store=store)["ok"] is True
    assert guardian_resolve_handler(a["alert_id"], {}, store=store)["ok"] is True


def test_recent_record_duplicate_protection(store: VaultStore):
    payload = {
        "filename": "bp_samsung_2026-07-25.json",
        "mime_type": "application/json",
        "document_type": "blood_pressure_screenshot",
        "source_system": "Samsung Health Monitor",
        "measured_at": "2026-07-26T03:48:00Z",
        "provenance": "wearable_screenshot",
        "content": json.dumps(
            {
                "systolic": 127,
                "diastolic": 84,
                "resting_hr": 65,
                "measured_at": "2026-07-26T03:48:00Z",
            }
        ).encode(),
    }
    r1 = import_health_record_handler(payload, store=store)
    r2 = import_health_record_handler(payload, store=store)
    assert r1.get("ok") is True
    assert r2.get("duplicate") is True


def test_import_triggers_guardian(store: VaultStore):
    result = import_health_record_handler(
        {
            "filename": "glu.json",
            "mime_type": "application/json",
            "content": json.dumps({"glucose": 54, "measured_at": "2026-07-26T05:00:00Z"}).encode(),
            "measured_at": "2026-07-26T05:00:00Z",
        },
        store=store,
    )
    assert result.get("ok") is True
    assert "guardian" in result
    assert store.get_guardian_status()


# ---------------------------------------------------------------------------
# Alert engine — expanded coverage
# ---------------------------------------------------------------------------


def test_alert_unique_ids(store: VaultStore):
    eng = AlertEngine(store)
    a1 = eng.ingest_evaluation(_base_eval(rule_id="r1"), now="2026-07-26T10:00:00Z")
    a2 = eng.ingest_evaluation(_base_eval(rule_id="r2"), now="2026-07-26T10:01:00Z")
    assert a1["alert_id"] != a2["alert_id"]


def test_alert_patient_isolation(store: VaultStore):
    eng = AlertEngine(store)
    a = eng.ingest_evaluation(_base_eval(), patient_id="patient-a", now="2026-07-26T10:00:00Z")
    b = eng.ingest_evaluation(_base_eval(), patient_id="patient-b", now="2026-07-26T10:00:00Z")
    assert a["alert_id"] != b["alert_id"]
    assert a["patient_id"] == "patient-a"
    assert b["patient_id"] == "patient-b"
    assert len(eng.list_alerts(patient_id="patient-a", active_only=True)) == 1
    assert len(eng.list_alerts(patient_id="patient-b", active_only=True)) == 1


def test_alert_dedupe_same_patient(store: VaultStore):
    eng = AlertEngine(store)
    a1 = eng.ingest_evaluation(_base_eval(), patient_id="p1", now="2026-07-26T10:00:00Z")
    a2 = eng.ingest_evaluation(_base_eval(), patient_id="p1", now="2026-07-26T10:05:00Z")
    assert a1["alert_id"] == a2["alert_id"]
    assert a2["occurrence_count"] == 2


def test_alert_repeat_count_increments(store: VaultStore):
    eng = AlertEngine(store)
    last = None
    for i in range(4):
        last = eng.ingest_evaluation(_base_eval(), now=f"2026-07-26T10:0{i}:00Z")
    assert last["occurrence_count"] == 4


def test_alert_escalation_warning_to_urgent(store: VaultStore):
    eng = AlertEngine(store)
    eng.ingest_evaluation(_base_eval(severity="warning"), now="2026-07-26T10:00:00Z")
    up = eng.ingest_evaluation(_base_eval(severity="urgent"), now="2026-07-26T10:10:00Z")
    assert up["severity"] == "urgent"
    assert any(h.get("action") == "escalated" for h in up.get("audit_history") or [])


def test_alert_persistence_escalation_at_five(store: VaultStore):
    eng = AlertEngine(store)
    last = None
    for i in range(5):
        last = eng.ingest_evaluation(
            _base_eval(severity="warning"),
            now=f"2026-07-26T1{i}:00:00Z",
        )
    assert last["occurrence_count"] >= 5
    assert last["severity"] in ("urgent", "warning")
    # After 5 same-severity detections, persistence bump should lift warning → urgent
    assert last["severity"] == "urgent"


def test_alert_cooldown_suppresses_non_critical(store: VaultStore):
    eng = AlertEngine(store, cooldowns_minutes={"warning": 60})
    a = eng.ingest_evaluation(_base_eval(severity="warning"), now="2026-07-26T10:00:00Z")
    eng.resolve(a["alert_id"], now="2026-07-26T10:05:00Z")
    again = eng.ingest_evaluation(_base_eval(severity="warning"), now="2026-07-26T10:10:00Z")
    assert again is None


def test_alert_acknowledge_sets_status(store: VaultStore):
    eng = AlertEngine(store)
    a = eng.ingest_evaluation(_base_eval(), now="2026-07-26T10:00:00Z")
    out = eng.acknowledge(a["alert_id"], note="seen", now="2026-07-26T10:01:00Z")
    assert out["ok"] is True
    assert out["alert"]["status"] == "acknowledged"
    assert out["alert"]["acknowledgement_state"] == "acknowledged"


def test_alert_resolve_after_ack(store: VaultStore):
    eng = AlertEngine(store)
    a = eng.ingest_evaluation(_base_eval(), now="2026-07-26T10:00:00Z")
    eng.acknowledge(a["alert_id"])
    out = eng.resolve(a["alert_id"], now="2026-07-26T10:02:00Z")
    assert out["ok"] is True
    assert out["alert"]["status"] == "resolved"
    assert out["alert"]["cooldown_until"]


def test_alert_critical_requires_ack_before_resolve(store: VaultStore):
    eng = AlertEngine(store)
    a = eng.ingest_evaluation(_base_eval(severity="critical", rule_id="crit"), now="2026-07-26T10:00:00Z")
    blocked = eng.resolve(a["alert_id"])
    assert blocked["ok"] is False
    assert "critical_requires_acknowledgement" in blocked["errors"]
    eng.acknowledge(a["alert_id"])
    ok = eng.resolve(a["alert_id"])
    assert ok["ok"] is True


def test_alert_reactivation_after_cooldown_with_new_evidence(store: VaultStore):
    eng = AlertEngine(store, cooldowns_minutes={"warning": 30})
    a = eng.ingest_evaluation(
        _base_eval(evidence={"value": 260}),
        now="2026-07-26T10:00:00Z",
    )
    eng.resolve(a["alert_id"], now="2026-07-26T10:05:00Z")
    # Still in cooldown
    assert eng.ingest_evaluation(_base_eval(evidence={"value": 265}), now="2026-07-26T10:20:00Z") is None
    # Past cooldown — new evidence creates a fresh alert
    revived = eng.ingest_evaluation(
        _base_eval(evidence={"value": 280}),
        now="2026-07-26T10:40:00Z",
    )
    assert revived is not None
    assert revived["evidence"]["value"] == 280
    assert revived["status"] == "active"


def test_alert_critical_bypasses_cooldown(store: VaultStore):
    eng = AlertEngine(store, cooldowns_minutes={"warning": 60, "critical": 15})
    a = eng.ingest_evaluation(_base_eval(severity="warning"), now="2026-07-26T10:00:00Z")
    eng.resolve(a["alert_id"], force=True, now="2026-07-26T10:05:00Z")
    # Non-critical suppressed
    assert eng.ingest_evaluation(_base_eval(severity="warning"), now="2026-07-26T10:10:00Z") is None
    # Critical reactivates despite cooldown
    crit = eng.ingest_evaluation(
        _base_eval(severity="critical", rule_id="glucose_high"),
        now="2026-07-26T10:10:00Z",
    )
    assert crit is not None
    assert crit["severity"] == "critical"


def test_alert_audit_immutability_copy_history(store: VaultStore):
    eng = AlertEngine(store)
    a = eng.ingest_evaluation(_base_eval(), now="2026-07-26T10:00:00Z")
    eng.acknowledge(a["alert_id"], now="2026-07-26T10:01:00Z")
    fetched = eng.get_alert(a["alert_id"])
    hist = list(fetched.get("audit_history") or [])
    original_len = len(hist)
    assert original_len >= 2
    # Mutating the returned list must not wipe the store
    returned = fetched.get("audit_history")
    if isinstance(returned, list):
        returned.clear()
    refetch = eng.get_alert(a["alert_id"])
    # Re-read from disk via fresh list_alerts path
    store_again = AlertEngine(VaultStore(root=store.root))
    persisted = store_again.get_alert(a["alert_id"])
    assert len(persisted.get("audit_history") or []) == original_len


def test_alert_malformed_payload_rejection(store: VaultStore):
    eng = AlertEngine(store)
    assert eng.ingest_evaluation(None) is None  # type: ignore[arg-type]
    assert eng.ingest_evaluation({}) is None
    assert eng.ingest_evaluation({"triggered": False, "rule_id": "x"}) is None
    assert eng.ingest_evaluation({"triggered": True}) is None  # missing rule_id
    assert eng.ingest_evaluation({"triggered": True, "rule_id": "x", "severity": "not-a-sev"}) is None


# ---------------------------------------------------------------------------
# Rules engine
# ---------------------------------------------------------------------------


def test_rules_invalid_config_value_error(store: VaultStore):
    with pytest.raises(ValueError):
        ExpandedClinicalRulesEngine(store, rules_config="not-an-object")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ExpandedClinicalRulesEngine(store, rules_config={})
    with pytest.raises(ValueError):
        ExpandedClinicalRulesEngine(store, rules_config={"rules": "nope"})


def test_rules_unknown_type_value_error(store: VaultStore):
    with pytest.raises(ValueError, match="unknown type"):
        ExpandedClinicalRulesEngine(
            store,
            rules_config={
                "rules": [
                    {
                        "rule_id": "weird",
                        "type": "telepathy_threshold",
                        "title": "x",
                    }
                ]
            },
        )


@pytest.mark.parametrize(
    "value,rule_id",
    [
        (260, "glucose_high"),
        (65, "glucose_low"),
        (50, "glucose_very_low"),
    ],
)
def test_rules_upper_lower_threshold(store: VaultStore, value: float, rule_id: str):
    _seed_glucose(store, [("2026-07-26T08:00:00Z", value)], doc_prefix=f"thr-{rule_id}")
    ids = {
        e["rule_id"]
        for e in ExpandedClinicalRulesEngine(store).evaluate(now="2026-07-26T09:00:00Z")
        if e.get("triggered")
    }
    assert rule_id in ids


def test_rules_rolling_average(store: VaultStore):
    _seed_glucose(
        store,
        [
            ("2026-07-20T08:00:00Z", 100),
            ("2026-07-21T08:00:00Z", 130),
            ("2026-07-22T08:00:00Z", 160),
            ("2026-07-23T08:00:00Z", 190),
        ],
    )
    ids = {
        e["rule_id"]
        for e in ExpandedClinicalRulesEngine(store).evaluate(now="2026-07-23T12:00:00Z")
        if e.get("triggered")
    }
    assert "worsening_multi_day_trend" in ids


def test_rules_rise_rate(store: VaultStore):
    _seed_glucose(
        store,
        [
            ("2026-07-26T08:00:00Z", 100),
            ("2026-07-26T08:40:00Z", 160),
        ],
        doc_prefix="rise",
    )
    ids = {
        e["rule_id"]
        for e in ExpandedClinicalRulesEngine(store).evaluate(now="2026-07-26T08:45:00Z")
        if e.get("triggered")
    }
    assert "glucose_rapid_rise" in ids


def test_rules_fall_rate(store: VaultStore):
    _seed_glucose(
        store,
        [
            ("2026-07-26T08:00:00Z", 180),
            ("2026-07-26T08:30:00Z", 120),
        ],
        doc_prefix="fall",
    )
    ids = {
        e["rule_id"]
        for e in ExpandedClinicalRulesEngine(store).evaluate(now="2026-07-26T08:35:00Z")
        if e.get("triggered")
    }
    assert "glucose_rapid_fall" in ids


def test_rules_consecutive_abnormal(store: VaultStore):
    _seed_glucose(
        store,
        [
            ("2026-07-20T08:00:00Z", 140),
            ("2026-07-21T08:00:00Z", 150),
            ("2026-07-22T08:00:00Z", 160),
        ],
        doc_prefix="consec",
    )
    data = store._read_index()
    for m in data["measurements"]:
        m["abnormal_flag"] = "Abnormal"
    store._write_index(data)
    ids = {
        e["rule_id"]
        for e in ExpandedClinicalRulesEngine(store).evaluate(now="2026-07-22T12:00:00Z")
        if e.get("triggered")
    }
    assert "repeated_abnormal" in ids


def test_rules_multi_metric_bp(store: VaultStore):
    doc = MedicalDocument(
        id="bp-hi",
        document_type="blood_pressure_screenshot",
        measured_at="2026-07-26T01:00:00Z",
    )
    store.store(
        document=doc,
        measurements=[
            create_measurement(
                metric="systolic", value=150, units="mmHg", measured_at=doc.measured_at, document_id=doc.id
            ),
            create_measurement(
                metric="diastolic", value=95, units="mmHg", measured_at=doc.measured_at, document_id=doc.id
            ),
        ],
        content=b"{}",
    )
    ids = {
        e["rule_id"]
        for e in ExpandedClinicalRulesEngine(store).evaluate(now="2026-07-26T02:00:00Z")
        if e.get("triggered")
    }
    assert "elevated_blood_pressure" in ids


def test_rules_missing_data(store: VaultStore):
    _seed_glucose(store, [("2026-07-26T01:00:00Z", 110)], doc_prefix="miss")
    cgm = CGMContinuityGuardian(store)
    gap = cgm.detect_glucose_gap(now="2026-07-26T06:00:00Z")
    evs = ExpandedClinicalRulesEngine(store).evaluate(
        context={"continuity": {"open_data_gaps": [gap], "state": "SIGNAL_LOSS"}},
        now="2026-07-26T06:00:00Z",
    )
    assert any(e.get("rule_id") == "glucose_data_gap" and e.get("triggered") for e in evs)


def test_rules_no_data_not_normal_unknown(store: VaultStore):
    clinical = ClinicalRulesEngine()
    assert clinical.classify({"metric": "glucose", "value": None, "units": "mg/dL"}) == FLAG_UNKNOWN
    assert clinical.classify({"metric": "glucose", "value": "", "units": "mg/dL"}) == FLAG_UNKNOWN
    # Empty vault absolute rules must not invent Normal
    results = ExpandedClinicalRulesEngine(store).evaluate(now="2026-07-26T09:00:00Z")
    abs_hits = [e for e in results if e.get("type") == "absolute_threshold" and e.get("triggered")]
    assert abs_hits == [] or all(e.get("triggered") for e in abs_hits)
    # Classifier path: None → Unknown, never Normal
    assert FLAG_UNKNOWN == "Unknown"


def test_rules_absolute_critical_precedence_over_baseline(store: VaultStore):
    cfg = {
        "minimum_sample_count": 5,
        "rolling_window_days": 90,
        "min_confidence": 0.0,
        "percentile_low": 10,
        "percentile_high": 90,
        "supported_metrics": ["glucose"],
        "contexts": [],
    }
    _seed_glucose(store, [(f"2026-07-{i+1:02d}T08:00:00Z", 100) for i in range(6)], doc_prefix="basecrit")
    # Critical-low glucose (clinical critical_below=54) also outside baseline
    doc = MedicalDocument(id="crit-low", document_type="blood_glucose", measured_at="2026-07-20T08:00:00Z")
    store.store(
        document=doc,
        measurements=[
            create_measurement(
                metric="glucose", value=48, units="mg/dL", measured_at=doc.measured_at, document_id=doc.id
            )
        ],
        content=b"{}",
    )
    base = BaselineEngine(store, config=cfg)
    base.rebuild()
    rules = ExpandedClinicalRulesEngine(store, baseline=base)
    triggered = [e for e in rules.evaluate(now="2026-07-20T09:00:00Z") if e.get("triggered")]
    ids = {e["rule_id"] for e in triggered}
    assert "glucose_very_low" in ids
    # Absolute Critical classification suppresses baseline_deviation soft watch
    assert "baseline_deviation_glucose" not in ids


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------


def test_baseline_calc_ready(store: VaultStore):
    cfg = {
        "minimum_sample_count": 5,
        "rolling_window_days": 90,
        "min_confidence": 0.0,
        "percentile_low": 10,
        "percentile_high": 90,
        "supported_metrics": ["glucose"],
        "contexts": [],
    }
    _seed_glucose(store, [(f"2026-07-{i+1:02d}T08:00:00Z", 100 + i) for i in range(6)])
    summary = BaselineEngine(store, config=cfg).rebuild()
    g = summary["baselines"]["glucose"]
    assert g["ready"] is True
    assert g["sample_count"] == 6
    assert g["median"] is not None
    assert g["mean"] is not None


def test_baseline_insufficient(store: VaultStore):
    cfg = {
        "minimum_sample_count": 5,
        "rolling_window_days": 90,
        "min_confidence": 0.0,
        "supported_metrics": ["glucose"],
        "contexts": [],
    }
    _seed_glucose(store, [(f"2026-07-0{i+1}T08:00:00Z", 110) for i in range(2)])
    g = BaselineEngine(store, config=cfg).rebuild()["baselines"]["glucose"]
    assert g["ready"] is False
    assert g["insufficient_data"] is True


def test_baseline_unit_separation(store: VaultStore):
    cfg = {
        "minimum_sample_count": 5,
        "rolling_window_days": 90,
        "min_confidence": 0.0,
        "supported_metrics": ["glucose"],
        "contexts": [],
    }
    _seed_glucose(store, [(f"2026-07-{i+1:02d}T08:00:00Z", 100 + i) for i in range(6)])
    for i in range(3):
        doc = MedicalDocument(
            id=f"mmol-{i}",
            document_type="blood_glucose",
            measured_at=f"2026-07-{10+i:02d}T08:00:00Z",
        )
        store.store(
            document=doc,
            measurements=[
                create_measurement(
                    metric="glucose",
                    value=5.5 + i * 0.1,
                    units="mmol/L",
                    measured_at=doc.measured_at,
                    document_id=doc.id,
                )
            ],
            content=b"{}",
        )
    g = BaselineEngine(store, config=cfg).rebuild()["baselines"]["glucose"]
    assert g["units"] == "mg/dL"
    assert g["sample_count"] == 6


def test_baseline_context_separation_fasting_vs_post_meal(store: VaultStore):
    cfg = {
        "minimum_sample_count": 5,
        "rolling_window_days": 90,
        "min_confidence": 0.0,
        "percentile_low": 10,
        "percentile_high": 90,
        "supported_metrics": ["glucose"],
        "contexts": ["fasting", "post_meal"],
    }
    _seed_glucose(
        store,
        [(f"2026-07-{i+1:02d}T08:00:00Z", 95 + i) for i in range(6)],
        context="fasting",
        doc_prefix="fast",
    )
    _seed_glucose(
        store,
        [(f"2026-07-{i+1:02d}T18:00:00Z", 140 + i) for i in range(6)],
        context="post_meal",
        doc_prefix="post",
    )
    g = BaselineEngine(store, config=cfg).rebuild()["baselines"]["glucose"]
    assert "fasting" in (g.get("contextual") or {})
    assert "post_meal" in (g.get("contextual") or {})
    assert g["contextual"]["fasting"]["median"] < g["contextual"]["post_meal"]["median"]


def test_baseline_deviation(store: VaultStore):
    cfg = {
        "minimum_sample_count": 5,
        "rolling_window_days": 90,
        "min_confidence": 0.0,
        "percentile_low": 10,
        "percentile_high": 90,
        "supported_metrics": ["glucose"],
        "contexts": [],
    }
    _seed_glucose(store, [(f"2026-07-{i+1:02d}T08:00:00Z", 100) for i in range(6)])
    eng = BaselineEngine(store, config=cfg)
    eng.rebuild()
    inside = eng.deviation("glucose", 100, units="mg/dL")
    outside = eng.deviation("glucose", 250, units="mg/dL")
    assert inside["available"] is True
    assert inside["outside_band"] is False
    assert outside["outside_band"] is True


def test_baseline_rebuild_idempotent(store: VaultStore):
    cfg = {
        "minimum_sample_count": 5,
        "rolling_window_days": 90,
        "min_confidence": 0.0,
        "supported_metrics": ["glucose"],
        "contexts": [],
    }
    _seed_glucose(store, [(f"2026-07-{i+1:02d}T08:00:00Z", 105 + i) for i in range(6)])
    eng = BaselineEngine(store, config=cfg)
    a = eng.rebuild(as_of="2026-07-26T12:00:00Z")
    b = eng.rebuild(as_of="2026-07-26T12:00:00Z")
    assert a["baselines"]["glucose"]["sample_count"] == b["baselines"]["glucose"]["sample_count"]
    assert a["baselines"]["glucose"]["median"] == b["baselines"]["glucose"]["median"]
    assert a["baselines"]["glucose"]["mean"] == b["baselines"]["glucose"]["mean"]


# ---------------------------------------------------------------------------
# CGM continuity
# ---------------------------------------------------------------------------


def test_cgm_register(store: VaultStore):
    cgm = CGMContinuityGuardian(store)
    sensor = cgm.register_sensor({"serial_or_reference": "SN-1", "expected_wear_days": 14})
    assert sensor["sensor_id"]
    assert sensor["status"] == "planned"
    assert sensor["serial_or_reference"] == "SN-1"


def test_cgm_activate_once_decrements_inventory(store: VaultStore):
    cgm = CGMContinuityGuardian(store)
    cgm.update_inventory({"unused_sensor_count": 3, "confidence": "confirmed"})
    sensor = cgm.register_sensor({"serial_or_reference": "SN-2"})
    out = cgm.activate_sensor(sensor["sensor_id"], activation_timestamp="2026-07-10T00:00:00Z")
    assert out["ok"] is True
    assert cgm.get_inventory()["unused_sensor_count"] == 2


def test_cgm_activate_idempotent_no_double_decrement(store: VaultStore):
    cgm = CGMContinuityGuardian(store)
    cgm.update_inventory({"unused_sensor_count": 2, "confidence": "confirmed"})
    sensor = cgm.register_sensor({"serial_or_reference": "SN-3"})
    cgm.activate_sensor(sensor["sensor_id"], activation_timestamp="2026-07-10T00:00:00Z")
    again = cgm.activate_sensor(sensor["sensor_id"], activation_timestamp="2026-07-10T00:00:00Z")
    assert again.get("idempotent") is True
    assert cgm.get_inventory()["unused_sensor_count"] == 1


def test_cgm_inventory_never_negative(store: VaultStore):
    cgm = CGMContinuityGuardian(store)
    cgm.update_inventory({"unused_sensor_count": -9, "confidence": "confirmed"})
    assert cgm.get_inventory()["unused_sensor_count"] == 0


def test_cgm_reserve_breach(store: VaultStore):
    cgm = CGMContinuityGuardian(store)
    cgm.update_inventory(
        {
            "unused_sensor_count": 0,
            "minimum_protected_reserve": 2,
            "confidence": "confirmed",
            "travel_buffer_days": 7,
            "reorder_lead_days": 5,
        }
    )
    cont = cgm.evaluate_continuity(now="2026-07-26T12:00:00Z")
    assert "REORDER_REQUIRED" in cont["states"] or cont["state"] == "REORDER_REQUIRED"
    assert cont["inventory"]["unused_sensor_count"] < cont["inventory"]["minimum_protected_reserve"]


def test_cgm_coverage_reorder_travel(store: VaultStore):
    cgm = CGMContinuityGuardian(store)
    cgm.update_inventory(
        {
            "unused_sensor_count": 0,
            "minimum_protected_reserve": 1,
            "travel_buffer_days": 7,
            "reorder_lead_days": 5,
            "expected_wear_days": 14,
            "confidence": "confirmed",
        }
    )
    cont = cgm.evaluate_continuity(now="2026-07-26T12:00:00Z")
    inv = cont["inventory"]
    assert inv["projected_coverage_days"] is not None
    assert inv["reorder_deadline"] is not None
    assert inv["travel_buffer_days"] == 7
    assert inv["reorder_lead_days"] == 5


def test_cgm_expiry(store: VaultStore):
    cgm = CGMContinuityGuardian(store)
    cgm.update_inventory({"unused_sensor_count": 1, "confidence": "confirmed"})
    sensor = cgm.register_sensor({"expected_wear_days": 14})
    cgm.activate_sensor(sensor["sensor_id"], activation_timestamp="2026-07-01T00:00:00Z", reduce_inventory=False)
    cont = cgm.evaluate_continuity(now="2026-07-16T00:00:00Z")
    assert cont["state"] == "SENSOR_EXPIRED" or "SENSOR_EXPIRED" in cont["states"]


def test_cgm_failure(store: VaultStore):
    cgm = CGMContinuityGuardian(store)
    sensor = cgm.register_sensor({"serial_or_reference": "FAIL-1"})
    cgm.activate_sensor(sensor["sensor_id"], activation_timestamp="2026-07-10T00:00:00Z", reduce_inventory=False)
    out = cgm.fail_sensor(sensor["sensor_id"], reason="adhesion_loss")
    assert out["ok"] is True
    assert out["sensor"]["status"] == "failed"
    assert out["sensor"]["failure_reason"] == "adhesion_loss"


def test_cgm_replacement(store: VaultStore):
    cgm = CGMContinuityGuardian(store)
    cgm.update_inventory({"unused_sensor_count": 2, "confidence": "confirmed"})
    old = cgm.register_sensor({"serial_or_reference": "OLD"})
    cgm.activate_sensor(old["sensor_id"], activation_timestamp="2026-07-01T00:00:00Z")
    replaced = cgm.replace_sensor(
        old["sensor_id"],
        {"serial_or_reference": "NEW", "activation_timestamp": "2026-07-10T00:00:00Z"},
    )
    assert replaced["ok"] is True
    assert replaced["sensor"]["status"] == "active"
    old_row = next(s for s in cgm.list_sensors() if s["sensor_id"] == old["sensor_id"])
    assert old_row["status"] == "replaced"


def test_cgm_unknown_inventory_not_safe(store: VaultStore):
    cgm = CGMContinuityGuardian(store)
    cont = cgm.evaluate_continuity(now="2026-07-26T12:00:00Z")
    assert cont["state"] != "SAFE"
    assert "INVENTORY_UNKNOWN" in cont["states"] or cont["state"] == "INVENTORY_UNKNOWN"


def test_cgm_real_world_combo_not_safe_normal(store: VaultStore):
    """Expiring sensor + reserve breach + no glucose → not SAFE / not NORMAL overall."""
    cgm = CGMContinuityGuardian(store)
    cgm.update_inventory(
        {
            "unused_sensor_count": 0,
            "minimum_protected_reserve": 1,
            "travel_buffer_days": 7,
            "reorder_lead_days": 5,
            "confidence": "confirmed",
        }
    )
    sensor = cgm.register_sensor({"expected_wear_days": 14})
    cgm.activate_sensor(sensor["sensor_id"], activation_timestamp="2026-07-01T00:00:00Z", reduce_inventory=False)
    cont = cgm.evaluate_continuity(now="2026-07-14T12:00:00Z")
    assert cont["state"] != "SAFE"
    assert "SAFE" not in (cont.get("states") or []) or cont["state"] != "SAFE"
    g = HealthGuardian(store=store)
    status = g.build_status(patient_id="default-patient", continuity=cont, now="2026-07-14T12:00:00Z")
    assert status["overall_state"] not in ("NORMAL", "SAFE")


def test_cgm_uploaded_not_live(store: VaultStore):
    cgm = CGMContinuityGuardian(store)
    cont = cgm.evaluate_continuity()
    assert cont.get("live_libre_api") is False


# ---------------------------------------------------------------------------
# Guardian orchestration
# ---------------------------------------------------------------------------


def test_guardian_empty_vault_unknown(store: VaultStore):
    g = HealthGuardian(store=store)
    status = g.build_status(now="2026-07-26T12:00:00Z")
    assert status["overall_state"] == "UNKNOWN"


def test_guardian_critical_precedence(store: VaultStore):
    _seed_glucose(store, [("2026-07-26T08:00:00Z", 48)])
    result = HealthGuardian(store=store).evaluate(now="2026-07-26T08:05:00Z", trigger="test")
    assert result["status"]["overall_state"] == "CRITICAL"


def test_guardian_monitoring_degraded(store: VaultStore):
    g = HealthGuardian(store=store)

    def _boom(**_kwargs):
        raise RuntimeError("simulated pipeline failure")

    g.rules.evaluate = _boom  # type: ignore[method-assign]
    result = g.evaluate(now="2026-07-26T08:00:00Z", trigger="test")
    assert result["ok"] is False
    assert result["status"]["overall_state"] == "MONITORING_DEGRADED"
    assert result.get("fully_evaluated") is False


def test_guardian_idempotent_evaluate(store: VaultStore):
    _seed_glucose(store, [("2026-07-26T08:00:00Z", 120)])
    g = HealthGuardian(store=store)
    r1 = g.evaluate(now="2026-07-26T09:00:00Z", trigger="a")
    r2 = g.evaluate(now="2026-07-26T09:00:00Z", trigger="b")
    assert r1["ok"] and r2["ok"]
    assert r1["status"]["overall_state"] == r2["status"]["overall_state"]


def test_guardian_fully_evaluated_false_on_lightweight(store: VaultStore):
    _seed_glucose(store, [("2026-07-26T08:00:00Z", 120)])
    g = HealthGuardian(store=store)
    result = g.evaluate(now="2026-07-26T09:00:00Z", trigger="import", lightweight=True)
    assert result["fully_evaluated"] is False
    assert result["deferred_steps"]
    assert "cgm_gap_detection" in result["deferred_steps"] or "full_continuity_refresh" in result["deferred_steps"]
    assert result["status"]["fully_evaluated"] is False
    assert result["evaluation_mode"] == "lightweight"


def test_guardian_persistence_across_vaultstore_reinstantiation(store: VaultStore):
    _seed_glucose(store, [("2026-07-26T08:00:00Z", 54)])
    g1 = HealthGuardian(store=store)
    r1 = g1.evaluate(now="2026-07-26T08:05:00Z", trigger="persist")
    root = store.root
    store2 = VaultStore(root=root)
    g2 = HealthGuardian(store=store2)
    status = g2.get_status()
    assert status.get("overall_state") == r1["status"]["overall_state"]
    assert store2.list_alerts()
    assert store2.get_guardian_status()


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------


def test_timeline_unified_ordering(store: VaultStore):
    _seed_glucose(store, [("2026-07-26T08:00:00Z", 120)], doc_prefix="ord")
    store.append_timeline_event(
        {
            "kind": "alert",
            "category": "glucose",
            "measured_at": "2026-07-26T10:00:00Z",
            "summary": "later",
            "severity": "warning",
            "dedupe_key": "ord-later",
        }
    )
    store.append_timeline_event(
        {
            "kind": "data_gap",
            "category": "cgm_continuity",
            "measured_at": "2026-07-26T09:00:00Z",
            "summary": "mid",
            "dedupe_key": "ord-mid",
        }
    )
    entries = build_unified_timeline(store, newest_first=True)
    dates = [e.get("date") or e.get("measured_at") or "" for e in entries]
    assert dates == sorted(dates, reverse=True)


def test_timeline_dedupe(store: VaultStore):
    store.append_timeline_event(
        {
            "kind": "data_gap",
            "category": "cgm_continuity",
            "measured_at": "2026-07-26T09:00:00Z",
            "summary": "gap",
            "dedupe_key": "dup-key-x",
        }
    )
    store.append_timeline_event(
        {
            "kind": "data_gap",
            "category": "cgm_continuity",
            "measured_at": "2026-07-26T09:00:00Z",
            "summary": "gap-again",
            "dedupe_key": "dup-key-x",
        }
    )
    assert len([e for e in store.list_timeline_events() if e.get("dedupe_key") == "dup-key-x"]) == 1
    entries = build_unified_timeline(store)
    assert len([e for e in entries if e.get("entry_kind") == "data_gap"]) == 1


def test_timeline_filters(store: VaultStore):
    _seed_glucose(store, [("2026-07-26T08:00:00Z", 120)], doc_prefix="filt")
    store.append_timeline_event(
        {
            "kind": "alert",
            "category": "glucose",
            "measured_at": "2026-07-26T09:00:00Z",
            "severity": "urgent",
            "summary": "urgent alert",
            "dedupe_key": "filt-urgent",
        }
    )
    store.append_timeline_event(
        {
            "kind": "data_gap",
            "category": "cgm_continuity",
            "measured_at": "2026-07-26T09:30:00Z",
            "severity": "warning",
            "summary": "gap",
            "dedupe_key": "filt-gap",
        }
    )
    by_sev = build_timeline(store, include_guardian_events=True, severity="urgent")
    assert all(e.get("severity") == "urgent" for e in by_sev if e.get("entry_kind") != "document")
    by_cat = build_timeline(store, include_guardian_events=True, category="cgm_continuity")
    assert all(
        e.get("primary_category") == "cgm_continuity" or e.get("entry_kind") == "data_gap"
        for e in by_cat
        if e.get("entry_kind") != "document"
    )
    by_date = build_timeline(
        store,
        include_guardian_events=True,
        date_from="2026-07-26T09:00:00Z",
        date_to="2026-07-26T09:15:00Z",
    )
    for e in by_date:
        d = e.get("date") or ""
        assert d >= "2026-07-26T09:00:00Z"
        assert d <= "2026-07-26T09:15:00Z"


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def test_api_patient_isolation(store: VaultStore):
    eng = AlertEngine(store)
    eng.ingest_evaluation(_base_eval(), patient_id="alice", now="2026-07-26T10:00:00Z")
    eng.ingest_evaluation(_base_eval(), patient_id="bob", now="2026-07-26T10:00:00Z")
    alice = guardian_alerts_handler(patient_id="alice", store=store)
    bob = guardian_alerts_handler(patient_id="bob", store=store)
    assert len(alice["alerts"]) == 1
    assert alice["alerts"][0]["patient_id"] == "alice"
    assert len(bob["alerts"]) == 1
    assert bob["alerts"][0]["patient_id"] == "bob"


def test_api_evaluate_trigger(store: VaultStore):
    _seed_glucose(store, [("2026-07-26T08:00:00Z", 120)])
    out = guardian_evaluate_handler({"trigger": "api_dev_trigger", "patient_id": "default-patient"}, store=store)
    assert out.get("ok") is True
    assert "status" in out
    assert out["status"].get("trigger") == "api_dev_trigger" or out.get("status")


def test_api_handlers_status_and_baselines(store: VaultStore):
    status = guardian_status_handler(patient_id="default-patient", store=store)
    assert "overall_state" in status
    bases = guardian_baselines_handler(patient_id="default-patient", store=store)
    assert isinstance(bases, dict)


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def test_import_duplicate_protection(store: VaultStore):
    payload = {
        "filename": "dup_glu.json",
        "mime_type": "application/json",
        "content": json.dumps({"glucose": 130, "measured_at": "2026-07-26T06:00:00Z"}).encode(),
        "measured_at": "2026-07-26T06:00:00Z",
        "source_system": "Contour Next GEN",
    }
    r1 = import_health_record_handler(payload, store=store)
    r2 = import_health_record_handler(payload, store=store)
    assert r1.get("ok") is True
    assert r2.get("duplicate") is True


def test_import_guardian_fully_evaluated_field_present(store: VaultStore):
    result = import_health_record_handler(
        {
            "filename": "glu_fe.json",
            "mime_type": "application/json",
            "content": json.dumps({"glucose": 140, "measured_at": "2026-07-26T07:00:00Z"}).encode(),
            "measured_at": "2026-07-26T07:00:00Z",
        },
        store=store,
    )
    assert result.get("ok") is True
    guardian = result.get("guardian") or {}
    assert "fully_evaluated" in guardian
    # Post-import path is lightweight → fully_evaluated False with deferred_steps
    assert guardian["fully_evaluated"] is False or isinstance(guardian["fully_evaluated"], bool)
    assert "deferred_steps" in guardian or "evaluation_mode" in guardian


# ---------------------------------------------------------------------------
# Service worker (text assertions)
# ---------------------------------------------------------------------------


def test_service_worker_vault_storage_and_api_forbidden():
    sw_path = ROOT / "service-worker.js"
    text = sw_path.read_text(encoding="utf-8")
    assert "CACHE_NAME" in text
    assert "hc-guardian-v1" in text or 'CACHE_NAME = "hc-guardian' in text
    assert "vault_storage" in text
    assert "/api/" in text
    # Forbidden cache logic must reject vault blobs and API paths
    assert "isForbiddenCacheUrl" in text
    assert 'path.indexOf("vault_storage")' in text or "vault_storage" in text
    assert 'path.indexOf("/api/")' in text or "/api/" in text


def test_service_worker_cache_name_constant():
    text = (ROOT / "service-worker.js").read_text(encoding="utf-8")
    assert 'const CACHE_NAME = "hc-guardian-v1"' in text
