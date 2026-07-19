"""OCR provider abstraction — parsers never hardcode OCR vendors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
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
    """Replaceable OCR backend (EasyOCR, Tesseract, Azure, Vision, Textract, OpenAI)."""

    name: str = "base"

    @abstractmethod
    def extract(self, content: bytes | None, *, mime_type: str | None = None, filename: str | None = None) -> OCRResult:
        raise NotImplementedError


class NullOCRProvider(OCRProvider):
    """Default: no OCR — returns empty text. Safe for JSON/text imports."""

    name = "null"

    def extract(self, content: bytes | None, *, mime_type: str | None = None, filename: str | None = None) -> OCRResult:
        return OCRResult(text="", confidence=0.0, provider=self.name, meta={"reason": "ocr_disabled"})


class PassthroughTextOCRProvider(OCRProvider):
    """If content is already text/JSON, decode without external OCR."""

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


# Registry of future providers (stubs register readiness only)
FUTURE_OCR_PROVIDERS = (
    "EasyOCR",
    "Tesseract",
    "Azure OCR",
    "Google Vision",
    "AWS Textract",
    "OpenAI Vision",
)

_ACTIVE: OCRProvider = PassthroughTextOCRProvider()


def set_ocr_provider(provider: OCRProvider) -> None:
    global _ACTIVE
    _ACTIVE = provider


def get_ocr_provider() -> OCRProvider:
    return _ACTIVE
