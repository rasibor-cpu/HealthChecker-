"""
HC-201 API surface.

Endpoints:
  POST /api/import-health-record          (multipart; requires python-multipart)
  POST /api/import-health-records/batch   (multipart; requires python-multipart)
  POST /api/import-health-records/batch/json
  POST /api/import-health-record/json
  GET  /api/health-vault/executive-briefing
  GET  /api/health-vault/executive-briefing/print
  POST /api/ai-health/import-preview
  POST /api/ai-health/import-confirm
  GET  /api/ai-health/import-history
  GET  /api/monitoring/status
  GET  /api/monitoring/connectors
  POST /api/monitoring/sync
  POST /api/monitoring/evaluate
  POST /api/monitoring/scheduler/tick
  POST /api/companion/pair/start
  POST /api/companion/pair/confirm
  GET  /api/companion/devices
  DELETE /api/companion/devices/{device_id}
  POST /api/companion/observations
  GET  /api/companion/status
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from backend.health_vault.batch_config import get_batch_config
from backend.health_vault.batch_import import BatchImportService, sanitize_filename
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


def _run_json_batch(batch_service: BatchImportService, body: dict[str, Any]) -> dict[str, Any]:
    meta = _strip_banned_path_keys(body)
    items: list[dict[str, Any]] = []
    for raw in meta.get("items") or meta.get("files") or []:
        if not isinstance(raw, dict):
            continue
        entry = _strip_banned_path_keys(raw)
        entry.pop("content_path", None)
        if "filename" in entry:
            entry["filename"] = sanitize_filename(entry.get("filename"))
        items.append(entry)
    return batch_service.import_batch(
        items,
        batch_id=meta.get("batch_id"),
        auto_group=bool(meta.get("auto_group", True)),
    )


def create_health_vault_app(store: VaultStore | None = None):
    """Create a minimal FastAPI app if fastapi is installed; else return None."""
    try:
        from fastapi import FastAPI, File, Form, Request, UploadFile
        from fastapi.responses import JSONResponse
    except Exception:
        return None

    app = FastAPI(title="HealthChecker+ Health Vault", version="hc201g")
    vault = store or VaultStore()
    service = ImportService(store=vault)
    batch_service = BatchImportService(store=vault)
    doctor = DoctorVisitMode(vault)

    try:
        import multipart  # noqa: F401

        multipart_ok = True
    except Exception:
        multipart_ok = False

    if multipart_ok:

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
                raw_name = file.filename or body.get("filename") or "upload.bin"
                body["filename"] = sanitize_filename(raw_name)
                body["mime_type"] = (
                    file.content_type or body.get("mime_type") or "application/octet-stream"
                )
            result = service.import_health_record(body)
            return JSONResponse(_sanitize_value(result))

        @app.post("/api/import-health-records/batch")
        async def import_health_records_batch(
            files: list[UploadFile] | None = File(default=None),
            payload_json: str | None = Form(default=None),
        ) -> JSONResponse:
            meta: dict[str, Any] = {}
            if payload_json:
                try:
                    parsed = json.loads(payload_json)
                    if not isinstance(parsed, dict):
                        return JSONResponse(
                            {"ok": False, "errors": ["payload_json must be an object"]},
                            status_code=400,
                        )
                    meta = _strip_banned_path_keys(parsed)
                except json.JSONDecodeError:
                    return JSONResponse(
                        {"ok": False, "errors": ["invalid_payload_json"]},
                        status_code=400,
                    )

            items: list[dict[str, Any]] = []
            for raw in meta.get("items") or meta.get("files") or []:
                if not isinstance(raw, dict):
                    continue
                entry = _strip_banned_path_keys(raw)
                entry.pop("content_path", None)
                if "filename" in entry:
                    entry["filename"] = sanitize_filename(entry.get("filename"))
                items.append(entry)

            for upload in files or []:
                content = await upload.read()
                items.append(
                    {
                        "content": content,
                        "filename": sanitize_filename(upload.filename or "upload.bin"),
                        "mime_type": upload.content_type or "application/octet-stream",
                        "size_bytes": len(content),
                    }
                )

            report = batch_service.import_batch(
                items,
                batch_id=meta.get("batch_id"),
                auto_group=bool(meta.get("auto_group", True)),
            )
            status = 200 if report.get("ok") or report.get("partial_success") else 400
            if report.get("status") == "rejected":
                status = 400
            return JSONResponse(_sanitize_value(report), status_code=status)

    @app.post("/api/import-health-records/batch/json")
    async def import_health_records_batch_json(body: dict[str, Any]) -> JSONResponse:
        """Structured JSON batch (works without python-multipart)."""
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "errors": ["body must be an object"]}, status_code=400)
        report = _run_json_batch(batch_service, body)
        status = 200 if report.get("ok") or report.get("partial_success") else 400
        if report.get("status") == "rejected":
            status = 400
        return JSONResponse(_sanitize_value(report), status_code=status)

    @app.post("/api/import-health-record/json")
    async def import_health_record_json(body: dict[str, Any]) -> JSONResponse:
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "errors": ["body must be an object"]}, status_code=400)
        cleaned = _strip_banned_path_keys(body)
        cleaned.pop("content_path", None)
        result = service.import_health_record(cleaned)
        return JSONResponse(_sanitize_value(result))

    @app.get("/api/health-vault/batch-limits")
    def batch_limits() -> dict[str, Any]:
        return get_batch_config().to_dict()

    @app.get("/api/health-vault/timeline")
    def timeline(unified: bool = False) -> dict[str, Any]:
        if unified:
            from backend.health_vault.timeline import build_unified_timeline

            return _sanitize_value({"entries": build_unified_timeline(vault), "unified": True})
        return _sanitize_value({"entries": build_timeline(vault), "unified": False})

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

    @app.get("/api/health-vault/executive-briefing")
    def executive_briefing(
        patient_id: str = "default-patient",
        as_of: str | None = None,
        trend_window: str = "30d",
        category: str | None = None,
    ) -> dict[str, Any]:
        """HC-201I read-only executive health briefing (observational only)."""
        from backend.health_vault.executive_briefing import ExecutiveHealthBriefingEngine

        engine = ExecutiveHealthBriefingEngine(vault)
        payload = engine.generate(
            patient_id=patient_id,
            as_of=as_of,
            trend_window=trend_window,
            category=category,
        )
        return _sanitize_value(payload)

    @app.get("/api/health-vault/executive-briefing/print")
    def executive_briefing_print(
        patient_id: str = "default-patient",
        trend_window: str = "30d",
    ) -> dict[str, Any]:
        from backend.health_vault.executive_briefing import ExecutiveHealthBriefingEngine

        engine = ExecutiveHealthBriefingEngine(vault)
        return _sanitize_value(
            engine.printable_summary(patient_id=patient_id, trend_window=trend_window)
        )

    @app.post("/api/ai-health/import-preview")
    async def ai_health_import_preview(body: dict[str, Any]) -> JSONResponse:
        """HC-202 — preview AI-extracted records (no vault writes)."""
        from backend.ai_health.bridge import AIHealthBridge

        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "errors": ["body must be an object"]}, status_code=400)
        try:
            result = AIHealthBridge(store=vault).preview(_strip_banned_path_keys(body))
        except ValueError as exc:
            return JSONResponse(
                {"ok": False, "errors": [str(exc)]},
                status_code=400,
            )
        return JSONResponse(_sanitize_value(result))

    @app.post("/api/ai-health/import-confirm")
    async def ai_health_import_confirm(body: dict[str, Any]) -> JSONResponse:
        """HC-202 — confirmed AI import via canonical ImportPipeline only."""
        from backend.ai_health.bridge import AIHealthBridge

        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "errors": ["body must be an object"]}, status_code=400)
        result = AIHealthBridge(store=vault).confirm(_strip_banned_path_keys(body))
        status = 200 if result.get("ok") or result.get("partial_success") else 400
        if result.get("status") == "rejected":
            status = 400
        return JSONResponse(_sanitize_value(result), status_code=status)

    @app.get("/api/ai-health/import-history")
    def ai_health_import_history(limit: int = 50) -> dict[str, Any]:
        from backend.ai_health.bridge import AIHealthBridge

        return _sanitize_value(AIHealthBridge(store=vault).import_history(limit=limit))

    # --- HC-302 Continuous Monitoring ---

    @app.get("/api/monitoring/status")
    def monitoring_status(patient_id: str = "default-patient") -> dict[str, Any]:
        return _sanitize_value(monitoring_status_handler(patient_id=patient_id, store=vault))

    @app.get("/api/monitoring/connectors")
    def monitoring_connectors(include_simulated: bool = False) -> dict[str, Any]:
        return _sanitize_value(
            monitoring_connectors_handler(include_simulated=include_simulated, store=vault)
        )

    @app.post("/api/monitoring/sync")
    async def monitoring_sync(body: dict[str, Any] | None = None) -> JSONResponse:
        result = monitoring_sync_handler(body or {}, store=vault)
        return JSONResponse(_sanitize_value(result), status_code=200 if result.get("ok") or result.get("status") in {
            "UNAVAILABLE", "IMPORT_REQUIRED", "permission_required", "permission_denied"
        } else (200 if result.get("ok") else 400))

    @app.post("/api/monitoring/evaluate")
    async def monitoring_evaluate(body: dict[str, Any] | None = None) -> JSONResponse:
        result = monitoring_evaluate_handler(body or {}, store=vault)
        return JSONResponse(_sanitize_value(result), status_code=200 if result.get("ok") else 400)

    @app.post("/api/monitoring/scheduler/tick")
    async def monitoring_scheduler_tick(body: dict[str, Any] | None = None) -> JSONResponse:
        result = monitoring_scheduler_tick_handler(body or {}, store=vault)
        return JSONResponse(_sanitize_value(result))

    # --- HC-303A Android companion ---

    @app.post("/api/companion/pair/start")
    async def companion_pair_start(request: Request, body: dict[str, Any] | None = None) -> JSONResponse:
        admin = request.headers.get("X-HC-Companion-Admin")
        result = companion_pair_start_handler(body or {}, store=vault, admin_header=admin)
        code = 200 if result.get("ok") else (403 if result.get("status") == "admin_required" else 400)
        return JSONResponse(_sanitize_value(result), status_code=code)

    @app.post("/api/companion/pair/confirm")
    async def companion_pair_confirm(body: dict[str, Any] | None = None) -> JSONResponse:
        result = companion_pair_confirm_handler(body or {}, store=vault)
        return JSONResponse(_sanitize_value(result), status_code=200 if result.get("ok") else 400)

    @app.get("/api/companion/devices")
    def companion_devices(request: Request, include_revoked: bool = False) -> JSONResponse:
        admin = request.headers.get("X-HC-Companion-Admin")
        result = companion_devices_handler(
            include_revoked=include_revoked, store=vault, admin_header=admin
        )
        code = 200 if result.get("ok", True) and result.get("status") != "admin_required" else 403
        return JSONResponse(_sanitize_value(result), status_code=code)

    @app.delete("/api/companion/devices/{device_id}")
    async def companion_revoke(device_id: str, request: Request) -> JSONResponse:
        admin = request.headers.get("X-HC-Companion-Admin")
        result = companion_revoke_handler(device_id, store=vault, admin_header=admin)
        code = 200 if result.get("ok") else (403 if result.get("status") == "admin_required" else 400)
        return JSONResponse(_sanitize_value(result), status_code=code)

    @app.post("/api/companion/observations")
    async def companion_observations(request: Request) -> JSONResponse:
        # Fail closed on duplicated Authorization headers
        try:
            auth_values = request.headers.getlist("Authorization")
        except Exception:
            auth_values = [request.headers.get("Authorization")] if request.headers.get("Authorization") else []
        auth_values = [v for v in auth_values if v]
        if len(auth_values) > 1:
            return JSONResponse(
                {"ok": False, "status": "unauthorized", "errors": ["duplicate_authorization_header"]},
                status_code=401,
            )
        # Bound body size before JSON parse when Content-Length is present
        from backend.health_vault.companion.security import MAX_PAYLOAD_BYTES

        cl_raw = request.headers.get("content-length")
        content_length = None
        if cl_raw is not None:
            try:
                content_length = int(cl_raw)
            except ValueError:
                return JSONResponse(
                    {"ok": False, "status": "malformed", "errors": ["invalid_content_length"]},
                    status_code=400,
                )
            if content_length > MAX_PAYLOAD_BYTES:
                return JSONResponse(
                    {
                        "ok": False,
                        "status": "payload_too_large",
                        "errors": [f"payload_exceeds_{MAX_PAYLOAD_BYTES}_bytes"],
                    },
                    status_code=413,
                )
        # Credentials must not be accepted via query parameters
        if request.query_params.get("token") or request.query_params.get("authorization"):
            return JSONResponse(
                {"ok": False, "status": "unauthorized", "errors": ["credentials_in_query_rejected"]},
                status_code=401,
            )
        try:
            body = await request.json()
            if not isinstance(body, dict):
                body = {}
        except Exception:
            body = {}
        auth = auth_values[0] if auth_values else None
        proto = (request.headers.get("x-forwarded-proto") or "").lower()
        tls_enabled = True if proto == "https" else (False if proto == "http" else None)
        local_dev = str(request.headers.get("x-hc-local-dev") or "").lower() in {"1", "true", "yes"}
        result = companion_observations_handler(
            body,
            authorization=auth,
            store=vault,
            tls_enabled=tls_enabled,
            local_dev=local_dev,
            content_length=content_length,
        )
        status = 200 if result.get("ok") or result.get("status") == "duplicate_ack" else 400
        if result.get("status") == "unauthorized":
            status = 401
        if result.get("status") == "revoked":
            status = 403
        if result.get("status") == "payload_too_large":
            status = 413
        return JSONResponse(_sanitize_value(result), status_code=status)

    @app.get("/api/companion/status")
    def companion_status(request: Request) -> dict[str, Any]:
        auth = request.headers.get("Authorization")
        return _sanitize_value(companion_status_handler(store=vault, authorization=auth))

    # --- HC-301 Health Guardian ---

    @app.get("/api/guardian/status")
    def guardian_status(patient_id: str = "default-patient") -> dict[str, Any]:
        return _sanitize_value(guardian_status_handler(patient_id=patient_id, store=vault))

    @app.get("/api/guardian/alerts")
    def guardian_alerts(patient_id: str = "default-patient", active_only: bool = True) -> dict[str, Any]:
        return _sanitize_value(
            guardian_alerts_handler(patient_id=patient_id, active_only=active_only, store=vault)
        )

    @app.get("/api/guardian/alerts/history")
    def guardian_alerts_history(patient_id: str = "default-patient") -> dict[str, Any]:
        return _sanitize_value(
            guardian_alerts_handler(patient_id=patient_id, active_only=False, store=vault)
        )

    @app.post("/api/guardian/alerts/{alert_id}/acknowledge")
    async def guardian_ack(alert_id: str, body: dict[str, Any] | None = None) -> JSONResponse:
        result = guardian_acknowledge_handler(alert_id, body or {}, store=vault)
        return JSONResponse(_sanitize_value(result), status_code=200 if result.get("ok") else 400)

    @app.post("/api/guardian/alerts/{alert_id}/resolve")
    async def guardian_resolve(alert_id: str, body: dict[str, Any] | None = None) -> JSONResponse:
        result = guardian_resolve_handler(alert_id, body or {}, store=vault)
        return JSONResponse(_sanitize_value(result), status_code=200 if result.get("ok") else 400)

    @app.get("/api/guardian/baselines")
    def guardian_baselines(patient_id: str = "default-patient") -> dict[str, Any]:
        return _sanitize_value(guardian_baselines_handler(patient_id=patient_id, store=vault))

    @app.get("/api/guardian/cgm/sensors")
    def guardian_cgm_sensors(patient_id: str = "default-patient") -> dict[str, Any]:
        return _sanitize_value(cgm_sensors_handler(patient_id=patient_id, store=vault))

    @app.post("/api/guardian/cgm/sensors/register")
    async def guardian_cgm_register(body: dict[str, Any]) -> JSONResponse:
        result = cgm_register_handler(body, store=vault)
        return JSONResponse(_sanitize_value(result))

    @app.post("/api/guardian/cgm/sensors/{sensor_id}/activate")
    async def guardian_cgm_activate(sensor_id: str, body: dict[str, Any] | None = None) -> JSONResponse:
        result = cgm_activate_handler(sensor_id, body or {}, store=vault)
        return JSONResponse(_sanitize_value(result), status_code=200 if result.get("ok") else 400)

    @app.post("/api/guardian/cgm/sensors/{sensor_id}/fail")
    async def guardian_cgm_fail(sensor_id: str, body: dict[str, Any] | None = None) -> JSONResponse:
        result = cgm_fail_handler(sensor_id, body or {}, store=vault)
        return JSONResponse(_sanitize_value(result), status_code=200 if result.get("ok") else 400)

    @app.post("/api/guardian/cgm/sensors/{sensor_id}/replace")
    async def guardian_cgm_replace(sensor_id: str, body: dict[str, Any]) -> JSONResponse:
        result = cgm_replace_handler(sensor_id, body or {}, store=vault)
        return JSONResponse(_sanitize_value(result), status_code=200 if result.get("ok") else 400)

    @app.get("/api/guardian/cgm/inventory")
    def guardian_cgm_inventory(patient_id: str = "default-patient") -> dict[str, Any]:
        return _sanitize_value(cgm_inventory_handler(patient_id=patient_id, store=vault))

    @app.post("/api/guardian/cgm/inventory")
    async def guardian_cgm_inventory_update(body: dict[str, Any]) -> JSONResponse:
        return JSONResponse(_sanitize_value(cgm_inventory_update_handler(body, store=vault)))

    @app.get("/api/guardian/cgm/continuity")
    def guardian_cgm_continuity(patient_id: str = "default-patient") -> dict[str, Any]:
        return _sanitize_value(cgm_continuity_handler(patient_id=patient_id, store=vault))

    @app.get("/api/guardian/cgm/data-gaps")
    def guardian_data_gaps(patient_id: str = "default-patient") -> dict[str, Any]:
        return _sanitize_value(cgm_data_gaps_handler(patient_id=patient_id, store=vault))

    @app.post("/api/guardian/evaluate")
    async def guardian_evaluate(body: dict[str, Any] | None = None) -> JSONResponse:
        """Development/testing trigger only — not a clinical escalation channel."""
        result = guardian_evaluate_handler(body or {}, store=vault)
        return JSONResponse(_sanitize_value(result))

    return app


def import_health_record_handler(request: dict[str, Any], store: VaultStore | None = None) -> dict[str, Any]:
    """Framework-agnostic handler used by tests and future servers."""
    cleaned = _strip_banned_path_keys(dict(request or {}))
    result = ImportService(store=store or VaultStore()).import_health_record(cleaned)
    return _sanitize_value(result)


def import_health_records_batch_handler(
    items: list[dict[str, Any]],
    store: VaultStore | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Framework-agnostic batch handler for tests."""
    report = BatchImportService(store=store or VaultStore()).import_batch(items, **kwargs)
    return _sanitize_value(report)


