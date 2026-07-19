"""Reusable Import Service — architecture for POST /api/import-health-record."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.health_vault import parsers as _parsers  # noqa: F401 — auto-register
from backend.health_vault.models import MedicalDocument, classify_document_type
from backend.health_vault.parser_registry import get_default_registry
from backend.health_vault.timeline import build_timeline
from backend.health_vault.trend_engine import TrendEngine
from backend.health_vault.vault_store import VaultStore


class ImportService:
    """Accept PDF/PNG/JPG/JSON (+ AI payloads). Append-only vault writes."""

    def __init__(self, store: VaultStore | None = None, registry=None) -> None:
        self.store = store or VaultStore()
        self.registry = registry or get_default_registry()
        self.trends = TrendEngine(self.store)

    def import_health_record(self, request: dict[str, Any]) -> dict[str, Any]:
        req = dict(request or {})
        content: bytes | None = req.get("content")
        filename = req.get("filename") or "upload.bin"
        mime = req.get("mime_type") or "application/octet-stream"
        text = req.get("text") or ""
        data_json = req.get("json")

        if content is None and isinstance(req.get("document"), str):
            text = req["document"]
            content = text.encode("utf-8")
            try:
                data_json = json.loads(text)
            except Exception:
                pass
        elif content is not None and (
            "json" in mime or str(filename).lower().endswith(".json") or "text" in mime
        ):
            try:
                text = content.decode("utf-8", errors="replace")
                data_json = json.loads(text)
            except Exception:
                text = content.decode("utf-8", errors="replace")

        sha256 = None
        if content is not None:
            sha256 = VaultStore.sha256_bytes(content)
        elif req.get("sha256"):
            sha256 = req["sha256"]

        document_type = classify_document_type(
            filename, mime, req.get("document_type")
        )
        document = MedicalDocument(
            patient_id=req.get("patient_id") or "default-patient",
            document_type=document_type,
            source_system=req.get("source_system") or "healthchecker_plus",
            acquisition_method=req.get("acquisition_method")
            or ("external_ai" if req.get("extracted_measurements") else "manual_upload"),
            original_filename=filename,
            sha256=sha256,
            mime_type=mime,
            tags=list(req.get("tags") or []),
            interpretation=req.get("interpretation"),
            measured_at=req.get("measured_at"),
            status="imported",
        )

        parse_ctx = {
            "document_id": document.id,
            "document_type": document.document_type,
            "filename": filename,
            "mime_type": mime,
            "text": text,
            "json": data_json,
            "source_system": document.source_system,
            "acquisition_method": document.acquisition_method,
            "extracted_measurements": req.get("extracted_measurements"),
            "confidence": req.get("confidence"),
            "measured_at": req.get("measured_at"),
        }
        parsed = self.registry.parse(parse_ctx)
        document.parser_version = (
            f"{parsed['parser']['id']}@{parsed['parser']['version']}"
            if parsed.get("parser")
            else None
        )
        document.parser_confidence = (
            float(req["confidence"])
            if req.get("confidence") is not None
            else float(parsed.get("confidence") or 0.0)
        )
        document.status = "parsed" if parsed.get("measurements") else "partial"

        stored = self.store.store(
            document=document,
            measurements=list(parsed.get("measurements") or []),
            content=content,
            interpretation=req.get("interpretation"),
            parser=parsed.get("parser"),
            import_meta={"notes": parsed.get("notes"), "mime_type": mime},
        )

        trends = self.trends.recompute()
        timeline = build_timeline(self.store)

        return {
            "ok": True,
            "document": stored["document"],
            "measurements": [m.to_dict() if hasattr(m, "to_dict") else m for m in (parsed.get("measurements") or [])],
            "parser": parsed.get("parser"),
            "confidence": document.parser_confidence,
            "import_record": stored["import_record"],
            "sha256": sha256,
            "imported_at": document.imported_at,
            "trends": trends,
            "timeline_preview": timeline[:5],
        }

    def import_file(self, path: str | Path, **kwargs: Any) -> dict[str, Any]:
        p = Path(path)
        content = p.read_bytes()
        payload = {
            "content": content,
            "filename": p.name,
            "mime_type": kwargs.get("mime_type") or "application/octet-stream",
            **{k: v for k, v in kwargs.items() if k != "mime_type"},
        }
        return self.import_health_record(payload)
