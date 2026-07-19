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

from typing import Any

from backend.health_vault.doctor_visit import DoctorVisitMode
from backend.health_vault.import_service import ImportService
from backend.health_vault.timeline import build_timeline
from backend.health_vault.vault_store import VaultStore


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
            import json

            body.update(json.loads(payload_json))
        if file is not None:
            content = await file.read()
            body["content"] = content
            body["filename"] = file.filename or body.get("filename") or "upload.bin"
            body["mime_type"] = file.content_type or body.get("mime_type") or "application/octet-stream"
        result = service.import_health_record(body)
        return JSONResponse(result)

    @app.post("/api/import-health-record/json")
    async def import_health_record_json(body: dict[str, Any]) -> JSONResponse:
        """AI assistant path — pure JSON, no internal logic change required."""
        result = service.import_health_record(body)
        return JSONResponse(result)

    @app.get("/api/health-vault/timeline")
    def timeline() -> dict[str, Any]:
        return {"entries": build_timeline(vault)}

    @app.get("/api/health-vault/doctor-visit")
    def doctor_visit() -> dict[str, Any]:
        return doctor.generate()

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
        return {"entries": vault.import_log()}

    return app


def import_health_record_handler(request: dict[str, Any], store: VaultStore | None = None) -> dict[str, Any]:
    """Framework-agnostic handler used by tests and future servers."""
    return ImportService(store=store or VaultStore()).import_health_record(request)
