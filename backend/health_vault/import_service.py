"""Reusable Import Service — delegates to autonomous ImportPipeline (HC-201C)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.health_vault.import_pipeline import ImportPipeline
from backend.health_vault.parser_registry import get_default_registry
from backend.health_vault.vault_store import VaultStore


class ImportService:
    """
    Accept PDF/PNG/JPG/JSON (+ AI payloads).

    All imports pass through ImportPipeline:
    Document → Parser → OCR → Extract → Validate → Duplicate check →
    Store → Timeline → Trends → Doctor Visit → Audit → UI notify.
    """

    def __init__(self, store: VaultStore | None = None, registry=None) -> None:
        self.store = store or VaultStore()
        self.registry = registry or get_default_registry()
        self.pipeline = ImportPipeline(store=self.store, registry=self.registry)

    def import_health_record(self, request: dict[str, Any]) -> dict[str, Any]:
        return self.pipeline.run(request)

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
