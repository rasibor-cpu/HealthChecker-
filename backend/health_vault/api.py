"""
HC-201 API surface.

Future endpoint:
  POST /api/import-health-record

Accepts multipart file uploads or JSON AI payloads:
{
  "document": "...",
  "extracted_measurements": [...],
  "interpretation": "...",
  "confidence": 0.9
}
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from backend.health_vault.doctor_visit import DoctorVisitMode
from backend.health_vault.import_service import ImportService
from backend.health_vault.timeline import build_timeline
from backend.health_vault.vault_store import VaultStore

_ABS_PATH_RE = re.compile(r"(?i)([a-z]:\\|\\\\|/home/|/Users/|/var/|/tmp/)")
_BANNED_PATH_KEYS = {
    "path",
    "filepath",
    "file_path",
    "absolute_path",
    "local_path",
    "filesystem_path",
}


def _sanitize_value(value: Any) -> Any:
    """Remove absolute filesystem paths from API payloads."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in {"storage_uri", "original_link"} and isinstance(v, str):
                if v.startswith("vault://") or v.startswith("idb://"):
                    out[k] = v
                elif _ABS_PATH_RE.search(v) or (len(v) > 2 and (v[1] == ":" or v.startswith("/"))):
                    # Redact absolute paths; keep document-relative hint when possible.
                    name = Path(v).name
                    out[k] = f"vault://documents/{name}" if name else "vault://redacted"
                else:
                    out[k] = v
            else:
                out[k] = _sanitize_value(v)
        return out
    if isinstance(value, list):
        return [_sanitize_value(v) for v in value]
    if isinstance(value, str) and _ABS_PATH_RE.search(value):
        return "[redacted-path]"
    return value


def _strip_banned_path_keys(body: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(body)
    for key in list(cleaned.keys()):
        if key in _BANNED_PATH_KEYS:
            cleaned.pop(key, None)
    return cleaned


def create_health_vault_app(store: VaultStore | None = None):
    """Create a minimal FastAPI app if fastapi is installed; else return None."""
    try:
        from fastapi import FastAPI, File, Form, UploadFile
        from fastapi.responses import JSONResponse
    except Exception:
        return None

    app = FastAPI(title="HealthChecker+ Health Vault", version="hc201")
    vault = store or VaultStore()
    service = ImportService(store=vault)
    doctor = DoctorVisitMode(vault)

    @app.post("/api/import-health-record")
    async def import_health_record(
        file: UploadFile | None = File(default=None),
        payload_json: str | None = Form(default=None),
    ) -> JSONResponse:
        body: dict[str, Any] = {}
        if payload_json:
            try:
                parsed = json.loads(payload_json)
                if not isinstance(parsed, dict):
                    return JSONResponse(
                        {"ok": False, "errors": ["payload_json must be an object"]},
                        status_code=400,
                    )
                body.update(parsed)
            except json.JSONDecodeError:
                return JSONResponse(
                    {"ok": False, "errors": ["invalid_payload_json"]},
                    status_code=400,
                )
        body = _strip_banned_path_keys(body)
        if file is not None:
            content = await file.read()
            body["content"] = content
            # Use basename only — never trust client-supplied directory paths.
            raw_name = file.filename or body.get("filename") or "upload.bin"
            body["filename"] = Path(str(raw_name)).name or "upload.bin"
            body["mime_type"] = file.content_type or body.get("mime_type") or "application/octet-stream"
        result = service.import_health_record(body)
        return JSONResponse(_sanitize_value(result))

    @app.post("/api/import-health-record/json")
    async def import_health_record_json(body: dict[str, Any]) -> JSONResponse:
        """AI assistant path — pure JSON, no internal logic change required."""
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "errors": ["body must be an object"]}, status_code=400)
        cleaned = _strip_banned_path_keys(body)
        # Never accept raw filesystem reads via API.
        cleaned.pop("content_path", None)
        result = service.import_health_record(cleaned)
        return JSONResponse(_sanitize_value(result))

    @app.get("/api/health-vault/timeline")
    def timeline() -> dict[str, Any]:
        return _sanitize_value({"entries": build_timeline(vault)})

    @app.get("/api/health-vault/doctor-visit")
    def doctor_visit() -> dict[str, Any]:
        return _sanitize_value(doctor.generate())

    @app.get("/api/health-vault/integrity")
    def integrity() -> dict[str, Any]:
        return vault.verify_integrity()

    @app.get("/api/health-vault/intelligence")
    def intelligence() -> dict[str, Any]:
        from backend.health_vault.health_intelligence import HealthIntelligenceEngine

        obs = HealthIntelligenceEngine(vault).generate_observations()
        return {"observations": obs, "disclaimer": "Observational only — not a diagnosis."}

    @app.get("/api/health-vault/import-log")
    def import_log() -> dict[str, Any]:
        return _sanitize_value({"entries": vault.import_log()})

    return app


def import_health_record_handler(request: dict[str, Any], store: VaultStore | None = None) -> dict[str, Any]:
    """Framework-agnostic handler used by tests and future servers."""
    cleaned = _strip_banned_path_keys(dict(request or {}))
    result = ImportService(store=store or VaultStore()).import_health_record(cleaned)
    return _sanitize_value(result)
