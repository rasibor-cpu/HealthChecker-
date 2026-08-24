"""
HC-201 API surface.

Endpoints:
  POST /api/import-health-record          (multipart; requires python-multipart)
  POST /api/import-health-records/batch   (multipart; requires python-multipart)
  POST /api/import-health-records/batch/json
  POST /api/import-health-record/json
  GET  /api/health-vault/executive-briefing
  GET  /api/health-vault/executive-briefing/print
  GET  /api/health-vault/health-snapshot
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
import secrets
from functools import partial
from pathlib import Path
from typing import Any

from backend.health_vault.batch_config import get_batch_config
from backend.health_vault.batch_import import BatchImportService, sanitize_filename
from backend.health_vault.doctor_visit import DoctorVisitMode
from backend.health_vault.import_service import ImportService
from backend.health_vault.timeline import build_timeline
from backend.health_vault.vault_store import VaultStore

# Module-level Request is required for FastAPI with `from __future__ import annotations`
# (nested local imports are not visible to get_type_hints / route analysis).
try:
    from fastapi import Request
except Exception:  # pragma: no cover - FastAPI optional at import time
    Request = Any  # type: ignore[misc,assignment]

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


def _parse_single_multipart(content_type: str, payload: bytes) -> tuple[str, str, bytes]:
    """Dependency-free fallback for the single-file HC-317B upload contract."""
    match = re.search(r"boundary=(?:\"([^\"]+)\"|([^;]+))", content_type)
    if not match:
        raise ValueError("multipart_boundary_missing")
    boundary = (match.group(1) or match.group(2)).encode("ascii", "strict")
    for part in payload.split(b"--" + boundary):
        if b"filename=" not in part:
            continue
        header_blob, separator, content = part.lstrip(b"\r\n").partition(b"\r\n\r\n")
        if not separator:
            continue
        headers = header_blob.decode("latin-1")
        name_match = re.search(r'filename="([^\"]*)"', headers)
        type_match = re.search(r"(?im)^Content-Type:\s*([^\r\n]+)", headers)
        filename = sanitize_filename(name_match.group(1) if name_match else "upload.bin")
        mime_type = type_match.group(1).strip() if type_match else "application/octet-stream"
        return filename, mime_type, content.removesuffix(b"\r\n")
    raise ValueError("multipart_file_missing")


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


def create_health_vault_app(
    store: VaultStore | None = None,
    *,
    test_users: dict[str, str] | None = None,
    production: bool | None = None,
    bootstrap_password: str | None = None,
):
    """Create a minimal FastAPI app if fastapi is installed; else return None."""
    try:
        from fastapi import FastAPI, File, Form, UploadFile
        from fastapi.responses import FileResponse, JSONResponse
        from fastapi.staticfiles import StaticFiles
    except Exception:
        return None

    app = FastAPI(title="HealthChecker+ Health Vault", version="hc201g")
    frontend_root = Path(__file__).resolve().parents[2]

    def serve_frontend_file(filename: str) -> FileResponse:
        headers = None
        if filename == "mobile.html":
            headers = {
                "Cache-Control": "no-store",
                "Content-Security-Policy": (
                    "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
                    "script-src 'self'; style-src 'self'; object-src 'none'; "
                    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
                ),
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            }
        return FileResponse(frontend_root / filename, headers=headers)

    # The consumer UI is deliberately exposed through a narrow allowlist. Never
    # mount frontend_root: it also contains the encrypted vault and runtime data.
    app.add_api_route(
        "/", partial(serve_frontend_file, "index.html"), methods=["GET"],
        include_in_schema=False,
    )
    app.add_api_route(
        "/mobile", partial(serve_frontend_file, "mobile.html"), methods=["GET"],
        include_in_schema=False,
    )
    for public_asset in (
        "index.html",
        "mobile.html",
        "style.css",
        "app.js",
        "manifest.webmanifest",
        "service-worker.js",
        "icon-192.png",
        "icon-512.png",
        "maskable-192.png",
        "maskable-512.png",
        "apple-touch-icon.png",
    ):
        app.add_api_route(
            f"/{public_asset}", partial(serve_frontend_file, public_asset),
            methods=["GET"], include_in_schema=False,
        )
    app.mount("/js", StaticFiles(directory=frontend_root / "js"), name="frontend-js")
    app.mount("/css", StaticFiles(directory=frontend_root / "css"), name="frontend-css")
    production_mode = (store is None) if production is None else bool(production)
    if store is None:
        if production_mode:
            from backend.health_vault.production_runtime import create_production_vault

            vault = create_production_vault()
        else:
            vault = VaultStore()
    else:
        vault = store
    if production_mode and not vault.encrypted:
        raise RuntimeError("production_vault_encryption_required")
    service = ImportService(store=vault)
    batch_service = BatchImportService(store=vault)
    doctor = DoctorVisitMode(vault)
    from backend.health_vault.dashboard_service import DashboardService
    dashboard_service = DashboardService(vault)
    from backend.health_vault.records_service import RecordsService
    records_service = RecordsService(vault)
    from backend.health_vault.auth import AuthenticationError, AuthenticationService
    enrollment_password = bootstrap_password
    if production_mode and not (Path(vault.root) / "auth_registry.json").exists():
        enrollment_password = enrollment_password or __import__("os").environ.get("HC_BOOTSTRAP_PASSWORD")
    auth_service = AuthenticationService(
        vault,
        bootstrap_password=enrollment_password,
        allow_development_bootstrap=not production_mode,
    )
    app.state.auth_service = auth_service
    app.state.production_mode = production_mode

    def _bearer_token(request: Request) -> str:
        auth = request.headers.get("Authorization")
        if not auth or not auth.startswith("Bearer ") or not auth[7:].strip():
            raise AuthenticationError()
        return auth[7:].strip()

    def _get_authenticated_patient(request: Request) -> str:
        account, _ = auth_service.resolve(_bearer_token(request), require_full=True)
        return account.user_id

    def _auth_error(exc: AuthenticationError) -> JSONResponse:
        message = "Unauthorized" if exc.code == "unauthorized" else exc.code
        return JSONResponse({"ok": False, "error": message, "code": exc.code}, status_code=exc.status_code)

    def _request_patient(request: Request) -> str:
        patient_id = getattr(request.state, "patient_id", None)
        return str(patient_id) if patient_id else _get_authenticated_patient(request)

    # HC-320B production boundary. Device-token ingestion and one-time pairing
    # confirmation have their own authentication. Everything else under the
    # clinical API surface is account-authenticated before route dispatch.
    production_public_api = {
        "/api/auth/login",
        "/api/auth/session",
        "/api/auth/password/change",
        "/api/auth/logout",
        "/api/companion/pair/confirm",
        "/api/companion/observations",
        "/api/companion/status",
        "/api/health-vault/batch-limits",
    }

    @app.middleware("http")
    async def production_clinical_authentication(request: Request, call_next):
        path = request.url.path
        if production_mode and path.startswith("/api/") and path not in production_public_api:
            try:
                account, _ = auth_service.resolve(_bearer_token(request), require_full=True)
            except AuthenticationError as exc:
                return _auth_error(exc)
            request.state.patient_id = account.user_id
        return await call_next(request)
    for test_user_id, test_password in (test_users or {}).items():
        if auth_service.get_account(test_user_id) is None:
            auth_service.create_user(
                user_id=test_user_id, name=f"Test {test_user_id}",
                email_identifier=test_user_id, password=test_password,
                must_change_password=False,
            )

    try:
        import multipart  # noqa: F401

        multipart_ok = True
    except Exception:
        multipart_ok = False

    if multipart_ok:

        @app.post("/api/import-health-record")
        async def import_health_record(
            request: Request,
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
            if production_mode:
                body["patient_id"] = _request_patient(request)
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
            request: Request,
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
            patient_id = _request_patient(request) if production_mode else None
            for raw in meta.get("items") or meta.get("files") or []:
                if not isinstance(raw, dict):
                    continue
                entry = _strip_banned_path_keys(raw)
                entry.pop("content_path", None)
                if "filename" in entry:
                    entry["filename"] = sanitize_filename(entry.get("filename"))
                if patient_id:
                    entry["patient_id"] = patient_id
                items.append(entry)

            for upload in files or []:
                content = await upload.read()
                items.append(
                    {
                        "content": content,
                        "filename": sanitize_filename(upload.filename or "upload.bin"),
                        "mime_type": upload.content_type or "application/octet-stream",
                        "size_bytes": len(content),
                        **({"patient_id": patient_id} if patient_id else {}),
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
    async def import_health_records_batch_json(body: dict[str, Any], request: Request) -> JSONResponse:
        """Structured JSON batch (works without python-multipart)."""
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "errors": ["body must be an object"]}, status_code=400)
        scoped = dict(body)
        if production_mode:
            pid = _request_patient(request)
            scoped["items"] = [
                {**item, "patient_id": pid} for item in (body.get("items") or body.get("files") or [])
                if isinstance(item, dict)
            ]
        report = _run_json_batch(batch_service, scoped)
        status = 200 if report.get("ok") or report.get("partial_success") else 400
        if report.get("status") == "rejected":
            status = 400
        return JSONResponse(_sanitize_value(report), status_code=status)

    @app.post("/api/import-health-record/json")
    async def import_health_record_json(body: dict[str, Any], request: Request) -> JSONResponse:
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "errors": ["body must be an object"]}, status_code=400)
        cleaned = _strip_banned_path_keys(body)
        cleaned.pop("content_path", None)
        if production_mode:
            cleaned["patient_id"] = _request_patient(request)
        result = service.import_health_record(cleaned)
        return JSONResponse(_sanitize_value(result))

    @app.get("/api/health-vault/batch-limits")
    def batch_limits() -> dict[str, Any]:
        return get_batch_config().to_dict()

    @app.get("/api/health-vault/timeline")
    def timeline(
        request: Request,
        unified: bool = False,
        metric: str | None = None,
        metrics: str | None = None,
    ) -> JSONResponse:
        """Authenticated consumer timeline. Slim JSON only — never the HTML app shell."""
        try:
            patient_id = _request_patient(request) if production_mode else (
                str(getattr(request.state, "patient_id", None) or "default-patient")
            )
        except AuthenticationError as exc:
            return _auth_error(exc)
        from backend.health_vault.timeline import (
            build_consumer_timeline_response,
            build_unified_timeline,
        )

        entries = (
            build_unified_timeline(vault, patient_id=patient_id)
            if unified
            else build_timeline(vault, patient_id=patient_id)
        )
        return JSONResponse(
            _sanitize_value(
                build_consumer_timeline_response(
                    entries,
                    unified=bool(unified),
                    metric=metric,
                    metrics=metrics,
                )
            )
        )

    @app.get("/api/health-vault/trends")
    def health_vault_trends(
        request: Request,
        metric: str | None = None,
        metrics: str | None = None,
    ) -> JSONResponse:
        """Authenticated JSON trends surface. Never returns the HTML app shell."""
        try:
            pid = _get_authenticated_patient(request)
        except AuthenticationError as exc:
            return _auth_error(exc)
        payload = dashboard_service.get_trends_payload(pid, metric=metric, metrics=metrics)
        return JSONResponse(_sanitize_value(payload))

    @app.get("/api/health-vault/doctor-visit")
    def doctor_visit(request: Request) -> dict[str, Any]:
        return _sanitize_value(doctor.generate(patient_id=_request_patient(request) if production_mode else "default-patient"))

    @app.get("/api/health-vault/integrity")
    def integrity(request: Request) -> dict[str, Any]:
        result = vault.verify_integrity()
        return {"ok": bool(result.get("ok")), "scope": "authenticated_patient"} if production_mode else result

    @app.get("/api/health-vault/intelligence")
    def intelligence(request: Request) -> dict[str, Any]:
        from backend.health_vault.health_intelligence import HealthIntelligenceEngine

        pid = _request_patient(request) if production_mode else "default-patient"
        obs = HealthIntelligenceEngine(vault).generate_observations(patient_id=pid)
        return {"observations": obs, "disclaimer": "Observational only — not a diagnosis."}

    @app.get("/api/health-vault/import-log")
    def import_log(request: Request) -> dict[str, Any]:
        entries = vault.import_log()
        if production_mode:
            pid = _request_patient(request)
            entries = [row for row in entries if str(row.get("patient_id") or "default-patient") == pid]
        return _sanitize_value({"entries": entries})

    @app.get("/api/health-vault/executive-briefing")
    def executive_briefing(
        request: Request,
        patient_id: str = "default-patient",
        as_of: str | None = None,
        trend_window: str = "30d",
        category: str | None = None,
    ) -> dict[str, Any]:
        """HC-201I read-only executive health briefing (observational only)."""
        from backend.health_vault.executive_briefing import ExecutiveHealthBriefingEngine

        engine = ExecutiveHealthBriefingEngine(vault)
        payload = engine.generate(
            patient_id=_request_patient(request) if production_mode else patient_id,
            as_of=as_of,
            trend_window=trend_window,
            category=category,
        )
        return _sanitize_value(payload)

    @app.get("/api/health-vault/executive-briefing/print")
    def executive_briefing_print(
        request: Request,
        patient_id: str = "default-patient",
        trend_window: str = "30d",
    ) -> dict[str, Any]:
        from backend.health_vault.executive_briefing import ExecutiveHealthBriefingEngine

        engine = ExecutiveHealthBriefingEngine(vault)
        return _sanitize_value(
            engine.printable_summary(patient_id=_request_patient(request) if production_mode else patient_id, trend_window=trend_window)
        )

    @app.get("/api/health-vault/health-snapshot")
    def health_snapshot(
        request: Request,
        patient_id: str = "default-patient",
        as_of: str | None = None,
        metric: str | None = None,
    ) -> dict[str, Any]:
        """HC-321 read-only consumer Health Snapshot (observational only).

        Optional ``metric`` returns a drill-down payload for one Snapshot card
        (history + stats) without fabricating missing measurements.
        """
        from backend.health_vault.health_snapshot import HealthSnapshotEngine

        engine = HealthSnapshotEngine(vault)
        # Prefer authenticated patient whenever a session is present (production or tests).
        scoped_patient = patient_id
        try:
            if getattr(request.state, "patient_id", None):
                scoped_patient = str(request.state.patient_id)
            elif production_mode:
                scoped_patient = _request_patient(request)
        except Exception:
            if production_mode:
                scoped_patient = _request_patient(request)
        if metric:
            return _sanitize_value(
                engine.metric_detail(
                    metric,
                    patient_id=scoped_patient,
                    as_of=as_of,
                )
            )
        return _sanitize_value(
            engine.generate(
                patient_id=scoped_patient,
                as_of=as_of,
            )
        )

    @app.post("/api/ai-health/import-preview")
    async def ai_health_import_preview(body: dict[str, Any], request: Request) -> JSONResponse:
        """HC-202 — preview AI-extracted records (no vault writes)."""
        from backend.ai_health.bridge import AIHealthBridge

        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "errors": ["body must be an object"]}, status_code=400)
        try:
            scoped = _strip_banned_path_keys(body)
            if production_mode:
                scoped["patient_id"] = _request_patient(request)
            result = AIHealthBridge(store=vault).preview(scoped)
        except ValueError as exc:
            return JSONResponse(
                {"ok": False, "errors": [str(exc)]},
                status_code=400,
            )
        return JSONResponse(_sanitize_value(result))

    @app.post("/api/ai-health/import-confirm")
    async def ai_health_import_confirm(body: dict[str, Any], request: Request) -> JSONResponse:
        """HC-202 — confirmed AI import via canonical ImportPipeline only."""
        from backend.ai_health.bridge import AIHealthBridge

        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "errors": ["body must be an object"]}, status_code=400)
        scoped = _strip_banned_path_keys(body)
        if production_mode:
            scoped["patient_id"] = _request_patient(request)
        result = AIHealthBridge(store=vault).confirm(scoped)
        status = 200 if result.get("ok") or result.get("partial_success") else 400
        if result.get("status") == "rejected":
            status = 400
        return JSONResponse(_sanitize_value(result), status_code=status)

    @app.get("/api/ai-health/import-history")
    def ai_health_import_history(request: Request, limit: int = 50) -> dict[str, Any]:
        from backend.ai_health.bridge import AIHealthBridge

        result = AIHealthBridge(store=vault).import_history(limit=limit)
        if production_mode:
            pid = _request_patient(request)
            if isinstance(result, dict) and isinstance(result.get("entries"), list):
                result = {**result, "entries": [r for r in result["entries"] if str(r.get("patient_id") or "default-patient") == pid]}
        return _sanitize_value(result)

    # --- HC-302 Continuous Monitoring ---

    @app.get("/api/monitoring/status")
    def monitoring_status(request: Request, patient_id: str = "default-patient") -> dict[str, Any]:
        pid = _request_patient(request) if production_mode else patient_id
        return _sanitize_value(monitoring_status_handler(patient_id=pid, store=vault))

    @app.get("/api/monitoring/connectors")
    def monitoring_connectors(request: Request, include_simulated: bool = False) -> dict[str, Any]:
        return _sanitize_value(
            monitoring_connectors_handler(include_simulated=include_simulated, store=vault)
        )

    @app.post("/api/monitoring/sync")
    async def monitoring_sync(request: Request, body: dict[str, Any] | None = None) -> JSONResponse:
        scoped = dict(body or {})
        if production_mode:
            scoped["patient_id"] = _request_patient(request)
        result = monitoring_sync_handler(scoped, store=vault)
        return JSONResponse(_sanitize_value(result), status_code=200 if result.get("ok") or result.get("status") in {
            "UNAVAILABLE", "IMPORT_REQUIRED", "permission_required", "permission_denied"
        } else (200 if result.get("ok") else 400))

    @app.post("/api/monitoring/evaluate")
    async def monitoring_evaluate(request: Request, body: dict[str, Any] | None = None) -> JSONResponse:
        scoped = dict(body or {})
        if production_mode:
            scoped["patient_id"] = _request_patient(request)
        result = monitoring_evaluate_handler(scoped, store=vault)
        return JSONResponse(_sanitize_value(result), status_code=200 if result.get("ok") else 400)

    @app.post("/api/monitoring/scheduler/tick")
    async def monitoring_scheduler_tick(request: Request, body: dict[str, Any] | None = None) -> JSONResponse:
        scoped = dict(body or {})
        if production_mode:
            scoped["patient_id"] = _request_patient(request)
        result = monitoring_scheduler_tick_handler(scoped, store=vault)
        return JSONResponse(_sanitize_value(result))

    # --- HC-303A Android companion ---

    @app.post("/api/companion/pair/start")
    async def companion_pair_start(request: Request) -> JSONResponse:
        try:
            patient_id = _get_authenticated_patient(request)
        except AuthenticationError as exc:
            return _auth_error(exc)
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        result = companion_pair_start_handler(body, store=vault, patient_id=patient_id)
        code = 200 if result.get("ok") else 400
        return JSONResponse(_sanitize_value(result), status_code=code)

    @app.post("/api/companion/pair/confirm")
    async def companion_pair_confirm(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        result = companion_pair_confirm_handler(body, store=vault)
        return JSONResponse(_sanitize_value(result), status_code=200 if result.get("ok") else 400)

    @app.get("/api/companion/devices")
    def companion_devices(request: Request, include_revoked: bool = False) -> JSONResponse:
        try:
            patient_id = _get_authenticated_patient(request)
        except AuthenticationError as exc:
            return _auth_error(exc)
        devices = _companion_pairing(vault).list_devices(
            include_revoked=include_revoked, patient_id=patient_id
        )
        return JSONResponse(_sanitize_value({"ok": True, "devices": devices}))

    @app.delete("/api/companion/devices/{device_id}")
    async def companion_revoke(device_id: str, request: Request) -> JSONResponse:
        try:
            patient_id = _get_authenticated_patient(request)
        except AuthenticationError as exc:
            return _auth_error(exc)
        result = _companion_pairing(vault).revoke_device(device_id, patient_id=patient_id)
        code = 200 if result.get("ok") else (403 if result.get("status") == "forbidden" else 400)
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
    def guardian_status(request: Request, patient_id: str = "default-patient") -> dict[str, Any]:
        return _sanitize_value(guardian_status_handler(patient_id=_request_patient(request) if production_mode else patient_id, store=vault))

    @app.get("/api/guardian/alerts")
    def guardian_alerts(request: Request, patient_id: str = "default-patient", active_only: bool = True) -> dict[str, Any]:
        return _sanitize_value(
            guardian_alerts_handler(patient_id=_request_patient(request) if production_mode else patient_id, active_only=active_only, store=vault)
        )

    @app.get("/api/guardian/alerts/history")
    def guardian_alerts_history(request: Request, patient_id: str = "default-patient") -> dict[str, Any]:
        return _sanitize_value(
            guardian_alerts_handler(patient_id=_request_patient(request) if production_mode else patient_id, active_only=False, store=vault)
        )

    @app.post("/api/guardian/alerts/{alert_id}/acknowledge")
    async def guardian_ack(alert_id: str, request: Request, body: dict[str, Any] | None = None) -> JSONResponse:
        scoped = dict(body or {})
        if production_mode:
            pid = _request_patient(request)
            if not any(str(a.get("alert_id")) == alert_id and str(a.get("patient_id") or "default-patient") == pid for a in vault.list_alerts()):
                return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
            scoped["patient_id"] = pid
        result = guardian_acknowledge_handler(alert_id, scoped, store=vault)
        return JSONResponse(_sanitize_value(result), status_code=200 if result.get("ok") else 400)

    @app.post("/api/guardian/alerts/{alert_id}/resolve")
    async def guardian_resolve(alert_id: str, request: Request, body: dict[str, Any] | None = None) -> JSONResponse:
        scoped = dict(body or {})
        if production_mode:
            pid = _request_patient(request)
            if not any(str(a.get("alert_id")) == alert_id and str(a.get("patient_id") or "default-patient") == pid for a in vault.list_alerts()):
                return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
            scoped["patient_id"] = pid
        result = guardian_resolve_handler(alert_id, scoped, store=vault)
        return JSONResponse(_sanitize_value(result), status_code=200 if result.get("ok") else 400)

    @app.get("/api/guardian/baselines")
    def guardian_baselines(request: Request, patient_id: str = "default-patient") -> dict[str, Any]:
        return _sanitize_value(guardian_baselines_handler(patient_id=_request_patient(request) if production_mode else patient_id, store=vault))

    @app.get("/api/guardian/cgm/sensors")
    def guardian_cgm_sensors(request: Request, patient_id: str = "default-patient") -> dict[str, Any]:
        return _sanitize_value(cgm_sensors_handler(patient_id=_request_patient(request) if production_mode else patient_id, store=vault))

    @app.post("/api/guardian/cgm/sensors/register")
    async def guardian_cgm_register(body: dict[str, Any], request: Request) -> JSONResponse:
        scoped = dict(body)
        if production_mode:
            scoped["patient_id"] = _request_patient(request)
        result = cgm_register_handler(scoped, store=vault)
        return JSONResponse(_sanitize_value(result))

    @app.post("/api/guardian/cgm/sensors/{sensor_id}/activate")
    async def guardian_cgm_activate(sensor_id: str, request: Request, body: dict[str, Any] | None = None) -> JSONResponse:
        scoped = dict(body or {})
        if production_mode:
            pid = _request_patient(request)
            if not any(str(s.get("sensor_id")) == sensor_id and str(s.get("patient_id") or "default-patient") == pid for s in vault.list_cgm_sensors()):
                return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
            scoped["patient_id"] = pid
        result = cgm_activate_handler(sensor_id, scoped, store=vault)
        return JSONResponse(_sanitize_value(result), status_code=200 if result.get("ok") else 400)

    @app.post("/api/guardian/cgm/sensors/{sensor_id}/fail")
    async def guardian_cgm_fail(sensor_id: str, request: Request, body: dict[str, Any] | None = None) -> JSONResponse:
        scoped = dict(body or {})
        if production_mode:
            pid = _request_patient(request)
            if not any(str(s.get("sensor_id")) == sensor_id and str(s.get("patient_id") or "default-patient") == pid for s in vault.list_cgm_sensors()):
                return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
            scoped["patient_id"] = pid
        result = cgm_fail_handler(sensor_id, scoped, store=vault)
        return JSONResponse(_sanitize_value(result), status_code=200 if result.get("ok") else 400)

    @app.post("/api/guardian/cgm/sensors/{sensor_id}/replace")
    async def guardian_cgm_replace(sensor_id: str, body: dict[str, Any], request: Request) -> JSONResponse:
        scoped = dict(body or {})
        if production_mode:
            pid = _request_patient(request)
            if not any(str(s.get("sensor_id")) == sensor_id and str(s.get("patient_id") or "default-patient") == pid for s in vault.list_cgm_sensors()):
                return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
            scoped["patient_id"] = pid
        result = cgm_replace_handler(sensor_id, scoped, store=vault)
        return JSONResponse(_sanitize_value(result), status_code=200 if result.get("ok") else 400)

    @app.get("/api/guardian/cgm/inventory")
    def guardian_cgm_inventory(request: Request, patient_id: str = "default-patient") -> dict[str, Any]:
        return _sanitize_value(cgm_inventory_handler(patient_id=_request_patient(request) if production_mode else patient_id, store=vault))

    @app.post("/api/guardian/cgm/inventory")
    async def guardian_cgm_inventory_update(body: dict[str, Any], request: Request) -> JSONResponse:
        scoped = dict(body)
        if production_mode:
            scoped["patient_id"] = _request_patient(request)
        return JSONResponse(_sanitize_value(cgm_inventory_update_handler(scoped, store=vault)))

    @app.get("/api/guardian/cgm/continuity")
    def guardian_cgm_continuity(request: Request, patient_id: str = "default-patient") -> dict[str, Any]:
        return _sanitize_value(cgm_continuity_handler(patient_id=_request_patient(request) if production_mode else patient_id, store=vault))

    @app.get("/api/guardian/cgm/data-gaps")
    def guardian_data_gaps(request: Request, patient_id: str = "default-patient") -> dict[str, Any]:
        return _sanitize_value(cgm_data_gaps_handler(patient_id=_request_patient(request) if production_mode else patient_id, store=vault))

    @app.post("/api/guardian/evaluate")
    async def guardian_evaluate(request: Request, body: dict[str, Any] | None = None) -> JSONResponse:
        """Development/testing trigger only — not a clinical escalation channel."""
        scoped = dict(body or {})
        if production_mode:
            scoped["patient_id"] = _request_patient(request)
        result = guardian_evaluate_handler(scoped, store=vault)
        return JSONResponse(_sanitize_value(result))

    @app.post("/api/auth/login")
    async def dashboard_login(body: dict[str, Any]) -> JSONResponse:
        user_id = body.get("user_id") or body.get("patient_id")
        try:
            result = auth_service.login(str(user_id or ""), str(body.get("password") or ""))
        except AuthenticationError:
            return JSONResponse({"ok": False, "error": "Invalid credentials", "code": "invalid_credentials"}, status_code=401)
        return JSONResponse({"ok": True, **result})

    @app.get("/api/auth/session")
    async def auth_session(request: Request) -> JSONResponse:
        try:
            return JSONResponse(auth_service.safe_session(_bearer_token(request)))
        except AuthenticationError as exc:
            return JSONResponse({"ok": False, "error": exc.code}, status_code=exc.status_code)

    @app.post("/api/auth/password/change")
    async def auth_password_change(request: Request, body: dict[str, Any]) -> JSONResponse:
        try:
            result = auth_service.change_password(
                _bearer_token(request), str(body.get("current_password") or ""),
                str(body.get("new_password") or ""),
            )
            return JSONResponse({"ok": True, **result})
        except AuthenticationError as exc:
            return JSONResponse({"ok": False, "error": exc.code, "code": exc.code}, status_code=exc.status_code)

    @app.post("/api/auth/logout")
    async def auth_logout(request: Request, body: dict[str, Any] | None = None) -> JSONResponse:
        device_revoked = False
        devices_revoked = 0
        try:
            token = _bearer_token(request)
            account, _ = auth_service.resolve(token, require_full=False)
            if bool((body or {}).get("revoke_companion_devices")):
                for device in vault.list_companion_devices():
                    if str(device.get("patient_id") or "") != account.user_id or device.get("revoked"):
                        continue
                    result = _companion_pairing(vault).revoke_device(
                        str(device.get("device_id") or ""), patient_id=account.user_id
                    )
                    if result.get("ok"):
                        devices_revoked += 1
            device_id = str((body or {}).get("device_id") or "").strip()
            if device_id:
                result = _companion_pairing(vault).revoke_device(
                    device_id, patient_id=account.user_id
                )
                if not result.get("ok"):
                    return JSONResponse(
                        {"ok": False, "error": "Device does not belong to this user", "code": "device_owner_mismatch"},
                        status_code=403,
                    )
                device_revoked = True
            auth_service.logout(token)
        except AuthenticationError:
            pass
        return JSONResponse({
            "ok": True,
            "device_revoked": device_revoked,
            "devices_revoked": devices_revoked,
        })

    from backend.health_vault.privacy_rights import PrivacyDataRightsService, PrivacyRightsError

    privacy_service = PrivacyDataRightsService(vault)

    def _privacy_error(exc: PrivacyRightsError) -> JSONResponse:
        return JSONResponse({"ok": False, "error": exc.code, "code": exc.code}, status_code=exc.status_code)

    @app.get("/api/admin/users")
    async def admin_list_users(request: Request) -> JSONResponse:
        try:
            users = auth_service.list_accounts(_bearer_token(request))
            return JSONResponse({"ok": True, "users": users})
        except AuthenticationError as exc:
            return _auth_error(exc)

    @app.post("/api/admin/users")
    async def admin_create_user(request: Request, body: dict[str, Any]) -> JSONResponse:
        try:
            created = auth_service.admin_create_user(
                _bearer_token(request),
                user_id=str(body.get("user_id") or ""),
                name=str(body.get("name") or ""),
                email_identifier=str(body.get("email_identifier") or body.get("user_id") or ""),
                password=str(body.get("password") or ""),
                role=str(body.get("role") or "user"),
            )
            return JSONResponse({"ok": True, "user": created})
        except AuthenticationError as exc:
            return _auth_error(exc)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc), "code": str(exc)}, status_code=400)

    @app.post("/api/admin/users/{user_id}/status")
    async def admin_set_status(user_id: str, request: Request, body: dict[str, Any]) -> JSONResponse:
        try:
            row = auth_service.set_account_status(
                _bearer_token(request), user_id, str(body.get("status") or "")
            )
            return JSONResponse({"ok": True, "user": row})
        except AuthenticationError as exc:
            return _auth_error(exc)

    @app.post("/api/admin/users/{user_id}/role")
    async def admin_set_role(user_id: str, request: Request, body: dict[str, Any]) -> JSONResponse:
        try:
            row = auth_service.set_role(_bearer_token(request), user_id, str(body.get("role") or ""))
            return JSONResponse({"ok": True, "user": row})
        except AuthenticationError as exc:
            return _auth_error(exc)

    @app.post("/api/admin/users/{user_id}/sessions/revoke")
    async def admin_revoke_sessions(user_id: str, request: Request) -> JSONResponse:
        try:
            result = auth_service.revoke_all_sessions(_bearer_token(request), user_id)
            return JSONResponse(result)
        except AuthenticationError as exc:
            return _auth_error(exc)

    @app.get("/api/privacy/notice")
    async def privacy_notice(request: Request) -> JSONResponse:
        try:
            auth_service.resolve(_bearer_token(request), require_full=True)
            return JSONResponse({"ok": True, **privacy_service.privacy_notice()})
        except AuthenticationError as exc:
            return _auth_error(exc)

    @app.get("/api/privacy/consent")
    async def privacy_consent_get(request: Request) -> JSONResponse:
        try:
            pid = _get_authenticated_patient(request)
            return JSONResponse({"ok": True, **privacy_service.get_consent(pid)})
        except AuthenticationError as exc:
            return _auth_error(exc)

    @app.post("/api/privacy/consent")
    async def privacy_consent_grant(request: Request, body: dict[str, Any]) -> JSONResponse:
        try:
            pid = _get_authenticated_patient(request)
            record = privacy_service.record_consent(
                pid,
                purpose=str(body.get("purpose") or ""),
                notice_version=body.get("privacy_notice_version"),
                provenance=str(body.get("provenance") or "authenticated_user"),
            )
            return JSONResponse({"ok": True, "consent": record})
        except AuthenticationError as exc:
            return _auth_error(exc)
        except PrivacyRightsError as exc:
            return _privacy_error(exc)

    @app.post("/api/privacy/consent/withdraw")
    async def privacy_consent_withdraw(request: Request, body: dict[str, Any]) -> JSONResponse:
        try:
            pid = _get_authenticated_patient(request)
            record = privacy_service.withdraw_consent(pid, purpose=str(body.get("purpose") or ""))
            return JSONResponse({"ok": True, "consent": record})
        except AuthenticationError as exc:
            return _auth_error(exc)
        except PrivacyRightsError as exc:
            return _privacy_error(exc)

    @app.get("/api/privacy/export")
    async def privacy_export(request: Request) -> JSONResponse:
        try:
            pid = _get_authenticated_patient(request)
            package = privacy_service.export_patient_package(pid)
            return JSONResponse({"ok": True, "export": _sanitize_value(package)})
        except AuthenticationError as exc:
            return _auth_error(exc)

    @app.post("/api/privacy/amend")
    async def privacy_amend(request: Request, body: dict[str, Any]) -> JSONResponse:
        try:
            pid = _get_authenticated_patient(request)
            profile = privacy_service.amend_profile(pid, dict(body.get("amendments") or {}))
            return JSONResponse({"ok": True, "profile": profile})
        except AuthenticationError as exc:
            return _auth_error(exc)
        except PrivacyRightsError as exc:
            return _privacy_error(exc)

    @app.post("/api/privacy/deletion/request")
    async def privacy_deletion_request(request: Request, body: dict[str, Any]) -> JSONResponse:
        try:
            pid = _get_authenticated_patient(request)
            result = privacy_service.request_deletion(pid, confirmation=str(body.get("confirmation") or ""))
            return JSONResponse(result)
        except AuthenticationError as exc:
            return _auth_error(exc)
        except PrivacyRightsError as exc:
            return _privacy_error(exc)

    @app.post("/api/privacy/deletion/confirm")
    async def privacy_deletion_confirm(request: Request, body: dict[str, Any]) -> JSONResponse:
        try:
            pid = _get_authenticated_patient(request)
            result = privacy_service.confirm_deletion(
                pid,
                confirmation_token=str(body.get("confirmation_token") or ""),
                confirmation=str(body.get("confirmation") or ""),
            )
            return JSONResponse(result)
        except AuthenticationError as exc:
            return _auth_error(exc)
        except PrivacyRightsError as exc:
            return _privacy_error(exc)

    from backend.health_vault.ops_supportability import (
        SupportabilityError,
        build_readiness_status,
        create_support_bundle,
        support_bundle_bytes,
    )
    from fastapi.responses import Response

    @app.get("/api/ops/readiness")
    async def ops_readiness(request: Request) -> JSONResponse:
        try:
            auth_service.resolve(_bearer_token(request), require_full=True)
        except AuthenticationError as exc:
            return _auth_error(exc)
        companion = {}
        monitoring = {}
        try:
            companion = _companion_delivery(vault).status(authorization=None)
        except Exception:
            companion = {}
        try:
            from backend.health_vault.monitoring.monitoring_engine import MonitoringEngine

            monitoring = MonitoringEngine(vault).build_status(patient_id=_request_patient(request))
        except Exception:
            monitoring = {}
        status = build_readiness_status(
            vault,
            companion_status=companion if isinstance(companion, dict) else {},
            monitoring_status=monitoring if isinstance(monitoring, dict) else {},
            public_origin_reachable=None,
            loopback_ok=True,
        )
        return JSONResponse({"ok": True, "readiness": status})

    @app.post("/api/ops/support-bundle")
    async def ops_support_bundle(request: Request, body: dict[str, Any] | None = None) -> Response:
        try:
            auth_service.require_roles(_bearer_token(request), auth_service.PRIVILEGED_ROLES)
        except AuthenticationError as exc:
            return _auth_error(exc)
        if not bool((body or {}).get("confirm_export")):
            return JSONResponse(
                {"ok": False, "error": "confirm_export_required", "code": "confirm_export_required"},
                status_code=400,
            )
        try:
            readiness = build_readiness_status(vault, loopback_ok=True)
            payload = support_bundle_bytes(vault, readiness=readiness)
        except SupportabilityError as exc:
            return JSONResponse({"ok": False, "error": str(exc), "code": str(exc)}, status_code=400)
        return Response(
            content=payload,
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="healthchecker-support-bundle.zip"',
                "X-HC-Auto-Transmit": "never",
            },
        )

    @app.get("/api/dashboard/summary")
    async def get_dashboard_summary(request: Request) -> JSONResponse:
        try:
            pid = _get_authenticated_patient(request)
        except AuthenticationError as exc:
            return _auth_error(exc)
        summary = dashboard_service.get_summary(pid)
        return JSONResponse(_sanitize_value(summary.to_dict()))

    @app.get("/api/dashboard/preferences")
    async def get_dashboard_preferences(request: Request) -> JSONResponse:
        try:
            pid = _get_authenticated_patient(request)
        except AuthenticationError as exc:
            return _auth_error(exc)
        prefs = dashboard_service.get_preferences(pid)
        return JSONResponse(prefs.to_dict())

    @app.post("/api/dashboard/preferences")
    async def save_dashboard_preferences(request: Request, body: dict[str, Any]) -> JSONResponse:
        try:
            pid = _get_authenticated_patient(request)
        except AuthenticationError as exc:
            return _auth_error(exc)
        from backend.health_vault.models import UserDashboardPreferences
        prefs = UserDashboardPreferences.from_dict(body)
        dashboard_service.save_preferences(pid, prefs)
        return JSONResponse(prefs.to_dict())

    @app.get("/api/records")
    async def list_health_records(
        request: Request,
        category: str | None = None,
        status: str | None = None,
        metric: str | None = None,
        metrics: str | None = None,
        q: str | None = None,
        surface: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> JSONResponse:
        try:
            pid = _get_authenticated_patient(request)
        except AuthenticationError as exc:
            return _auth_error(exc)
        payload = records_service.consumer_records_payload(
            pid,
            category=category,
            status=status,
            metric=metric,
            metrics=metrics,
            q=q,
            surface=surface,
            limit=limit,
            offset=offset,
        )
        return JSONResponse(payload)

    # Register the static upload route before the document-id route so Starlette
    # does not interpret "upload" as a document identifier and return 405.
    if multipart_ok:
        @app.post("/api/records/upload")
        async def upload_health_record(
            request: Request,
            file: UploadFile = File(...)
        ) -> JSONResponse:
            try:
                pid = _get_authenticated_patient(request)
            except AuthenticationError as exc:
                return _auth_error(exc)
            content = await file.read()
            filename = sanitize_filename(file.filename or "upload.bin")
            mime_type = file.content_type or "application/octet-stream"
            result = records_service.upload_record(pid, content, filename, mime_type)
            code = 200 if result.get("ok") else 400
            return JSONResponse(_sanitize_value(result), status_code=code)
    else:
        @app.post("/api/records/upload")
        async def upload_health_record_fallback(request: Request) -> JSONResponse:
            try:
                pid = _get_authenticated_patient(request)
            except AuthenticationError as exc:
                return _auth_error(exc)
            try:
                filename, mime_type, content = _parse_single_multipart(
                    request.headers.get("Content-Type") or "", await request.body()
                )
            except ValueError as exc:
                return JSONResponse({"ok": False, "errors": [str(exc)]}, status_code=400)
            result = records_service.upload_record(pid, content, filename, mime_type)
            code = 200 if result.get("ok") else 400
            return JSONResponse(_sanitize_value(result), status_code=code)

    @app.get("/api/records/{document_id}")
    async def get_health_record_details(document_id: str, request: Request) -> JSONResponse:
        try:
            pid = _get_authenticated_patient(request)
        except AuthenticationError as exc:
            return _auth_error(exc)
        record = records_service.get_record_details(pid, document_id)
        if not record:
            return JSONResponse({"ok": False, "error": "Record not found"}, status_code=404)
        return JSONResponse(_sanitize_value(record.to_detail_dict()))

    @app.get("/api/records/download/{document_id}")
    async def download_health_record(document_id: str, request: Request):
        try:
            pid = _get_authenticated_patient(request)
        except AuthenticationError as exc:
            return _auth_error(exc)

        docs = vault.list_documents()
        doc = None
        for d in docs:
            if d["id"] == document_id:
                if d.get("patient_id", "default-patient") == pid:
                    doc = d
                break
        if not doc:
            return JSONResponse({"ok": False, "error": "Record not found"}, status_code=404)

        try:
            content = vault.read_document_bytes(storage_uri=doc.get("storage_uri"), document_id=document_id)
        except Exception:
            return JSONResponse({"ok": False, "error": "Failed to decrypt record"}, status_code=500)

        from fastapi.responses import Response
        filename = sanitize_filename(doc.get("original_filename") or f"{document_id}.bin")
        media_type = doc.get("mime_type") or "application/octet-stream"
        return Response(
            content=content,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )

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
    patient_id: str | None = None,
) -> dict[str, Any]:
    body = body or {}
    # A patient_id in the body is ignored; only the authenticated argument is trusted.
    return _companion_pairing(store).start_pairing(
        patient_id=str(patient_id or ""), display_name=body.get("display_name")
    )


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
