"""OCR provider abstraction â€” parsers never hardcode OCR vendors.

HC-359 adds an offline/local vision provider for image uploads.  Medical image
bytes are never sent to a network OCR service by this module.
"""

from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any


class LocalOCRAssetsError(ImportError):
    """Required offline OCR model assets are absent or fail integrity checks."""


# Default RapidOCR 3.9.2 models are shipped inside the hash-locked wheel
# (requirements/production.txt). They are pinned here so the runtime never
# falls back to RapidOCR's first-use network download of a missing/altered model.
RAPIDOCR_MODEL_SHA256 = {
    "PP-OCRv6_det_small.onnx": "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f",
    "PP-OCRv6_rec_small.onnx": "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884",
    "ch_ppocr_mobile_v2.0_cls_mobile.onnx": "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c",
}


def verify_local_model_assets(model_root: Path) -> None:
    for name, expected in RAPIDOCR_MODEL_SHA256.items():
        path = model_root / name
        if not path.is_file():
            raise LocalOCRAssetsError(f"missing_model:{name}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected:
            raise LocalOCRAssetsError(f"model_hash_mismatch:{name}")


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
                    import rapidocr

                    model_root = Path(rapidocr.__file__).resolve().parent / "models"
                    verify_local_model_assets(model_root)
                    self._engine = RapidOCR(params={"Global.model_root_dir": str(model_root)})
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


class LocalPdfOCRProvider(OCRProvider):
    """Local-only PDF text extraction with scanned-page OCR fallback.

    Embedded text is used first; pages are only rasterised and OCR'd when the
    PDF has no useful embedded text. Nothing leaves the host.
    """

    name = "local_pdf"
    MAX_BYTES = 20 * 1024 * 1024
    MAX_PAGES = 10
    RENDER_DPI = 200
    MAX_RENDER_PIXELS = 4000
    MIN_EMBEDDED_CHARS = 20

    def __init__(self, vision: "RapidLocalVisionOCRProvider | None" = None) -> None:
        self._vision = vision or RapidLocalVisionOCRProvider()

    @staticmethod
    def is_pdf(content: bytes | None, mime_type: str | None, filename: str | None) -> bool:
        if "pdf" in (mime_type or "").lower() or (filename or "").lower().endswith(".pdf"):
            return True
        return bool(content) and content[:5] == b"%PDF-"

    def _result(self, reason: str, *, text: str = "", conf: float = 0.0, pages=None, **meta) -> OCRResult:
        return OCRResult(
            text=text,
            confidence=conf,
            provider=self.name,
            pages=pages or [],
            meta={"reason": reason, "local_only": True, **meta},
        )

    def extract(self, content: bytes | None, *, mime_type: str | None = None, filename: str | None = None) -> OCRResult:
        if not content:
            return self._result("empty_content")
        if len(content) > self.MAX_BYTES:
            return self._result("resource_limit", limit="max_bytes")
        try:
            import pymupdf
        except ImportError:
            return self._result("pdf_renderer_unavailable")
        try:
            doc = pymupdf.open(stream=content, filetype="pdf")
        except Exception as exc:
            return self._result("malformed_pdf", error_type=type(exc).__name__)
        try:
            page_count = doc.page_count
            if page_count > self.MAX_PAGES:
                return self._result("resource_limit", limit="max_pages", page_count=page_count)
            embedded = []
            try:
                for i in range(page_count):
                    embedded.append((doc.load_page(i).get_text() or "").strip())
            except Exception as exc:
                return self._result("malformed_pdf", error_type=type(exc).__name__)
            if sum(len(re.sub(r"\s", "", t)) for t in embedded) >= self.MIN_EMBEDDED_CHARS:
                return self._result(
                    "embedded_pdf_text", text="\n".join(t for t in embedded if t),
                    conf=1.0, pages=embedded, pdf_kind="embedded_text", page_count=page_count,
                )
            return self._ocr_pages(doc, page_count, pymupdf)
        finally:
            doc.close()

    def _ocr_pages(self, doc, page_count: int, pymupdf) -> OCRResult:
        pages: list[str] = []
        confs: list[float] = []
        for i in range(page_count):
            try:
                page = doc.load_page(i)
                zoom = self.RENDER_DPI / 72.0
                rect = page.rect
                longest = max(rect.width, rect.height) * zoom
                if longest > self.MAX_RENDER_PIXELS:
                    zoom *= self.MAX_RENDER_PIXELS / longest
                png = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("png")
            except Exception as exc:
                return self._result("malformed_pdf", error_type=type(exc).__name__)
            r = self._vision.extract(png, mime_type="image/png", filename=f"page-{i + 1}.png")
            reason = (r.meta or {}).get("reason")
            if reason == "local_ocr_unavailable":
                return self._result("local_ocr_unavailable", pdf_kind="scanned_pdf", page_count=page_count)
            if reason == "local_ocr_failed":
                return self._result("local_ocr_failed", pdf_kind="scanned_pdf", page_count=page_count, failed_page=i + 1)
            pages.append(r.text)
            if r.text:
                confs.append(r.confidence)
        text = "\n".join(t for t in pages if t)
        if not text:
            return self._result("no_text_detected", pages=pages, pdf_kind="scanned_pdf", page_count=page_count)
        return self._result(
            "scanned_pdf_local_ocr", text=text, conf=sum(confs) / len(confs), pages=pages,
            pdf_kind="scanned_pdf", page_count=page_count,
        )


class LocalFirstOCRProvider(OCRProvider):
    """Text passthrough plus local-only vision OCR for image binaries."""

    name = "local_first"

    def __init__(self) -> None:
        self._text = PassthroughTextOCRProvider()
        self._vision = RapidLocalVisionOCRProvider()
        self._pdf = LocalPdfOCRProvider(self._vision)

    def extract(self, content: bytes | None, *, mime_type: str | None = None, filename: str | None = None) -> OCRResult:
        if LocalPdfOCRProvider.is_pdf(content, mime_type, filename):
            return self._pdf.extract(content, mime_type=mime_type, filename=filename)
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
