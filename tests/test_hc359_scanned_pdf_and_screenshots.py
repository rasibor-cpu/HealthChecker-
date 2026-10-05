"""HC-359 scanned-PDF OCR, screenshot parsers and runtime locality."""
import socket

import pytest

pymupdf = pytest.importorskip("pymupdf")

from backend.health_vault.ocr import LocalFirstOCRProvider, LocalPdfOCRProvider, RapidLocalVisionOCRProvider
from backend.health_vault.parsers.ocr_screenshot import (
    CgmScreenshotParser,
    GlucoseMeterScreenshotParser,
    WearableScreenshotParser,
)


def _pdf(pages):
    doc = pymupdf.open()
    for label in pages:
        doc.new_page().insert_text((72, 100), label, fontsize=14)
    data = doc.tobytes()
    doc.close()
    return data


def _scanned(n):
    """Image-only PDF: each page is a raster with no embedded text."""
    doc = pymupdf.open()
    for i in range(n):
        src = pymupdf.open()
        p = src.new_page()
        p.insert_text((72, 100), f"PAGE {i + 1} BP 128/82", fontsize=24)
        png = p.get_pixmap(dpi=100).tobytes("png")
        page = doc.new_page()
        page.insert_image(page.rect, stream=png)
    data = doc.tobytes()
    doc.close()
    return data


class _Out:
    def __init__(self, t):
        self.txts, self.scores = (t,), (0.9,)


class _Eng:
    def __init__(self):
        self.n = 0

    def __call__(self, content):
        self.n += 1
        return _Out(f"scan-page-{self.n}")


class _Boom:
    def __call__(self, content):
        raise RuntimeError("boom")


class _Blank:
    def __call__(self, content):
        return type("O", (), {"txts": (), "scores": ()})()


def _provider(engine):
    vision = RapidLocalVisionOCRProvider()
    vision._engine = engine
    return LocalPdfOCRProvider(vision)


def test_embedded_text_pdf_skips_vision():
    r = _provider(_Boom()).extract(_pdf(["Creatinine 90 umol/L eGFR 70"]), mime_type="application/pdf")
    assert r.meta["reason"] == "embedded_pdf_text"
    assert "Creatinine" in r.text and r.pages


def test_one_page_scanned_pdf_uses_local_ocr():
    r = _provider(_Eng()).extract(_scanned(1), mime_type="application/pdf")
    assert r.meta["reason"] == "scanned_pdf_local_ocr"
    assert r.meta["local_only"] is True and r.pages == ["scan-page-1"]


def test_multipage_scanned_pdf_preserves_order():
    r = _provider(_Eng()).extract(_scanned(3), mime_type="application/pdf")
    assert r.pages == ["scan-page-1", "scan-page-2", "scan-page-3"]
    assert r.text == "\n".join(r.pages)


def test_blank_scanned_pdf_has_no_text():
    r = _provider(_Blank()).extract(_scanned(1), mime_type="application/pdf")
    assert r.text == "" and r.meta["reason"] == "no_text_detected"


def test_malformed_pdf():
    r = _provider(_Eng()).extract(b"%PDF-1.4 garbage", mime_type="application/pdf")
    assert r.text == "" and r.meta["reason"] == "malformed_pdf"


def test_page_limit():
    p = _provider(_Eng())
    p.MAX_PAGES = 2
    r = p.extract(_pdf(["a", "b", "c"]), mime_type="application/pdf")
    assert r.meta["reason"] == "resource_limit" and r.meta["limit"] == "max_pages"


def test_size_limit():
    p = _provider(_Eng())
    p.MAX_BYTES = 10
    r = p.extract(_pdf(["a"]), mime_type="application/pdf")
    assert r.meta["reason"] == "resource_limit" and r.meta["limit"] == "max_bytes"


def test_renderer_unavailable(monkeypatch):
    import builtins

    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "pymupdf":
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    r = _provider(_Eng()).extract(_pdf(["a"]), mime_type="application/pdf")
    assert r.meta["reason"] == "pdf_renderer_unavailable"


def test_ocr_failure():
    r = _provider(_Boom()).extract(_scanned(1), mime_type="application/pdf")
    assert r.text == "" and r.meta["reason"] == "local_ocr_failed"


def test_local_first_routes_pdf():
    r = LocalFirstOCRProvider().extract(_pdf(["Heart rate 61 bpm"]), mime_type="application/pdf")
    assert r.provider == "local_pdf"


def _run(parser_cls, text, **ctx):
    p = parser_cls()
    c = {"text": text, "document_id": "d1", **ctx}
    return p.can_parse(c), p.parse(c)


def test_glucose_mgdl_and_mmol():
    ok, r = _run(GlucoseMeterScreenshotParser, "Glucose 112 mg/dL")
    assert ok and r["measurements"][0].value == 112.0 and r["measurements"][0].units == "mg/dL"
    _, r = _run(GlucoseMeterScreenshotParser, "Blood glucose 6.2 mmol/L")
    assert r["measurements"][0].units == "mmol/L"


def test_glucose_conflict_and_missing_unit_need_review():
    _, r = _run(GlucoseMeterScreenshotParser, "Glucose 110 mg/dL  Glucose 150 mg/dL")
    assert r["measurements"] == [] and r["requires_review"]
    ok, _ = _run(GlucoseMeterScreenshotParser, "Glucose 110")
    assert not ok


def test_cgm_value_timestamp_no_trend():
    ok, r = _run(CgmScreenshotParser, "Libre CGM Glucose 128 mg/dL 2026-01-02 08:30 rising fast")
    m = r["measurements"][0]
    assert ok and m.value == 128.0 and m.measured_at == "2026-01-02T08:30:00"
    assert len(r["measurements"]) == 1


def test_wearable_labelled_only_and_no_diagnosis():
    ok, r = _run(WearableScreenshotParser, "ECG Atrial fibrillation detected\nHeart rate 72 bpm\nSpO2 97%")
    assert ok and {m.metric: m.value for m in r["measurements"]} == {"heart_rate": 72, "spo2": 97}
    assert not any("fibrillation" in str(m.to_dict()).lower() for m in r["measurements"])


def test_wearable_conflicting_hr_needs_review():
    _, r = _run(WearableScreenshotParser, "Heart rate 72 bpm\nPulse 140 bpm")
    assert r["measurements"] == [] and r["requires_review"]


def test_runtime_ocr_succeeds_with_sockets_blocked(monkeypatch):
    """Proves only that no Python-level outbound connection is needed at inference time."""
    pytest.importorskip("rapidocr")
    src = pymupdf.open()
    p = src.new_page()
    p.insert_text((72, 120), "Glucose 112 mg/dL", fontsize=36)
    png = p.get_pixmap(dpi=150).tobytes("png")

    def deny(*a, **k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    prov = LocalFirstOCRProvider()
    img = prov.extract(png, mime_type="image/png", filename="g.png")
    assert img.meta["reason"] == "ok" and "112" in img.text
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(page.rect, stream=png)
    pdf = prov.extract(doc.tobytes(), mime_type="application/pdf")
    assert pdf.meta["reason"] == "scanned_pdf_local_ocr" and "112" in pdf.text
