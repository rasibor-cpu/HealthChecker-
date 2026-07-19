"""
HC-201 / HC-201A regression tests — Health Vault & medical record ingestion.

Covers vault storage, import engine, measurement pipeline, timeline, trends,
integrity, and non-regression of Foot Pain engine.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.intelligence.foot_pain_engine import FootPainEngine
from backend.health_vault.api import import_health_record_handler
from backend.health_vault.doctor_visit import DoctorVisitMode
from backend.health_vault.import_service import ImportService
from backend.health_vault.models import (
    MedicalDocument,
    classify_document_type,
    create_measurement,
    register_metric,
)
from backend.health_vault.parser_registry import ParserRegistry, get_default_registry
from backend.health_vault.parsers import (
    AIAssistedParser,
    BloodPressureParser,
    GalaxyWatchParser,
    HospitalReportParser,
    LibreParser,
    LifeLabsParser,
    SamsungHealthParser,
    register_builtin_parsers,
)
from backend.health_vault.timeline import build_timeline
from backend.health_vault.trend_engine import TrendEngine
from backend.health_vault.vault_store import VaultStore


@pytest.fixture()
def store(tmp_path: Path) -> VaultStore:
    return VaultStore(root=tmp_path / "vault")


@pytest.fixture()
def service(store: VaultStore) -> ImportService:
    reg = ParserRegistry()
    register_builtin_parsers(reg)
    return ImportService(store=store, registry=reg)


def test_medical_document_model_fields():
    doc = MedicalDocument(
        patient_id="p1",
        document_type="laboratory_pdf",
        source_system="lifelabs",
        acquisition_method="manual_upload",
        original_filename="labs.pdf",
        sha256="abc",
        tags=["lab"],
    )
    d = doc.to_dict()
    for key in (
        "id",
        "patient_id",
        "document_type",
        "source_system",
        "acquisition_method",
        "original_filename",
        "storage_uri",
        "sha256",
        "imported_at",
        "measured_at",
        "parser_version",
        "parser_confidence",
        "status",
        "tags",
    ):
        assert key in d
    assert d["fhir_resource"] == "DocumentReference"


def test_measurement_model_extensible():
    m = create_measurement(metric="glucose", value=110, document_id="d1")
    assert m.category == "Glucose"
    assert m.units == "mg/dL"
    assert m.fhir_resource == "Observation"
    register_metric("custom_metric", category="Custom", units="u")
    m2 = create_measurement(metric="custom_metric", value=1)
    assert m2.category == "Custom"


def test_classify_document_types():
    assert classify_document_type("Samsung_ECG.pdf", "application/pdf") == "samsung_health_ecg"
    assert classify_document_type("sleep_report.json", "application/json") == "samsung_health_sleep"
    assert classify_document_type("libre_cgm.pdf", "application/pdf") == "libre_cgm_report"
    assert classify_document_type("bp_120_80.png", "image/png") == "blood_pressure_screenshot"
    assert classify_document_type("lifelabs_blood.pdf", "application/pdf") == "laboratory_pdf"


def test_parser_registry_resolves_builtin():
    reg = ParserRegistry()
    register_builtin_parsers(reg)
    names = {p.name for p in reg.list()}
    assert {
        "SamsungHealthParser",
        "GalaxyWatchParser",
        "LifeLabsParser",
        "LibreParser",
        "BloodPressureParser",
        "HospitalReportParser",
        "AIAssistedParser",
    } <= names
    p = reg.resolve({"document_type": "laboratory_pdf", "filename": "lifelabs.pdf", "text": ""})
    assert p is not None
    assert p.name == "LifeLabsParser"


def test_import_engine_json_and_checksum(service: ImportService, store: VaultStore):
    payload = {
        "content": json.dumps({"glucose": 140, "systolic": 128, "diastolic": 82, "egfr": 55}).encode(
            "utf-8"
        ),
        "filename": "readings.json",
        "mime_type": "application/json",
        "document_type": "json_measurements",
    }
    result = service.import_health_record(payload)
    assert result["ok"] is True
    assert result["sha256"]
    assert result["document"]["sha256"] == result["sha256"]
    assert len(result["measurements"]) >= 3
    assert store.verify_integrity()["ok"] is True


def test_never_overwrite_documents(service: ImportService, store: VaultStore):
    content = b'{"glucose": 100}'
    r1 = service.import_health_record(
        {"content": content, "filename": "a.json", "mime_type": "application/json"}
    )
    r2 = service.import_health_record(
        {"content": content, "filename": "a-copy.json", "mime_type": "application/json"}
    )
    assert r1["ok"] is True
    assert r2.get("duplicate") is True or r2.get("status") == "Duplicate"
    # HC-201C: duplicates are not re-imported; original retained
    assert len(store.list_documents()) == 1
    assert r2.get("original_document_id") == r1["document"]["id"]
    path = store.resolve_storage_path(r1["document"].get("storage_uri"), r1["document"]["id"])
    assert path is not None and path.exists()
    assert path.read_bytes() == content


def test_refuse_id_overwrite(store: VaultStore):
    doc = MedicalDocument(id="fixed-id", original_filename="x.json")
    store.store(document=doc, measurements=[], content=b"one")
    with pytest.raises(ValueError, match="refuse overwrite"):
        store.store(
            document=MedicalDocument(id="fixed-id", original_filename="y.json"),
            measurements=[],
            content=b"two",
        )


def test_ai_assisted_ingestion_path(service: ImportService):
    result = service.import_health_record(
        {
            "document": "external AI packet",
            "filename": "ai.json",
            "mime_type": "application/json",
            "acquisition_method": "external_ai",
            "extracted_measurements": [
                {"metric": "glucose", "value": 118, "units": "mg/dL"},
                {"metric": "hba1c", "value": 6.4},
            ],
            "interpretation": "Stable glycemic control",
            "confidence": 0.91,
        }
    )
    assert result["ok"] is True
    assert result["parser"]["name"] == "AIAssistedParser"
    assert result["document"]["interpretation"] == "Stable glycemic control"
    conf = result["confidence"]
    if isinstance(conf, dict):
        assert conf["extraction_confidence"] >= 0.9 or conf["overall_confidence"] > 0
    else:
        assert abs(float(conf) - 0.91) < 1e-6
    assert len(result["measurements"]) == 2


def test_api_handler_matches_service(tmp_path: Path):
    store = VaultStore(root=tmp_path / "api-vault")
    out = import_health_record_handler(
        {
            "content": b'{"systolic": 120, "diastolic": 70}',
            "filename": "bp.json",
            "mime_type": "application/json",
            "document_type": "blood_pressure_screenshot",
        },
        store=store,
    )
    assert out["ok"] is True
    assert out["document"]["document_type"] == "blood_pressure_screenshot"


def test_timeline_and_trends(service: ImportService, store: VaultStore):
    for g in (100, 110, 125):
        service.import_health_record(
            {
                "content": json.dumps({"glucose": g, "measured_at": f"2026-0{g-99}-01T00:00:00Z"}).encode(),
                "filename": f"g{g}.json",
                "mime_type": "application/json",
            }
        )
    trends = TrendEngine(store).recompute()
    assert "glucose" in trends
    assert trends["glucose"]["direction"] in {"worsening", "rising", "stable", "improving"}
    # Rising glucose → worsening for diabetes metric
    assert trends["glucose"]["direction"] == "worsening"
    timeline = build_timeline(store)
    assert len(timeline) == 3
    assert "trend_impact" in timeline[0]
    assert timeline[0]["original_link"]


def test_doctor_visit_mode(service: ImportService, store: VaultStore):
    store.update_profile({"diagnoses": ["T2DM"], "medications": ["Metformin"]})
    service.import_health_record(
        {
            "content": b'{"egfr": 50, "glucose": 150}',
            "filename": "labs.json",
            "mime_type": "application/json",
        }
    )
    report = DoctorVisitMode(store).generate()
    assert report["current_diagnoses"] == ["T2DM"]
    assert report["current_medications"] == ["Metformin"]
    assert "kidney_trend" in report
    assert "blood_pressure_trend" in report
    assert "sleep_trend" in report
    assert "diabetes_trend" in report
    assert "health_timeline" in report
    assert "DocumentReference" in report["fhir_bundle_hint"]


def test_blood_pressure_text_parser():
    p = BloodPressureParser()
    assert p.can_parse({"filename": "bp_screenshot.png", "document_type": "blood_pressure_screenshot", "text": ""})
    out = p.parse({"document_id": "d", "text": "BP 142/91", "filename": "x.png"})
    metrics = {m.metric: m.value for m in out["measurements"]}
    assert metrics["systolic"] == 142
    assert metrics["diastolic"] == 91


def test_lifelabs_text_extraction():
    p = LifeLabsParser()
    out = p.parse(
        {
            "document_id": "d",
            "document_type": "laboratory_pdf",
            "text": "eGFR 48 Creatinine 140 HbA1c 7.1",
            "filename": "lifelabs.pdf",
        }
    )
    metrics = {m.metric for m in out["measurements"]}
    assert {"egfr", "creatinine", "hba1c"} <= metrics


def test_audit_and_import_history(service: ImportService, store: VaultStore):
    service.import_health_record(
        {"content": b'{"weight": 80}', "filename": "w.json", "mime_type": "application/json"}
    )
    assert len(store.imports()) >= 1
    assert any(a["action"] == "document_imported" for a in store.audit())


def test_storage_integrity_ok(service: ImportService, store: VaultStore):
    service.import_health_record(
        {"content": b'{"bmi": 27}', "filename": "bmi.json", "mime_type": "application/json"}
    )
    assert store.verify_integrity()["ok"] is True


def test_foot_pain_engine_non_regression():
    """Existing Foot Pain Analysis must remain fully functional."""
    engine = FootPainEngine()
    gout = engine.evaluate(
        pain_location="big_toe",
        swelling=False,
        symmetry="one_side",
        onset_speed="sudden",
        recent_med_change=False,
        glucose=120,
        kidney_status="normal",
        symptoms=["sharp_pain", "joint_focus"],
    )
    assert gout["likely_cause"] == "gout"
    med = engine.evaluate(
        pain_location="ankle",
        swelling=True,
        symmetry="asymmetrical",
        onset_speed="gradual",
        recent_med_change=True,
        glucose=110,
        kidney_status="normal",
        symptoms=[],
    )
    assert med["likely_cause"] == "medication_edema"


def test_default_registry_auto_registered():
    # Importing parsers module registers into DEFAULT_REGISTRY
    reg = get_default_registry()
    assert reg.get("samsung_health_parser") is not None or any(
        getattr(p, "id", None) == "samsung_health_parser" for p in reg.list()
    )


def test_index_html_preserves_existing_features():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "Trend Intelligence" in html
    assert "Foot Pain Analysis" in html
    assert 'id="dash"' in html
    assert 'id="add"' in html
    assert 'id="sym"' in html
    assert 'id="rep"' in html
    assert "HC_V6" in html
    assert "Health Vault" in html
    assert "js/health_vault/import_engine.js" in html


def test_vault_js_modules_exist():
    required = [
        "js/measurement_model.js",
        "js/health_vault/medical_document.js",
        "js/health_vault/parser_registry.js",
        "js/health_vault/parsers/builtin_parsers.js",
        "js/health_vault/vault_store.js",
        "js/health_vault/import_engine.js",
        "js/health_vault/timeline.js",
        "js/health_vault/trend_engine.js",
        "js/health_vault/doctor_visit.js",
        "js/health_vault/ui.js",
        "docs/HC201_HEALTH_VAULT.md",
    ]
    for rel in required:
        assert (ROOT / rel).exists(), rel
