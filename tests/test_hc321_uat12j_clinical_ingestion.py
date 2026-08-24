"""HC321-UAT12J — clinical record ingestion and semantic normalization."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.health_vault.api import create_health_vault_app
from backend.health_vault.clinical_semantics import classify_clinical_observation
from backend.health_vault.doctor_visit import DoctorVisitMode
from backend.health_vault.event_bus import EventBus
from backend.health_vault.health_intelligence import HealthIntelligenceEngine
from backend.health_vault.import_pipeline import ImportPipeline
from backend.health_vault.metric_normalization import (
    MONITORING_TREND_METRICS,
    TREND_METRICS,
    canonicalize_metric,
    normalize_measurement,
)
from backend.health_vault.models import create_measurement
from backend.health_vault.parser_registry import ParserRegistry
from backend.health_vault.parsers import register_builtin_parsers
from backend.health_vault.records_service import RecordsService
from backend.health_vault.trend_engine import TrendEngine
from backend.health_vault.unit_conversion import (
    UNIT_NORMALIZATION_REQUIRES_VERIFICATION,
    apply_display_units,
)
from backend.health_vault.vault_store import VaultStore

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "brantford_general_er_2026-08-23.json"


@pytest.fixture
def store(tmp_path: Path) -> VaultStore:
    return VaultStore(root=tmp_path / "vault", encryption_key=b"J" * 32)


@pytest.fixture
def pipeline(store: VaultStore) -> ImportPipeline:
    registry = ParserRegistry()
    register_builtin_parsers(registry)
    return ImportPipeline(store=store, registry=registry, bus=EventBus())


def _import_brantford(pipeline: ImportPipeline, patient_id: str = "patient-A") -> dict:
    payload = FIXTURE.read_bytes()
    return pipeline.run(
        {
            "patient_id": patient_id,
            "content": payload,
            "filename": "brantford_general_er_2026-08-23.json",
            "mime_type": "application/json",
            "document_type": "hospital_report",
            "source_system": "brantford_general_hospital",
            "acquisition_method": "manual_upload",
            "provenance": "original_document_verified",
            "measured_at": "2026-08-23T18:22:00Z",
        }
    )


def _metrics(result: dict) -> dict[str, dict]:
    return {row["metric"]: row for row in result.get("measurements") or []}


def test_semantic_classes_are_not_collapsed():
    assert canonicalize_metric("glucose_fasting") == "glucose_fasting"
    assert canonicalize_metric("glucose_random") == "glucose_random"
    assert canonicalize_metric("cgm_glucose") == "glucose_cgm_interstitial"
    assert canonicalize_metric("fasting_glucose") != canonicalize_metric("random_glucose")
    fasting = classify_clinical_observation(metric="glucose", context="fasting")
    random = classify_clinical_observation(metric="glucose", context="random")
    cgm = classify_clinical_observation(metric="glucose", document_type="libre_cgm_report")
    assert fasting["observation_class"] == "glucose_fasting"
    assert random["observation_class"] == "glucose_random"
    assert cgm["observation_class"] == "glucose_cgm_interstitial"
    assert "glucose" in TREND_METRICS
    assert "glucose_random" in TREND_METRICS
    assert "glucose_fasting" in TREND_METRICS
    assert "glucose_cgm_interstitial" in TREND_METRICS
    assert "glucose_random" not in MONITORING_TREND_METRICS


def test_canonical_unit_conversion_and_no_reconversion():
    first = normalize_measurement(
        {"metric": "glucose", "value": 6.2, "units": "mmol/L", "context": "random"}
    )
    assert first["metric"] == "glucose_random"
    assert first["original_value"] == 6.2
    assert first["original_units"] == "mmol/L"
    assert first["units"] == "mg/dL"
    assert first["value"] == pytest.approx(6.2 * 18.0182, rel=1e-6)
    second = normalize_measurement(first)
    assert second["value"] == pytest.approx(first["value"])
    assert second["original_value"] == 6.2
    creat = normalize_measurement(
        {"metric": "creatinine", "value": 1.0, "units": "mg/dL", "specimen": "serum"}
    )
    assert creat["metric"] == "creatinine_serum"
    assert creat["units"] == "umol/L"
    assert creat["value"] == pytest.approx(88.4, rel=1e-6)
    assert creat["original_value"] == 1.0


def test_ambiguous_units_are_not_auto_converted():
    missing = normalize_measurement({"metric": "glucose", "value": 6.2, "context": "random"})
    assert missing["unit_compatible"] is False
    assert missing["conversion_flag"] == UNIT_NORMALIZATION_REQUIRES_VERIFICATION
    assert missing["original_value"] == 6.2
    stones = normalize_measurement({"metric": "glucose", "value": 6.0, "units": "stones"})
    assert stones["unit_compatible"] is False


def test_regional_display_and_reference_range_conversion():
    stored = normalize_measurement(
        {
            "metric": "glucose",
            "value": 6.2,
            "units": "mmol/L",
            "context": "random",
            "reference_range": "3.6-11.1",
        }
    )
    ca = apply_display_units(stored, region="CA")
    us = apply_display_units(stored, region="US")
    assert ca["display_units"] == "mmol/L"
    assert ca["display_value"] == pytest.approx(6.2, abs=0.05)
    assert us["display_units"] == "mg/dL"
    assert us["display_value"] == pytest.approx(stored["value"], abs=0.6)
    assert "65" in str(us["display_reference_range"]) or "64" in str(us["display_reference_range"])
    assert stored["original_value"] == 6.2
    flipped = apply_display_units(stored, region="US")
    assert flipped["original_value"] == 6.2
    again = apply_display_units(flipped, region="CA")
    assert again["display_units"] == "mmol/L"
    assert again["original_units"] == "mmol/L"


def test_brantford_fixture_import_and_downstream(pipeline: ImportPipeline, store: VaultStore):
    result = _import_brantford(pipeline, "patient-A")
    assert result["ok"] is True
    doc = result["document"]
    assert doc["patient_id"] == "patient-A"
    assert doc["original_filename"] == "brantford_general_er_2026-08-23.json"
    by_metric = _metrics(result)
    for required in (
        "hemoglobin",
        "hematocrit",
        "rbc",
        "wbc",
        "neutrophils",
        "creatinine_serum",
        "egfr",
        "urea",
        "glucose_random",
        "sodium",
        "potassium",
        "calcium_total",
        "magnesium",
        "albumin_serum",
        "protein_total_serum",
        "troponin_i_hs",
        "inr",
    ):
        assert required in by_metric, required
    glucose = by_metric["glucose_random"]
    assert glucose["context"] == "random"
    assert glucose["original_units"] == "mmol/L"
    assert glucose["original_value"] == 6.2
    assert glucose["metric"] != "glucose_fasting"
    assert "glucose_fasting" not in by_metric
    assert "glucose_cgm_interstitial" not in by_metric
    creat = by_metric["creatinine_serum"]
    assert creat["specimen"] == "serum"
    assert creat["source_facility"] == "Brantford General Hospital"
    assert len(store.list_documents()) == 1
    assert len(store.list_measurements()) >= 17

    trends = store.get_trends(patient_id="patient-A")
    assert "egfr" in trends
    assert "glucose_random" in trends
    assert "glucose_fasting" not in trends
    assert trends["egfr"].get("provenance") == "clinical"
    assert trends["glucose_random"].get("data_plane") == "clinical"

    intel = HealthIntelligenceEngine(store)
    observations = intel.get_patient_observations("patient-A")
    facts = " ".join(str(row.get("fact") or "") for row in observations)
    cats = {row.get("category") for row in observations}
    assert "renal" in cats
    assert "glycemic" in cats
    assert "eGFR" in facts or "egfr" in facts.lower() or "creatinine" in facts.lower()
    assert "random glucose" in facts.lower()
    assert "fasting glucose" not in facts.lower()
    assert "No renal measurements found in the vault." not in facts
    assert "No glycemic measurements found in the vault." not in facts

    report = DoctorVisitMode(store).generate("patient-A")
    assert report["kidney_trend"] != "n/a"
    assert "random" in report["diabetes_trend"].lower()
    assert "n/a · HbA1c" not in report["diabetes_trend"] or "random" in report["diabetes_trend"].lower()

    records = RecordsService(store)
    payload = records.consumer_records_payload("patient-A", surface="clinical_document")
    assert payload["vault_record_count"] == 1
    assert payload["records"]
    assert payload["records"][0]["document_id"] == doc["id"]
    device = records.consumer_records_payload("patient-A", surface="device_data")
    assert device["records"] == []
    assert device["device_data"]["record_count"] == 0
    search = records.consumer_records_payload("patient-A", q="creatinine")
    assert search["records"]
    other = records.list_records("patient-B")
    assert other == []


def test_like_for_like_trends_do_not_merge_glucose_classes(store: VaultStore):
    data = store._read_index()
    data["documents"].extend(
        [
            {
                "id": "fast",
                "patient_id": "patient-A",
                "status": "imported",
                "measured_at": "2026-08-01T08:00:00Z",
                "date_confidence": 1.0,
            },
            {
                "id": "rand",
                "patient_id": "patient-A",
                "status": "imported",
                "measured_at": "2026-08-01T14:00:00Z",
                "date_confidence": 1.0,
            },
            {
                "id": "cgm",
                "patient_id": "patient-A",
                "status": "imported",
                "measured_at": "2026-08-01T14:05:00Z",
                "date_confidence": 1.0,
            },
        ]
    )
    data["measurements"].extend(
        [
            create_measurement(document_id="fast", metric="glucose_fasting", value=95, units="mg/dL", measured_at="2026-08-01T08:00:00Z").to_dict(),
            create_measurement(document_id="rand", metric="glucose_random", value=140, units="mg/dL", measured_at="2026-08-01T14:00:00Z").to_dict(),
            create_measurement(document_id="cgm", metric="glucose_cgm_interstitial", value=128, units="mg/dL", measured_at="2026-08-01T14:05:00Z").to_dict(),
        ]
    )
    store._write_index(data)
    trends = TrendEngine(store).recompute("patient-A")
    assert trends["glucose_fasting"]["latest"] == pytest.approx(95)
    assert trends["glucose_random"]["latest"] == pytest.approx(140)
    assert trends["glucose_cgm_interstitial"]["latest"] == pytest.approx(128)
    assert "glucose" not in trends


def test_device_data_and_api_compat_preserved(store: VaultStore):
    data = store._read_index()
    data["documents"].append(
        {
            "id": "hc-hr",
            "patient_id": "patient-A",
            "document_type": "continuous_monitoring_observation",
            "source_system": "health_connect_companion",
            "original_filename": "health_connect_heart_rate_abcd.json",
            "status": "imported",
            "measured_at": "2026-08-18T10:00:00Z",
        }
    )
    data["measurements"].append(
        create_measurement(document_id="hc-hr", metric="heart_rate", value=72, units="bpm").to_dict()
    )
    store._write_index(data)
    client = TestClient(create_health_vault_app(store, test_users={"patient-A": "correct"}))
    token = client.post("/api/auth/login", json={"patient_id": "patient-A", "password": "correct"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    listing = client.get("/api/records", headers=headers).json()
    assert listing["vault_record_count"] == 1
    clinical = client.get("/api/records?surface=clinical_document", headers=headers).json()
    assert clinical["records"] == []
    device = client.get("/api/records?surface=device_data", headers=headers).json()
    assert device["device_data"]["record_count"] == 1
    prefs = client.post(
        "/api/dashboard/preferences",
        headers=headers,
        json={"theme": "light", "reporting_region": "CA"},
    )
    assert prefs.status_code == 200
    assert prefs.json()["reporting_region"] == "CA"


def test_ui_contract_and_lineage_assets():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "js/health_vault/dashboard.js").read_text(encoding="utf-8")
    sw = (ROOT / "service-worker.js").read_text(encoding="utf-8")
    snap = (ROOT / "js/health_vault/health_snapshot.js").read_text(encoding="utf-8")
    surfaces = (ROOT / "js/health_vault/consumer_surfaces.js").read_text(encoding="utf-8")
    assert 'id="config_reporting_region"' in html
    assert "reporting_region" in js
    assert 'CACHE_REVISION = "hc323a"' in sw
    assert "service-worker.js?v=hc323a" in html
    assert "compactTimelineEntries" in surfaces
    assert "openFiltered" in snap
    assert (ROOT / "tests" / "test_hc322a_consumer_screenshot_policy.py").exists()