def ai_health_import_preview_handler(
    body: dict[str, Any],
    store: VaultStore | None = None,
) -> dict[str, Any]:
    from backend.ai_health.bridge import AIHealthBridge

    return _sanitize_value(AIHealthBridge(store=store or VaultStore()).preview(_strip_banned_path_keys(body)))


def ai_health_import_confirm_handler(
    body: dict[str, Any],
    store: VaultStore | None = None,
) -> dict[str, Any]:
    from backend.ai_health.bridge import AIHealthBridge

    return _sanitize_value(AIHealthBridge(store=store or VaultStore()).confirm(_strip_banned_path_keys(body)))


def ai_health_import_history_handler(
    store: VaultStore | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    from backend.ai_health.bridge import AIHealthBridge

    return _sanitize_value(AIHealthBridge(store=store or VaultStore()).import_history(limit=limit))


# --- HC-301 framework-agnostic handlers ---


def _guardian(store: VaultStore | None = None):
    from backend.health_vault.guardian.health_guardian import HealthGuardian

    return HealthGuardian(store=store or VaultStore())


def guardian_status_handler(patient_id: str = "default-patient", store: VaultStore | None = None) -> dict[str, Any]:
    g = _guardian(store)
    return g.get_status(patient_id=patient_id)


def guardian_alerts_handler(
    patient_id: str = "default-patient",
    active_only: bool = True,
    store: VaultStore | None = None,
) -> dict[str, Any]:
    g = _guardian(store)
    return {
        "alerts": g.alerts.list_alerts(patient_id=patient_id, active_only=active_only),
        "counts": g.alerts.active_counts(patient_id=patient_id),
        "disclaimer": "Observational alerts only — not diagnoses.",
    }


def guardian_acknowledge_handler(
    alert_id: str,
    body: dict[str, Any] | None = None,
    store: VaultStore | None = None,
) -> dict[str, Any]:
    body = body or {}
    return _guardian(store).alerts.acknowledge(alert_id, note=body.get("note"))


def guardian_resolve_handler(
    alert_id: str,
    body: dict[str, Any] | None = None,
    store: VaultStore | None = None,
) -> dict[str, Any]:
    body = body or {}
    return _guardian(store).alerts.resolve(
        alert_id,
        note=body.get("note"),
        force=bool(body.get("force")),
    )


def guardian_baselines_handler(
    patient_id: str = "default-patient",
    store: VaultStore | None = None,
) -> dict[str, Any]:
    return _guardian(store).baselines.get_summaries(patient_id=patient_id)


def cgm_sensors_handler(patient_id: str = "default-patient", store: VaultStore | None = None) -> dict[str, Any]:
    return {"sensors": _guardian(store).cgm.list_sensors(patient_id=patient_id)}


def cgm_register_handler(body: dict[str, Any], store: VaultStore | None = None) -> dict[str, Any]:
    return {"ok": True, "sensor": _guardian(store).cgm.register_sensor(body or {})}


def cgm_activate_handler(
    sensor_id: str,
    body: dict[str, Any] | None = None,
    store: VaultStore | None = None,
) -> dict[str, Any]:
    body = body or {}
    return _guardian(store).cgm.activate_sensor(
        sensor_id,
        activation_timestamp=body.get("activation_timestamp"),
        reduce_inventory=bool(body.get("reduce_inventory", True)),
    )


def cgm_fail_handler(
    sensor_id: str,
    body: dict[str, Any] | None = None,
    store: VaultStore | None = None,
) -> dict[str, Any]:
    return _guardian(store).cgm.fail_sensor(sensor_id, reason=(body or {}).get("reason"))


def cgm_replace_handler(
    sensor_id: str,
    body: dict[str, Any] | None = None,
    store: VaultStore | None = None,
) -> dict[str, Any]:
    return _guardian(store).cgm.replace_sensor(sensor_id, body or {})


def cgm_inventory_handler(patient_id: str = "default-patient", store: VaultStore | None = None) -> dict[str, Any]:
    return {"inventory": _guardian(store).cgm.get_inventory(patient_id=patient_id)}


def cgm_inventory_update_handler(body: dict[str, Any], store: VaultStore | None = None) -> dict[str, Any]:
    return {"ok": True, "inventory": _guardian(store).cgm.update_inventory(body or {})}


def cgm_continuity_handler(patient_id: str = "default-patient", store: VaultStore | None = None) -> dict[str, Any]:
    return _guardian(store).cgm.evaluate_continuity(patient_id=patient_id)


def cgm_data_gaps_handler(patient_id: str = "default-patient", store: VaultStore | None = None) -> dict[str, Any]:
    return {"gaps": _guardian(store).cgm.list_data_gaps(patient_id=patient_id)}


def guardian_evaluate_handler(body: dict[str, Any] | None = None, store: VaultStore | None = None) -> dict[str, Any]:
    body = body or {}
    return _guardian(store).evaluate(
        patient_id=str(body.get("patient_id") or "default-patient"),
        pipeline_failure=bool(body.get("pipeline_failure")),
        trigger=str(body.get("trigger") or "api_dev_trigger"),
    )


# --- HC-302 framework-agnostic handlers ---


def _monitoring(store: VaultStore | None = None):
    from backend.health_vault.monitoring.bridge import ContinuousMonitoringBridge

    return ContinuousMonitoringBridge(store=store or VaultStore())


def monitoring_status_handler(
    patient_id: str = "default-patient",
    store: VaultStore | None = None,
) -> dict[str, Any]:
    return _monitoring(store).get_status(patient_id=patient_id)


def monitoring_connectors_handler(
    include_simulated: bool = False,
    store: VaultStore | None = None,
) -> dict[str, Any]:
    return {
        "connectors": _monitoring(store).available_connectors(include_simulated=include_simulated),
        "disclaimer": (
            "Connector readiness is observational. Live sync requires platform permissions "
            "and authorized bridges. Simulated connectors are test-only."
        ),
    }


def monitoring_sync_handler(body: dict[str, Any] | None = None, store: VaultStore | None = None) -> dict[str, Any]:
    body = body or {}
    # Public API never enables simulated connectors (production isolation).
    if str(body.get("connector_id") or "") == "simulated" or bool(body.get("allow_simulated")):
        return {
            "ok": False,
            "status": "rejected",
            "errors": ["simulated_connector_forbidden_via_public_api"],
            "disclaimer": (
                "Simulated monitoring connectors are test-only and cannot be invoked through the public API."
            ),
        }
    connector_id = body.get("connector_id")
    ctx = dict(body.get("context") or {})
    for banned in ("token", "access_token", "refresh_token", "password", "secret", "api_key"):
        ctx.pop(banned, None)
    bridge = _monitoring(store)
    if connector_id:
        return bridge.sync_connector(
            str(connector_id),
            patient_id=str(body.get("patient_id") or "default-patient"),
            context=ctx,
            allow_simulated=False,
            run_guardian=bool(body.get("run_guardian", True)),
        )
    return bridge.sync_all(
        patient_id=str(body.get("patient_id") or "default-patient"),
        context=ctx,
        allow_simulated=False,
    )


def monitoring_evaluate_handler(
    body: dict[str, Any] | None = None,
    store: VaultStore | None = None,
) -> dict[str, Any]:
    body = body or {}
    return _monitoring(store).evaluate(
        patient_id=str(body.get("patient_id") or "default-patient"),
        trigger=str(body.get("trigger") or "api_monitoring_evaluate"),
    )


def monitoring_scheduler_tick_handler(
    body: dict[str, Any] | None = None,
    store: VaultStore | None = None,
) -> dict[str, Any]:
    body = body or {}
    ctx = dict(body.get("context") or {})
    for banned in ("token", "access_token", "refresh_token", "password", "secret", "api_key"):
        ctx.pop(banned, None)
    return _monitoring(store).run_scheduled_sync(
        patient_id=str(body.get("patient_id") or "default-patient"),
        context=ctx,
        force=bool(body.get("force")),
    )


# --- HC-303A companion handlers ---


def _companion_pairing(store: VaultStore | None = None):
    from backend.health_vault.companion.pairing import CompanionPairingService

    return CompanionPairingService(store=store or VaultStore())


def _companion_delivery(store: VaultStore | None = None):
    from backend.health_vault.companion.delivery import CompanionDeliveryService

    return CompanionDeliveryService(store=store or VaultStore())


def companion_pair_start_handler(
    body: dict[str, Any] | None = None,
    store: VaultStore | None = None,
    admin_header: str | None = None,
) -> dict[str, Any]:
    from backend.health_vault.companion.security import companion_admin_authorized

    if not companion_admin_authorized(admin_header):
        return {"ok": False, "status": "admin_required", "errors": ["companion_admin_required"]}
    body = body or {}
    # patient_id from client is ignored — host binds default-patient only
    return _companion_pairing(store).start_pairing(display_name=body.get("display_name"))


def companion_pair_confirm_handler(
    body: dict[str, Any] | None = None,
    store: VaultStore | None = None,
) -> dict[str, Any]:
    body = body or {}
    # Ignore caller-supplied device_id — host always generates identity
    return _companion_pairing(store).confirm_pairing(
        pair_code=str(body.get("pair_code") or ""),
        device_label=body.get("device_label"),
        platform=str(body.get("platform") or "android"),
        app_version=body.get("app_version"),
    )


def companion_devices_handler(
    include_revoked: bool = False,
    store: VaultStore | None = None,
    admin_header: str | None = None,
) -> dict[str, Any]:
    from backend.health_vault.companion.security import companion_admin_authorized

    if not companion_admin_authorized(admin_header):
        return {"ok": False, "status": "admin_required", "errors": ["companion_admin_required"], "devices": []}
    return {
        "ok": True,
        "devices": _companion_pairing(store).list_devices(include_revoked=include_revoked),
        "disclaimer": "Device list excludes token secrets. Revoke to invalidate companion access.",
    }


def companion_revoke_handler(
    device_id: str,
    store: VaultStore | None = None,
    admin_header: str | None = None,
) -> dict[str, Any]:
    from backend.health_vault.companion.security import companion_admin_authorized

    if not companion_admin_authorized(admin_header):
        return {"ok": False, "status": "admin_required", "errors": ["companion_admin_required"]}
    return _companion_pairing(store).revoke_device(str(device_id))


def companion_observations_handler(
    body: dict[str, Any] | None = None,
    *,
    authorization: str | None = None,
    store: VaultStore | None = None,
    tls_enabled: bool | None = None,
    local_dev: bool = False,
    content_length: int | None = None,
) -> dict[str, Any]:
    return _companion_delivery(store).deliver(
        body or {},
        authorization=authorization,
        tls_enabled=tls_enabled,
        local_dev=local_dev,
        content_length=content_length,
    )


def companion_status_handler(
    store: VaultStore | None = None,
    authorization: str | None = None,
) -> dict[str, Any]:
    return _companion_delivery(store).status(authorization=authorization)
