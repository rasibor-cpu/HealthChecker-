"""HC-359 local vision OCR safety and integration contracts."""

from tests.hc360_synthetic import blank_png

from backend.health_vault.ocr import (
    LocalFirstOCRProvider,
    RapidLocalVisionOCRProvider,
)


class _Output:
    txts = ("Blood Pressure", "128 / 82", "Pulse 61")
    scores = (0.99, 0.96, 0.94)


class _Engine:
    def __call__(self, content):
        assert content
        return _Output()


def test_local_vision_provider_extracts_text_and_confidence_without_network():
    provider = RapidLocalVisionOCRProvider()
    provider._engine = _Engine()
    result = provider.extract(blank_png(), mime_type="image/png", filename="bp.png")
    assert result.provider == "rapidocr_local"
    assert result.meta["local_only"] is True
    assert result.meta["reason"] == "ok"
    assert "128 / 82" in result.text
    assert 0.9 < result.confidence <= 1.0


def test_local_first_preserves_text_passthrough():
    result = LocalFirstOCRProvider().extract(
        b'{"measurements":[]}', mime_type="application/json", filename="record.json"
    )
    assert result.provider == "passthrough_text"
    assert result.confidence == 1.0
    assert result.text.startswith("{")


def test_non_image_binary_is_not_sent_to_vision_engine():
    provider = RapidLocalVisionOCRProvider()
    provider._engine = _Engine()
    result = provider.extract(b"%PDF", mime_type="application/pdf", filename="report.pdf")
    assert result.text == ""
    assert result.meta["reason"] == "unsupported_binary_type"


def test_empty_image_never_fabricates_text():
    result = RapidLocalVisionOCRProvider().extract(
        b"", mime_type="image/png", filename="empty.png"
    )
    assert result.text == ""
    assert result.confidence == 0.0
    assert result.meta["reason"] == "empty_content"


def test_image_byte_limit_fails_closed_before_engine(monkeypatch):
    provider = RapidLocalVisionOCRProvider()
    provider._engine = _Engine()
    monkeypatch.setattr(provider, "MAX_IMAGE_BYTES", 4)
    result = provider.extract(blank_png(), mime_type="image/png", filename="large.png")
    assert result.text == ""
    assert result.meta["reason"] == "resource_limit"
    assert result.meta["limit"] == "max_image_bytes"
    assert result.meta["local_only"] is True


def test_image_pixel_limit_fails_closed_before_engine(monkeypatch):
    provider = RapidLocalVisionOCRProvider()
    provider._engine = _Engine()
    monkeypatch.setattr(provider, "MAX_IMAGE_PIXELS", 1)
    result = provider.extract(blank_png(), mime_type="image/png", filename="wide.png")
    assert result.text == ""
    assert result.meta["reason"] == "resource_limit"
    assert result.meta["limit"] == "max_image_pixels"
    assert result.meta["local_only"] is True


def test_malformed_image_fails_closed_before_engine():
    provider = RapidLocalVisionOCRProvider()
    provider._engine = _Engine()
    result = provider.extract(b"not-an-image", mime_type="image/png", filename="bad.png")
    assert result.text == ""
    assert result.meta["reason"] == "malformed_image"
    assert result.meta["local_only"] is True
