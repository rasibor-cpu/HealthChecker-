"""HC-359 import-preview OCR gating contracts."""

from backend.health_vault.import_preview import ImportPreviewService


def _commit(*_args):
    raise AssertionError("preview tests must never commit")


def test_unreadable_image_is_review_only_and_has_no_confirmation_token():
    def dry(*_args):
        return {
            "ok": True,
            "document_type": "unknown",
            "source_system": "healthchecker_plus",
            "measurements": [],
            "requires_review": True,
            "clinical_data_detected": False,
            "confirmable": False,
            "ocr": {
                "provider": "rapidocr_local",
                "confidence": 0.0,
                "meta": {"reason": "no_text_detected", "local_only": True},
            },
            "warnings": ["No readable text was detected in this image"],
            "errors": [],
        }

    preview = ImportPreviewService(dry, _commit).create(
        "patient-A", b"image-bytes", "screen.png", "image/png"
    )
    assert preview["eligible"] is False
    assert preview["preview_token"] is None
    assert preview["clinical_data_detected"] is False
    assert preview["requires_review"] is True
    assert preview["ocr"]["provider"] == "rapidocr_local"
    assert preview["ocr"]["status"] == "no_text_detected"
    assert preview["ocr"]["local_only"] is True


def test_recognised_image_with_measurements_can_become_confirmable():
    def dry(*_args):
        return {
            "ok": True,
            "document_type": "blood_pressure_screenshot",
            "source_system": "healthchecker_plus",
            "measurements": [
                {"metric": "systolic", "value": 128, "units": "mmHg"},
                {"metric": "diastolic", "value": 82, "units": "mmHg"},
            ],
            "requires_review": False,
            "clinical_data_detected": True,
            "confirmable": True,
            "ocr": {
                "provider": "rapidocr_local",
                "confidence": 0.96,
                "meta": {"reason": "ok", "local_only": True},
            },
            "warnings": [],
            "errors": [],
        }

    preview = ImportPreviewService(dry, lambda *_a: {"ok": True}).create(
        "patient-A", b"image-bytes", "bp.png", "image/png"
    )
    assert preview["eligible"] is True
    assert preview["preview_token"]
    assert preview["clinical_data_detected"] is True
    assert preview["observation_count"] == 2
    assert preview["ocr"]["status"] == "ok"


def test_ocr_unavailable_image_cannot_be_confirmed():
    def dry(*_args):
        return {
            "ok": True,
            "measurements": [],
            "clinical_data_detected": False,
            "confirmable": False,
            "requires_review": True,
            "ocr": {
                "provider": "rapidocr_local",
                "confidence": 0.0,
                "meta": {"reason": "local_ocr_unavailable"},
            },
            "warnings": ["Local image OCR is unavailable; no clinical measurements were extracted"],
            "errors": [],
        }

    preview = ImportPreviewService(dry, _commit).create(
        "patient-A", b"image-bytes", "lab.jpg", "image/jpeg"
    )
    assert preview["eligible"] is False
    assert preview["preview_token"] is None
    assert preview["ocr"]["status"] == "local_ocr_unavailable"
