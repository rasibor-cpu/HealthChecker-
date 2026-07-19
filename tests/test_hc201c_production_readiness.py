"""
HC-201C — Production readiness tests.

Pipeline, validation, confidence, OCR abstraction, clinical rules,
event bus, health intelligence, non-regression.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.health_vault.clinical_rules import (
    FLAG_ABNORMAL,
    FLAG_BORDERLINE,
    FLAG_CRITICAL,
    FLAG_NORMAL,
    FLAG_UNKNOWN,
    ClinicalRulesEngine,
)
from backend.health_vault.confidence_engine import ConfidenceEngine
from backend.health_vault.event_bus import (
    DOCUMENT_RECEIVED,
    DUPLICATE_DETECTED,
    IMPORT_COMPLETED,
    EventBus,
)
from backend.health_vault.health_intelligence import HealthIntelligenceEngine
from backend.health_vault.import_pipeline import ImportPipeline
from backend.health_vault.import_service import ImportService
from backend.health_vault.models import create_measurement
from backend.health_vault.ocr import (
    FUTURE_OCR_PROVIDERS,
    NullOCRProvider,
    PassthroughTextOCRProvider,
    get_ocr_provider,
    set_ocr_provider,
)
from backend.health_vault.parser_registry import ParserRegistry
from backend.health_vault.parsers import register_builtin_parsers
from backend.health_vault.validation_engine import ValidationEngine
from backend.health_vault.vault_store import VaultStore
from backend.intelligence.foot_pain_engine import FootPainEngine


@pytest.fixture()
def store(tmp_path: Path) -> VaultStore:
    return VaultStore(root=tmp_path / "vault")


@pytest.fixture()
def pipeline(store: VaultStore) -> ImportPipeline:
    reg = ParserRegistry()
    register_builtin_parsers(reg)
    bus = EventBus()
    return ImportPipeline(store=store, registry=reg, bus=bus)


def _json_payload(obj: dict, name: str = "x.json") -> dict:
    return {
        "content": json.dumps(obj).encode("utf-8"),
        "filename": name,
        "mime_type": "application/json",
    }


# --- Event bus ---
def test_event_bus_publish_subscribe():
    bus = EventBus()
    seen = []
    bus.subscribe(DOCUMENT_RECEIVED, lambda e: seen.append(e.name))
    bus.publish(DOCUMENT_RECEIVED, {"ok": True})
    assert seen == [DOCUMENT_RECEIVED]
    assert len(bus.history) == 1


def test_event_bus_handler_isolation():
    bus = EventBus()

    def bad(_e):
        raise RuntimeError("boom")

    good = []
    bus.subscribe(IMPORT_COMPLETED, bad)
    bus.subscribe(IMPORT_COMPLETED, lambda e: good.append(1))
    bus.publish(IMPORT_COMPLETED, {})
    assert good == [1]


# --- OCR ---
def test_ocr_passthrough_json():
    set_ocr_provider(PassthroughTextOCRProvider())
    r = get_ocr_provider().extract(b'{"a":1}', mime_type="application/json", filename="a.json")
    assert r.text
    assert r.confidence == 1.0
    assert "EasyOCR" in FUTURE_OCR_PROVIDERS


def test_ocr_null_provider():
    p = NullOCRProvider()
    r = p.extract(b"\xff\xd8", mime_type="image/jpeg", filename="x.jpg")
    assert r.text == ""
    assert r.provider == "null"


def test_ocr_provider_swappable():
    set_ocr_provider(NullOCRProvider())
    assert get_ocr_provider().name == "null"
    set_ocr_provider(PassthroughTextOCRProvider())
    assert get_ocr_provider().name == "passthrough_text"


# --- Clinical rules ---
def test_clinical_rules_bp_flags():
    eng = ClinicalRulesEngine()
    assert eng.classify(create_measurement(metric="systolic", value=110, units="mmHg")) == FLAG_NORMAL
    assert eng.classify(create_measurement(metric="systolic", value=130, units="mmHg")) == FLAG_BORDERLINE
    assert eng.classify(create_measurement(metric="systolic", value=150, units="mmHg")) == FLAG_ABNORMAL
    assert eng.classify(create_measurement(metric="systolic", value=190, units="mmHg")) == FLAG_CRITICAL


def test_clinical_rules_glucose_and_unknown():
    eng = ClinicalRulesEngine()
    assert eng.classify(create_measurement(metric="glucose", value=90, units="mg/dL")) == FLAG_NORMAL
    assert eng.classify(create_measurement(metric="glucose", value=500, units="mg/dL")) == FLAG_CRITICAL
    assert eng.classify(create_measurement(metric="unknown_metric", value=1)) == FLAG_UNKNOWN


def test_clinical_rules_egfr_and_sleep():
    eng = ClinicalRulesEngine()
    assert eng.classify(create_measurement(metric="egfr", value=95)) == FLAG_NORMAL
    assert eng.classify(create_measurement(metric="egfr", value=45)) == FLAG_ABNORMAL
    assert eng.classify(create_measurement(metric="sleep_score", value=80)) == FLAG_NORMAL


def test_clinical_rules_module_is_observational_only():
    raw = (ROOT / "backend/health_vault/clinical_rules.py").read_text(encoding="utf-8")
    assert "never diagnoses" in raw.lower() or "not diagnoses" in raw.lower() or "observational" in raw.lower()


# --- Validation ---
def test_validation_impossible_value():
    eng = ValidationEngine()
    m = create_measurement(metric="systolic", value=10, units="mmHg")
    result = eng.validate([m])
    assert result.ok is False
    assert any(i.code == "impossible_value" for i in result.issues)


def test_validation_missing_and_duplicate():
    eng = ValidationEngine()
    a = create_measurement(metric="glucose", value=100, units="mg/dL", measured_at="2026-01-01")
    b = create_measurement(metric="glucose", value=100, units="mg/dL", measured_at="2026-01-01")
    c = create_measurement(metric="glucose", value=None)
    result = eng.validate([a, b, c])
    codes = {i.code for i in result.issues}
    assert "duplicate_measurement" in codes
    assert "missing_value" in codes


def test_validation_unit_mismatch_warning():
    eng = ValidationEngine()
    m = create_measurement(metric="glucose", value=100, units="mmol/L")
    result = eng.validate([m])
    assert any(i.code == "unit_mismatch" for i in result.issues)


# --- Confidence ---
def test_confidence_engine_bounds():
    eng = ConfidenceEngine()
    c = eng.compute(extraction=0.9, validation=0.8, clinical=0.7, storage=1.0)
    assert 0.0 <= c.overall_confidence <= 1.0
    assert c.extraction_confidence == 0.9
    d = c.to_dict()
    assert set(d) == {
        "extraction_confidence",
        "validation_confidence",
        "clinical_confidence",
        "storage_confidence",
        "overall_confidence",
    }


def test_confidence_clinical_from_flags():
    eng = ConfidenceEngine()
    assert eng.clinical_from_flags([]) == 0.4
    assert eng.clinical_from_flags(["Normal", "Normal"]) > 0.5


# --- Pipeline ---
def test_pipeline_full_happy_path(pipeline: ImportPipeline, store: VaultStore):
    result = pipeline.run(_json_payload({"glucose": 95, "systolic": 118, "diastolic": 75, "egfr": 92}))
    assert result["ok"] is True
    assert result["duplicate"] is False
    assert result["confidence"]["overall_confidence"] > 0
    assert result["validation"] is not None
    assert result["digital_signature"]["hash"]
    assert result["ui_notify"] is True
    assert store.import_log()
    assert any(e.name == IMPORT_COMPLETED for e in pipeline.bus.history)


def test_pipeline_duplicate_skips_reimport(pipeline: ImportPipeline, store: VaultStore):
    payload = _json_payload({"glucose": 110})
    r1 = pipeline.run(payload)
    r2 = pipeline.run(payload)
    assert r1["ok"] and r2["duplicate"] is True
    assert r2["status"] == "Duplicate"
    assert len(store.list_documents()) == 1
    assert any(e.name == DUPLICATE_DETECTED for e in pipeline.bus.history)


def test_pipeline_ai_path(pipeline: ImportPipeline):
    result = pipeline.run(
        {
            "document": "ai packet",
            "filename": "ai.json",
            "mime_type": "application/json",
            "acquisition_method": "external_ai",
            "ai_version": "chatgpt-test",
            "extracted_measurements": [
                {"metric": "glucose", "value": 130, "units": "mg/dL"},
            ],
            "interpretation": "Monitor",
            "confidence": 0.88,
        }
    )
    assert result["ok"] is True
    assert result["digital_signature"]["ai_version"] == "chatgpt-test"
    assert result["measurements"][0]["abnormal_flag"] in {
        FLAG_NORMAL,
        FLAG_BORDERLINE,
        FLAG_ABNORMAL,
        FLAG_CRITICAL,
        FLAG_UNKNOWN,
    }


def test_pipeline_emits_core_events(pipeline: ImportPipeline):
    pipeline.bus.clear_history()
    pipeline.run(_json_payload({"hba1c": 5.4}))
    names = {e.name for e in pipeline.bus.history}
    assert DOCUMENT_RECEIVED in names
    assert IMPORT_COMPLETED in names


def test_import_service_delegates_to_pipeline(store: VaultStore):
    svc = ImportService(store=store)
    assert isinstance(svc.pipeline, ImportPipeline)
    out = svc.import_health_record(_json_payload({"bmi": 24}))
    assert out["ok"] is True
    assert "confidence" in out


def test_pipeline_perf_timings_recorded(pipeline: ImportPipeline):
    result = pipeline.run(_json_payload({"glucose": 100}))
    assert "perf_ms" in result
    assert result["perf_ms"].get("total_ms", 0) >= 0


# --- Intelligence ---
def test_health_intelligence_observational(store: VaultStore, pipeline: ImportPipeline):
    for g in (100, 110, 130):
        pipeline.run(_json_payload({"glucose": g}, name=f"g{g}.json"))
    obs = HealthIntelligenceEngine(store).generate_observations()
    assert obs
    assert all(o.get("diagnostic") is False for o in obs)
    assert all("observational" in (o.get("observation") or "").lower() or o.get("kind") == "observational" for o in obs)
    blob = " ".join(o["observation"].lower() for o in obs)
    assert "diagnos" not in blob or "not a" in blob


def test_health_intelligence_persisted(store: VaultStore, pipeline: ImportPipeline):
    pipeline.run(_json_payload({"egfr": 90}))
    HealthIntelligenceEngine(store).generate_observations()
    hi = store.health_intelligence()
    assert "observations" in hi


# --- Immutability / signatures ---
def test_digital_signature_fields(pipeline: ImportPipeline):
    result = pipeline.run(_json_payload({"glucose": 99}))
    sig = result["digital_signature"]
    assert sig["hash"]
    assert sig["import_timestamp"]
    assert "parser_version" in sig


def test_document_bytes_immutable(pipeline: ImportPipeline, store: VaultStore):
    content = b'{"glucose": 101}'
    r = pipeline.run({"content": content, "filename": "g.json", "mime_type": "application/json"})
    path = store.resolve_storage_path(r["document"]["storage_uri"], r["document"]["id"])
    assert path is not None
    original = path.read_bytes()
    from backend.health_vault.models import MedicalDocument

    with pytest.raises(ValueError):
        store.store(
            document=MedicalDocument(id=r["document"]["id"]),
            measurements=[],
            content=b"tamper",
        )
    assert path.read_bytes() == original


# --- Non-regression ---
def test_foot_pain_non_regression_hc201c():
    engine = FootPainEngine()
    out = engine.evaluate(
        pain_location="big_toe",
        swelling=False,
        symmetry="one_side",
        onset_speed="sudden",
        recent_med_change=False,
        glucose=120,
        kidney_status="normal",
        symptoms=["sharp_pain"],
    )
    assert out["likely_cause"] == "gout"


def test_index_html_preserves_core_and_pipeline_scripts():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "HC_V6" in html
    assert "Trend Intelligence" in html
    assert "Foot Pain Analysis" in html
    assert "event_bus.js" in html
    assert "clinical_rules.js" in html


def test_hc_v6_key_untouched_by_vault_modules():
    store_js = (ROOT / "js/health_vault/vault_store.js").read_text(encoding="utf-8")
    assert 'META_KEY = "HC_HEALTH_VAULT_V1"' in store_js


# --- Performance smoke ---
def test_import_performance_budget(pipeline: ImportPipeline):
    t0 = time.perf_counter()
    for i in range(5):
        pipeline.run(_json_payload({"glucose": 100 + i}, name=f"p{i}.json"))
    elapsed = time.perf_counter() - t0
    # Local JSON pipeline should be well under 5s for 5 imports
    assert elapsed < 5.0


def test_timeline_and_doctor_after_pipeline(pipeline: ImportPipeline, store: VaultStore):
    pipeline.run(_json_payload({"egfr": 70, "glucose": 140, "systolic": 142, "diastolic": 88}))
    from backend.health_vault.doctor_visit import DoctorVisitMode
    from backend.health_vault.timeline import build_timeline

    tl = build_timeline(store)
    assert tl
    report = DoctorVisitMode(store).generate()
    assert "kidney_trend" in report


def test_import_log_records_parser_and_warnings(pipeline: ImportPipeline, store: VaultStore):
    pipeline.run(_json_payload({"glucose": 100}))
    log = store.import_log()
    assert log[-1]["result"] == "ok"
    assert "timestamp" in log[-1]


def test_rc1_docs_exist():
    assert (ROOT / "docs/HC201_HEALTH_VAULT.md").exists()
    assert (ROOT / "docs").is_dir()


def test_api_sanitizes_absolute_paths(tmp_path: Path):
    from backend.health_vault.api import import_health_record_handler

    store = VaultStore(root=tmp_path / "api-vault")
    out = import_health_record_handler(
        {
            "content": b'{"glucose": 105}',
            "filename": "g.json",
            "mime_type": "application/json",
            "path": "C:/secrets/should-not-be-used.json",
        },
        store=store,
    )
    assert out["ok"] is True
    blob = json.dumps(out)
    assert "C:/secrets" not in blob
    assert "C:\\\\secrets" not in blob
    uri = out["document"].get("storage_uri") or ""
    assert uri.startswith("vault://") or not (":\\" in uri or uri.startswith("/home/"))


def test_failed_import_before_store_leaves_no_document(tmp_path: Path):
    store = VaultStore(root=tmp_path / "fail-vault")
    reg = ParserRegistry()
    register_builtin_parsers(reg)
    from backend.health_vault.ocr import OCRProvider, set_ocr_provider, PassthroughTextOCRProvider

    class BoomOCR(OCRProvider):
        name = "boom"

        def extract(self, content, *, mime_type=None, filename=None):
            raise RuntimeError("ocr_boom")

    set_ocr_provider(BoomOCR())
    try:
        pipeline = ImportPipeline(store=store, registry=reg)
        result = pipeline.run(
            {"content": b'{"glucose": 1}', "filename": "x.json", "mime_type": "application/json"}
        )
        assert result["ok"] is False
        assert store.list_documents() == []
    finally:
        set_ocr_provider(PassthroughTextOCRProvider())


def test_index_html_has_medical_disclaimer():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "not a medical diagnosis" in html.lower()
