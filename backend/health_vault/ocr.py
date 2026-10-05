"""OCR provider abstraction — parsers never hardcode OCR vendors.

HC-359 adds an offline/local vision provider for image uploads.  Medical image
bytes are never sent to a network OCR service by this module.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from threading import Lock
from typing import Any


@dataclass
class OCRResult:
    text: str = ""
    confidence: float = 0.0
    provider: str = "none"
    pages: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "confidence": self.confidence,
            "provider": self.provider,
            "pages": list(self.pages),
            "meta": dict(self.meta),
        }


class OCRProvider(ABC):
    """Replaceable OCR backend. Providers must make PHI routing explicit."""

    name: str = "base"

    @abstractmethod
    def extract(
        self,
        content: bytes | None,
        *,
        mime_type: str | None = None,
        filename: str | None = None,
    ) -> OCRResult:
        raise NotImplementedError


class NullOCRProvider(OCRProvider):
    name = "null"

    def extract(self, content: bytes | None, *, mime_type: str | None = None, filename: str | None = None) -> OCRResult:
        return OCRResult(text="", confidence=0.0, provider=self.name, meta={"reason": "ocr_disabled"})


class PassthroughTextOCRProvider(OCRProvider):
    """Decode text/JSON inputs without invoking a vision model."""

    name = "passthrough_text"

    def extract(self, content: bytes | None, *, mime_type: str | None = None, filename: str | None = None) -> OCRResult:
        if not content:
            return OCRResult(text="", confidence=0.0, provider=self.name)
        mime = (mime_type or "").lower()
        name = (filename or "").lower()
        if "json" in mime or "text" in mime or name.endswith((".json", ".txt", ".csv")):
            try:
                text = content.decode("utf-8")
                return OCRResult(text=text, confidence=1.0, provider=self.name, pages=[text])
            except Exception:
                text = content.decode("utf-8", errors="replace")
                return OCRResult(text=text, confidence=0.7, provider=self.name, pages=[text])
        return OCRResult(
            text="",
            confidence=0.0,
            provider=self.name,
            meta={"reason": "binary_requires_vision_ocr", "mime_type": mime_type},
        )


class RapidLocalVisionOCRProvider(OCRProvider):
    """Offline image OCR backed by RapidOCR + ONNX Runtime.

    The engine is imported and constructed lazily so existing JSON/text imports
    keep working when the optional HC-359 dependency set is not installed.
    Failure is returned as control metadata; medical values are never guessed.
    """

    name = "rapidocr_local"
    _IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff")

    def __init__(self) -> None:
        self._engine = None
        self._engine_lock = Lock()

    @staticmethod
    def _is_image(mime_type: str | None, filename: str | None) -> bool:
        mime = (mime_type or "").lower()
        name = (filename or "").lower()
        return mime.startswith("image/") or name.endswith(RapidLocalVisionOCRProvider._IMAGE_EXTENSIONS)

    def _get_engine(self):
        if self._engine is None:
            with self._engine_lock:
                if self._engine is None:
                    from rapidocr import RapidOCR
                    self._engine = RapidOCR()
        return self._engine

    def extract(self, content: bytes | None, *, mime_type: str | None = None, filename: str | None = None) -> OCRResult:
        if not content:
            return OCRResult(text="", confidence=0.0, provider=self.name, meta={"reason": "empty_content"})
        if not self._is_image(mime_type, filename):
            return OCRResult(
                text="",
                confidence=0.0,
                provider=self.name,
                meta={"reason": "unsupported_binary_type", "mime_type": mime_type},
            )
        try:
            result = self._get_engine()(content)
        except (ImportError, ModuleNotFoundError) as exc:
            return OCRResult(
                text="",
                confidence=0.0,
                provider=self.name,
                meta={"reason": "local_ocr_unavailable", "error_type": type(exc).__name__},
            )
        except Exception as exc:
            return OCRResult(
                text="",
                confidence=0.0,
                provider=self.name,
                meta={"reason": "local_ocr_failed", "error_type": type(exc).__name__},
            )

        texts = [str(x).strip() for x in (getattr(result, "txts", None) or ()) if str(x).strip()]
        scores = [float(x) for x in (getattr(result, "scores", None) or ()) if x is not None]
        text = "\n".join(texts)
        confidence = sum(scores) / len(scores) if scores else (0.0 if not text else 0.5)
        return OCRResult(
            text=text,
            confidence=max(0.0, min(float(confidence), 1.0)),
            provider=self.name,
            pages=[text] if text else [],
            meta={
                "reason": "ok" if text else "no_text_detected",
                "local_only": True,
                "line_count": len(texts),
            },
        )


class LocalFirstOCRProvider(OCRProvider):
    """Text passthrough plus local-only vision OCR for image binaries."""

    name = "local_first"

    def __init__(self) -> None:
        self._text = PassthroughTextOCRProvider()
        self._vision = RapidLocalVisionOCRProvider()

    def extract(self, content: bytes | None, *, mime_type: str | None = None, filename: str | None = None) -> OCRResult:
        text_result = self._text.extract(content, mime_type=mime_type, filename=filename)
        if text_result.text or text_result.meta.get("reason") != "binary_requires_vision_ocr":
            return text_result
        return self._vision.extract(content, mime_type=mime_type, filename=filename)


FUTURE_OCR_PROVIDERS = (
    "Azure OCR",
    "Google Vision",
    "AWS Textract",
    "OpenAI Vision",
)

_ACTIVE: OCRProvider = LocalFirstOCRProvider()


def set_ocr_provider(provider: OCRProvider) -> None:
    global _ACTIVE
    _ACTIVE = provider


def get_ocr_provider() -> OCRProvider:
    return _ACTIVE
