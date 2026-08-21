"""
HC-201I — Executive Health Dashboard / briefing engine tests.

Fictional fixtures only. Observational — no diagnostic claims.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from backend.health_vault.executive_briefing import (
    DISCLAIMER,
    ExecutiveHealthBriefingEngine,
    load_executive_dashboard_config,
)
from backend.health_vault.models import Measurement, MedicalDocument
from backend.health_vault.vault_store import VaultStore


def _store(tmp: Path) -> VaultStore:
    return VaultStore(root=tmp)


def _seed_full(store: VaultStore) -> None:
    ecg = MedicalDocument(
        patient_id="default-patient",
        document_type="samsung_health_ecg",
        source_system="Galaxy Watch5 Pro / Samsung Health Monitor",
        original_filename="ecg_2026-07-19.pdf",
        measured_at="2026-07-19T12:00:00Z",
        primary_category="ecg_cardiology",
        classification_confidence=0.9,
        provenance="wearable_pdf",
        interpretation="Sinus rhythm",
        tags=["ecg"],
        sha256="a" * 64,
    )
    sleep = MedicalDocument(
        patient_id="default-patient",
        document_type="samsung_sleep",
        source_system="Samsung Health",
        original_filename="sleep_2026-07-19.png",
        measured_at="2026-07-19T08:00:00Z",
        primary_category="sleep",
        classification_confidence=0.88,
        provenance="wearable_screenshot",
        interpretation="Awake until approximately 4:00 a.m.; late bedtime short night.",
        tags=["late bedtime"],
        sha256="b" * 64,
    )
    bp = MedicalDocument(
        patient_id="default-patient",
        document_type="bp_screenshot",
        source_system="home_monitor",
        original_filename="bp.png",
        measured_at="2026-07-18T10:00:00Z",
        primary_category="blood_pressure",
        classification_confidence=0.91,
        provenance="wearable_screenshot",
        sha256="c" * 64,
    )
    kidney = MedicalDocument(
        patient_id="default-patient",
        document_type="lab_panel",
        source_system="LifeLabs",
        original_filename="kidney.pdf",
        measured_at="2026-04-01T00:00:00Z",
        primary_category="kidney_renal",
        secondary_categories=["laboratory_report"],
        classification_confidence=0.95,
        provenance="historical_summary",
        requires_review=True,
        sha256="d" * 64,
    )

    store.store(
        document=ecg,
        measurements=[
            Measurement(metric="average_hr", value=60, units="bpm", measured_at="2026-07-19T12:00:00Z", confidence=0.9),
            Measurement(metric="rhythm", value="Sinus rhythm", units=None, measured_at="2026-07-19T12:00:00Z", confidence=0.9),
        ],
        content=b"ecg",
    )
    store.store(
        document=sleep,
        measurements=[
            Measurement(metric="sleep_duration", value=240, units="minutes", measured_at="2026-07-19T08:00:00Z", confidence=0.9),
            Measurement(metric="sleep_score", value=55, units="score", measured_at="2026-07-19T08:00:00Z", confidence=0.9),
        ],
        content=b"sleep",
    )
    store.store(
        document=bp,
        measurements=[
            Measurement(metric="systolic_bp", value=128, units="mmHg", measured_at="2026-07-18T10:00:00Z", confidence=0.9),
            Measurement(metric="diastolic_bp", value=78, units="mmHg", measured_at="2026-07-18T10:00:00Z", confidence=0.9),
        ],
        content=b"bp",
    )
    store.store(
        document=kidney,
        measurements=[
            Measurement(metric="egfr", value=52, units="mL/min/1.73m2", measured_at="2026-04-01T00:00:00Z", confidence=0.9),
            Measurement(metric="creatinine", value=120, units="umol/L", measured_at="2026-04-01T00:00:00Z", confidence=0.9),
        ],
        content=b"kidney",
    )

    store.update_profile(
        {
            "medications": ["Metformin 500mg", "Amlodipine uncertain dose"],
            "diagnoses": ["Type 2 diabetes"],
        }
    )
    store.record_batch_audit(
        {
            "batch_id": "batch-demo",
            "selected_count": 4,
            "imported_count": 4,
            "duplicate_count": 0,
            "failed_count": 0,
            "category_counts": {"sleep": 1, "blood_pressure": 1, "ecg_cardiology": 1, "kidney_renal": 1},
            "earliest_measured_at": "2026-04-01T00:00:00Z",
            "latest_measured_at": "2026-07-19T12:00:00Z",
            "completed_at": "2026-07-19T15:00:00Z",
            "confirmed_by_user": True,
            "grouped_report_count": 0,
        }
    )


def test_config_loads():
    cfg = load_executive_dashboard_config()
    assert cfg["schema_version"].startswith("hc.executive_dashboard")
    assert any(d["id"] == "heart" for d in cfg["domains"])


def test_briefing_engine_structure_and_disclaimer():
    with tempfile.TemporaryDirectory() as td:
        store = _store(Path(td))
        _seed_full(store)
        engine = ExecutiveHealthBriefingEngine(store)
        brief = engine.generate(trend_window="30d")
        assert brief["schema_version"].startswith("hc.executive_briefing")
        assert brief["disclaimer"]
        assert "diagnosis" in brief["disclaimer"].lower() or "not a diagnosis" in DISCLAIMER.lower()
        assert brief["observational_only"] is True
        assert brief["diagnostic"] is False
        assert brief["prescriptive"] is False
        assert "domain_summaries" in brief
        assert "attention_items" in brief
        assert "monitoring_actions" in brief
        assert "recent_imports" in brief
        assert brief["data_status"] in {
            "Current",
            "Partially current",
            "Needs record updates",
            "Limited data",
        }


def test_heart_ecg_july19_fields():
    with tempfile.TemporaryDirectory() as td:
        store = _store(Path(td))
        _seed_full(store)
        heart = ExecutiveHealthBriefingEngine(store).generate()["domain_summaries"]["heart"]
        detail = heart["heart_detail"]
        assert detail is not None
        assert "Sinus" in str(detail.get("rhythm") or detail.get("ecg_classification") or "")
        assert detail.get("average_heart_rate") == 60
        assert "none" in str(detail.get("symptoms") or "").lower()
        assert "Watch5" in str(detail.get("source_device") or "") or "Samsung" in str(
            detail.get("source_device") or ""
        )
        assert "do not exclude" in str(detail.get("wearable_note") or "").lower()


def test_sleep_single_night_context_not_chronic():
    with tempfile.TemporaryDirectory() as td:
        store = _store(Path(td))
        _seed_full(store)
        sleep = ExecutiveHealthBriefingEngine(store).generate()["domain_summaries"]["sleep"]
        note = (sleep.get("sleep_context") or {}).get("contextual_note") or ""
        assert "chronic deterioration" in note.lower()
        assert sleep.get("status_label") != "Worsening" or sleep.get("sleep_context")


def test_bp_pair_and_kidney_provenance():
    with tempfile.TemporaryDirectory() as td:
        store = _store(Path(td))
        _seed_full(store)
        brief = ExecutiveHealthBriefingEngine(store).generate(as_of="2026-07-19T15:00:00Z")
        bp = brief["domain_summaries"]["blood_pressure"]
        assert bp.get("bp_display") and "/" in bp["bp_display"]
        kidney = brief["domain_summaries"]["kidney"]
        assert "historical" in str(kidney.get("verification_status") or "").lower() or kidney.get(
            "requires_review"
        )


def test_attention_and_monitoring_actions():
    with tempfile.TemporaryDirectory() as td:
        store = _store(Path(td))
        _seed_full(store)
        brief = ExecutiveHealthBriefingEngine(store).generate()
        codes = {a["code"] for a in brief["attention_items"]}
        assert "requires_review" in codes or "source_document_required" in codes
        prompts = " ".join(a["prompt"] for a in brief["monitoring_actions"]).lower()
        assert "not a medical prescription" in " ".join(
            a.get("note", "") for a in brief["monitoring_actions"]
        ).lower() or "upload" in prompts or "confirm" in prompts
        assert all(a.get("prescriptive") is False for a in brief["monitoring_actions"] if "prescriptive" in a)


def test_recent_imports_and_medications():
    with tempfile.TemporaryDirectory() as td:
        store = _store(Path(td))
        _seed_full(store)
        brief = ExecutiveHealthBriefingEngine(store).generate()
        assert brief["recent_imports"]
        assert brief["recent_imports"][0]["batch_id"] == "batch-demo"
        meds = brief["medications_summary"]
        assert meds["uncertain_medication_statuses"]
        assert "interaction" in meds["note"].lower() or "does not infer" in meds["note"].lower()


def test_insufficient_data_empty_store():
    with tempfile.TemporaryDirectory() as td:
        store = _store(Path(td))
        brief = ExecutiveHealthBriefingEngine(store).generate()
        assert brief["data_status"] == "Limited data"
        assert brief["domain_summaries"]["heart"]["status_label"] == "Insufficient data"


def test_trend_window_and_duplicate_exclusion():
    with tempfile.TemporaryDirectory() as td:
        store = _store(Path(td))
        _seed_full(store)
        # duplicate doc
        dup = MedicalDocument(
            primary_category="blood_pressure",
            measured_at="2026-07-17T00:00:00Z",
            duplicate_of="x",
            status="imported",
            classification_confidence=0.9,
            sha256="e" * 64,
        )
        store.store(
            document=dup,
            measurements=[
                Measurement(
                    metric="systolic_bp",
                    value=200,
                    units="mmHg",
                    measured_at="2026-07-17T00:00:00Z",
                )
            ],
            content=b"dup",
        )
        engine = ExecutiveHealthBriefingEngine(store)
        brief = engine.generate(trend_window="7d")
        assert brief["trend_window"] == "7d"
        # Duplicate should not become sole latest for BP when other BP exists
        bp = brief["domain_summaries"]["blood_pressure"]
        assert bp["recent_record_count"] >= 1


def test_printable_and_path_redaction_api():
    with tempfile.TemporaryDirectory() as td:
        store = _store(Path(td))
        _seed_full(store)
        engine = ExecutiveHealthBriefingEngine(store)
        printable = engine.printable_summary()
        assert printable["title"].startswith("HealthChecker+")
        assert printable["disclaimer"]
        blob = str(printable).lower()
        assert "c:\\" not in blob
        assert "/home/" not in blob

    app_factory = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from backend.health_vault.api import create_health_vault_app

    with tempfile.TemporaryDirectory() as td:
        store = _store(Path(td))
        _seed_full(store)
        app = create_health_vault_app(store=store)
        assert app is not None
        client = TestClient(app)
        res = client.get("/api/health-vault/executive-briefing")
        assert res.status_code == 200
        body = res.json()
        assert body["observational_only"] is True
        assert "diagnosis" in body["disclaimer"].lower() or "not a diagnosis" in body["disclaimer"].lower()
        assert "password" not in body
        print_res = client.get("/api/health-vault/executive-briefing/print")
        assert print_res.status_code == 200
        assert print_res.json()["observational_only"] is True


def test_ui_structure_markers_present():
    html = Path("index.html").read_text(encoding="utf-8")
    assert 'id="exec_health_dashboard"' in html
    assert "executive_dashboard.js" in html
    js = Path("js/health_vault/executive_dashboard.js").read_text(encoding="utf-8")
    assert "HCExecutiveDashboard" in js
    assert "not a diagnosis" in js.lower()
    assert "Items Requiring Attention" in js or "attention" in js.lower()


def test_non_diagnostic_language_in_engine_source():
    src = Path("backend/health_vault/executive_briefing.py").read_text(encoding="utf-8")
    assert "Does not diagnose" in src or "does not diagnose" in src.lower()
    assert "prescribe" in src.lower()
