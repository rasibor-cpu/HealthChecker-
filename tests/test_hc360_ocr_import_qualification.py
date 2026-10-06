"""HC-360 governed OCR/import qualification matrix (synthetic, non-PHI, isolated Vault).

source -> local OCR (where appropriate) -> parser -> preview -> confirmability.
Real RapidOCR/PyMuPDF are used; nothing touches a production Vault.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

pymupdf = pytest.importorskip("pymupdf")
pytest.importorskip("rapidocr")

from backend.health_vault.import_preview import ImportPreviewService, PreviewError
from backend.health_vault.ocr import (
    LocalFirstOCRProvider,
    LocalPdfOCRProvider,
    get_ocr_provider,
    set_ocr_provider,
)
from backend.health_vault.records_service import RecordsService
from backend.health_vault.vault_store import VaultStore
from tests.hc360_synthetic import blank_pdf, blank_png, png_lines, scanned_pdf, text_pdf

PDF, PNG = "application/pdf", "image/png"
USER = "patient-A"


def _tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


@pytest.fixture()
def vault(tmp_path):
    previous = get_ocr_provider()
    set_ocr_provider(LocalFirstOCRProvider())  # earlier suites may have swapped the global
    try:
        store = VaultStore(root=tmp_path / "vault", allow_plaintext=True)
        records = RecordsService(store)
        service = ImportPreviewService(records.preview_record, records.upload_record)
        yield store, records, service, tmp_path / "vault"
    finally:
        set_ocr_provider(previous)


# name -> (content factory, mime, filename, eligible, ocr provider, ocr status, expected metrics)
MATRIX = {
    "text_pdf": (lambda: text_pdf(["Creatinine 90 umol/L", "eGFR 70 mL/min", "HbA1c 5.9 %"]),
                 PDF, "lab.pdf", True, "local_pdf", "embedded_pdf_text", {"creatinine", "egfr", "hba1c"}),
    "scanned_pdf_1p": (lambda: scanned_pdf([["Blood Pressure", "128/82 mmHg"]]),
                       PDF, "bp.pdf", True, "local_pdf", "scanned_pdf_local_ocr", {"systolic_bp", "diastolic_bp"}),
    "scanned_pdf_multipage": (lambda: scanned_pdf([["Blood Pressure 118/76"], ["Heart rate 64 bpm"]]),
                              PDF, "multi.pdf", True, "local_pdf", "scanned_pdf_local_ocr", {"systolic_bp", "diastolic_bp"}),
    "malformed_pdf": (lambda: b"%PDF-1.7 definitely not a pdf", PDF, "bad.pdf", False, "local_pdf", "malformed_pdf", set()),
    "page_limit_pdf": (lambda: blank_pdf(LocalPdfOCRProvider.MAX_PAGES + 1),
                       PDF, "big.pdf", False, "local_pdf", "resource_limit", set()),
    "phone_screenshot": (lambda: png_lines(["12:45  5G  87%", "Messages", "See you at lunch"]),
                         PNG, "shot.png", False, "rapidocr_local", "ok", set()),
    "glucose_mgdl": (lambda: png_lines(["Blood Glucose", "112 mg/dL"]),
                     PNG, "g1.png", True, "rapidocr_local", "ok", {"glucose"}),
    "glucose_mmol": (lambda: png_lines(["Glucose 6.2 mmol/L"]),
                     PNG, "g2.png", True, "rapidocr_local", "ok", {"glucose"}),
    "glucose_conflict": (lambda: png_lines(["Glucose 110 mg/dL", "Glucose 150 mg/dL"]),
                         PNG, "g3.png", False, "rapidocr_local", "ok", set()),
    "cgm_libre": (lambda: png_lines(["Libre CGM", "Glucose 128 mg/dL", "2026-01-02 08:30"]),
                  PNG, "cgm.png", True, "rapidocr_local", "ok", {"glucose_cgm_interstitial"}),
    "wearable": (lambda: png_lines(["Heart rate 72 bpm", "SpO2 97%"]),
                 PNG, "w.png", True, "rapidocr_local", "ok", {"heart_rate", "oxygen_saturation"}),
    "ecg_diagnostic_wording": (lambda: png_lines(["ECG Atrial fibrillation", "Heart rate 72 bpm"]),
                               PNG, "ecg.png", True, "rapidocr_local", "ok", {"heart_rate"}),
    "lab_screenshot": (lambda: png_lines(["LifeLabs", "Creatinine 90", "eGFR 70", "HbA1c 5.9"]),
                       PNG, "lab.png", True, "rapidocr_local", "ok", {"creatinine", "egfr", "hba1c"}),
    "blank_image": (blank_png, PNG, "blank.png", False, "rapidocr_local", "no_text_detected", set()),
    "nonmedical_image": (lambda: png_lines(["Pizza Menu", "Margherita 12.50"]),
                         PNG, "menu.png", False, "rapidocr_local", "ok", set()),
}


@pytest.mark.parametrize("case", sorted(MATRIX))
def test_qualification_matrix(case, vault):
    store, _records, service, root = vault
    factory, mime, name, eligible, provider, status, metrics = MATRIX[case]
    before = _tree(root)
    preview = service.create(USER, factory(), name, mime)

    assert preview["ocr"]["provider"] == provider
    assert preview["ocr"]["status"] == status
    assert preview["ocr"]["local_only"] is True or provider == "passthrough_text"
    assert preview["eligible"] is eligible
    assert bool(preview["preview_token"]) is eligible
    assert metrics <= set(preview["metrics"])
    if not eligible:
        # Fail closed: nothing readable/unambiguous => review-only, zero observations.
        assert preview["requires_review"] is True
        assert preview["observation_count"] == 0
        assert preview["clinical_data_detected"] is False
    assert _tree(root) == before, "preview must not write to the Vault"
    assert store.list_documents() == []


def test_ambiguous_and_unreadable_inputs_explain_why(vault):
    _s, _r, service, _root = vault
    conflict = service.create(USER, png_lines(["Glucose 110 mg/dL", "Glucose 150 mg/dL"]), "g.png", PNG)
    assert any("unambiguous" in w for w in conflict["warnings"])
    blank = service.create(USER, blank_png(), "b.png", PNG)
    assert any("No readable text" in w for w in blank["warnings"])
    bad = service.create(USER, b"%PDF-1.4 junk", "b.pdf", PDF)
    assert any("malformed_pdf" in w for w in bad["warnings"])


def test_oversized_pdf_is_resource_rejected_before_parsing(vault, monkeypatch):
    _s, _r, service, _root = vault
    monkeypatch.setattr(LocalPdfOCRProvider, "MAX_BYTES", 64)
    preview = service.create(USER, text_pdf(["Creatinine 90 umol/L"]), "huge.pdf", PDF)
    assert preview["ocr"]["status"] == "resource_limit"
    assert preview["eligible"] is False and preview["observation_count"] == 0


def test_values_units_and_provenance_are_preserved_from_ocr(vault):
    _s, records, _svc, _root = vault
    mmol = records.preview_record(USER, png_lines(["Glucose 6.2 mmol/L"]), "g.png", PNG)
    m = mmol["measurements"][0]
    assert m["original_units"] == "mmol/L" and m["units"] == "mg/dL"
    assert abs(m["value"] - 111.7) < 0.5
    cgm = records.preview_record(USER, png_lines(["Libre CGM", "Glucose 128 mg/dL", "2026-01-02 08:30"]), "c.png", PNG)
    assert cgm["measurements"][0]["value"] == 128.0
    assert str(cgm["measurements"][0]["measured_at"]).startswith("2026-01-02T08:30")
    assert cgm["parser"]["id"] == "cgm_screenshot_parser"
    assert cgm["ocr"]["provider"] == "rapidocr_local"


def test_ecg_wording_never_becomes_a_diagnosis(vault):
    _s, records, service, _root = vault
    parsed = records.preview_record(USER, png_lines(["ECG Atrial fibrillation", "Heart rate 72 bpm"]), "e.png", PNG)
    blob = json.dumps(parsed["measurements"]).lower()
    assert "fibrillation" not in blob and "arrhythmia" not in blob
    assert {m["metric"] for m in parsed["measurements"]} == {"heart_rate"}
    assert "ecg_result" not in {m["metric"] for m in parsed["measurements"]}


# ---------------------------------------------------------------- atomicity


def _confirmable_png() -> bytes:
    return png_lines(["Glucose 112 mg/dL"])


def test_preview_and_cancel_perform_zero_persistent_writes(vault):
    store, _r, service, root = vault
    before = _tree(root)
    preview = service.create(USER, _confirmable_png(), "g.png", PNG)
    assert preview["eligible"] and _tree(root) == before
    assert service.cancel(USER, preview["preview_token"])["status"] == "CANCELLED"
    assert _tree(root) == before and store.list_documents() == []
    with pytest.raises(PreviewError) as err:
        service.confirm(USER, preview["preview_token"])
    assert err.value.code == "preview_cancelled"
    assert _tree(root) == before


def test_failed_or_ambiguous_import_performs_zero_clinical_writes(vault):
    store, _r, service, root = vault
    before = _tree(root)
    for content, name, mime in (
        (png_lines(["Glucose 110 mg/dL", "Glucose 150 mg/dL"]), "c.png", PNG),
        (blank_png(), "b.png", PNG),
        (b"%PDF-1.4 junk", "j.pdf", PDF),
    ):
        preview = service.create(USER, content, name, mime)
        assert preview["preview_token"] is None
        with pytest.raises(PreviewError):
            service.confirm(USER, "not-a-real-token")
    assert _tree(root) == before and store.list_documents() == []


def test_commit_failure_leaves_no_partial_write_and_is_retryable(vault, monkeypatch):
    store, _r, service, root = vault
    preview = service.create(USER, _confirmable_png(), "g.png", PNG)
    before = _tree(root)
    real = store._write_index

    def boom(*_a, **_k):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(store, "_write_index", boom)
    with pytest.raises(PreviewError) as err:
        service.confirm(USER, preview["preview_token"])
    assert err.value.code == "import_failed"
    monkeypatch.setattr(store, "_write_index", real)
    assert store.list_documents() == []
    leftovers = {k for k, v in _tree(root).items() if before.get(k) != v}
    assert not leftovers, f"failed commit left files behind: {leftovers}"
    done = service.confirm(USER, preview["preview_token"])  # same preview is retryable
    assert done["ok"] is True and len(store.list_documents()) == 1


def test_confirm_is_atomic_idempotent_duplicate_safe_and_survives_restart(vault):
    store, _r, service, root = vault
    content = _confirmable_png()
    preview = service.create(USER, content, "g.png", PNG)
    first = service.confirm(USER, preview["preview_token"])
    assert first["ok"] is True and first["already_confirmed"] is False
    docs = store.list_documents()
    assert len(docs) == 1 and docs[0].get("original_filename") == "g.png"
    measurements = store.list_measurements()
    assert [m["metric"] for m in measurements] == ["glucose"]

    again = service.confirm(USER, preview["preview_token"])  # idempotent confirm
    assert again["already_confirmed"] is True and len(store.list_documents()) == 1

    dup = service.create(USER, content, "g-again.png", PNG)  # same bytes => duplicate
    assert dup["duplicate"] is True and dup["eligible"] is False and dup["preview_token"] is None
    assert len(store.list_documents()) == 1 and len(store.list_measurements()) == 1

    # Restart: a brand-new store/service over the same directory reads the result back.
    reopened = VaultStore(root=root, allow_plaintext=True)
    assert len(reopened.list_documents()) == 1
    got = reopened.list_measurements()
    assert [(m["metric"], m["value"], m["units"]) for m in got] == [("glucose", 112.0, "mg/dL")]
    assert got[0].get("document_id") == docs[0]["id"]
