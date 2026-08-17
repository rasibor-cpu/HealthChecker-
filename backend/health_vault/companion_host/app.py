"""
Companion-only FastAPI application for HC-304B.

Exposes the minimum Companion surface. Does not mount clinical/import/Guardian/AI routes.
"""

from __future__ import annotations

import hmac
from typing import Any

try:
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
except Exception:  # pragma: no cover
    FastAPI = None  # type: ignore[misc,assignment]
    Request = Any  # type: ignore[misc,assignment]
    JSONResponse = Any  # type: ignore[misc,assignment]

from backend.health_vault.companion_host.ack_recovery import recover_abandoned_in_progress_acks
from backend.health_vault.companion_host.activation import HostActivationConfig
from backend.health_vault.companion_host.logging_safe import log_event
from backend.health_vault.companion_host.proxy_trust import (
    cors_deny_headers,
    evaluate_proxy_trust,
    reject_browser_cors,
)
from backend.health_vault.companion_host.rate_limit import (
    OBSERVATION_LIMITER,
    PAIR_CONFIRM_LIMITER,
    PAIR_START_LIMITER,
)
from backend.health_vault.companion.security import MAX_PAYLOAD_BYTES
from backend.health_vault.auth import AuthenticationError, AuthenticationService
from backend.health_vault.vault_store import VaultStore

# Exact inventory of routes this host may expose (method, path template).
COMPANION_ONLY_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/healthz"),
    ("GET", "/readyz"),
    ("POST", "/api/companion/pair/start"),
    ("POST", "/api/companion/pair/confirm"),
    ("GET", "/api/companion/devices"),
    ("DELETE", "/api/companion/devices/{device_id}"),
    ("POST", "/api/companion/observations"),
    ("GET", "/api/companion/status"),
)


def admin_authorized_mandatory(admin_header: str | None, expected_token: str) -> bool:
    """Fail closed: empty expected token is never authorized."""
    expected = (expected_token or "").strip()
    if not expected:
        return False
    return hmac.compare_digest(str(admin_header or ""), expected)


def create_companion_only_app(
    *,
    config: HostActivationConfig,
    store: VaultStore,
) -> Any:
    if FastAPI is None:  # pragma: no cover
        raise RuntimeError("fastapi_required_for_companion_host")

    from backend.health_vault.api import (
        companion_devices_handler,
        companion_observations_handler,
        companion_pair_confirm_handler,
        companion_pair_start_handler,
        companion_revoke_handler,
        companion_status_handler,
        _sanitize_value,
    )

    app = FastAPI(
        title="HealthChecker+ Companion Host",
        version="hc304b",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.hc_config = config
    app.state.hc_store = store
    auth_service = AuthenticationService(store)
    app.state.hc_auth_service = auth_service

    def _base_headers() -> dict[str, str]:
        return cors_deny_headers()

    def _json(payload: dict[str, Any], status_code: int = 200) -> JSONResponse:
        return JSONResponse(_sanitize_value(payload), status_code=status_code, headers=_base_headers())

    def _single_header(request: Request, name: str) -> tuple[str | None, bool]:
        try:
            values = [v for v in request.headers.getlist(name) if v]
        except Exception:
            raw = request.headers.get(name)
            values = [raw] if raw else []
        if len(values) > 1:
            return None, True
        return (values[0] if values else None), False

    async def _gate_request(request: Request, *, require_https_origin: bool) -> JSONResponse | None:
        cors_err = reject_browser_cors(request.headers.get("origin"))
        if cors_err:
            return _json({"ok": False, "status": cors_err, "errors": [cors_err]}, 403)

        proto, dup_proto = _single_header(request, "x-forwarded-proto")
        fwd_host, dup_host = _single_header(request, "x-forwarded-host")
        proxy_tok, dup_proxy = _single_header(request, "x-hc-proxy-token")
        duplicate = dup_proto or dup_host or dup_proxy

        client_host = request.client.host if request.client else None
        trust = evaluate_proxy_trust(
            config=config,
            client_host=client_host,
            forwarded_proto=proto,
            forwarded_host=fwd_host,
            host_header=request.headers.get("host"),
            path=request.url.path,
            proxy_token_header=proxy_tok,
            duplicate_forwarded=duplicate,
        )
        if not trust.ok:
            log_event("proxy_trust_rejected", error=trust.error)
            return _json({"ok": False, "status": trust.error, "errors": [trust.error]}, 403)
        if require_https_origin and not trust.tls_enabled and request.url.path.rstrip("/") not in {
            "/healthz",
            "/readyz",
        }:
            return _json({"ok": False, "status": "https_required", "errors": ["https_required"]}, 403)
        request.state.hc_trust = trust
        return None

    async def _read_json_object(request: Request) -> tuple[dict[str, Any] | None, JSONResponse | None]:
        import json

        ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
        if ctype and ctype not in {"application/json", "text/json"}:
            return None, _json(
                {"ok": False, "status": "unsupported_media_type", "errors": ["content_type_must_be_json"]},
                415,
            )
        cl_raw = request.headers.get("content-length")
        if cl_raw is not None:
            try:
                content_length = int(cl_raw)
            except ValueError:
                return None, _json(
                    {"ok": False, "status": "malformed", "errors": ["invalid_content_length"]},
                    400,
                )
            if content_length > MAX_PAYLOAD_BYTES:
                return None, _json(
                    {
                        "ok": False,
                        "status": "payload_too_large",
                        "errors": [f"payload_exceeds_{MAX_PAYLOAD_BYTES}_bytes"],
                    },
                    413,
                )
        # Bound read even when Content-Length is absent (chunked).
        raw = bytearray()
        try:
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > MAX_PAYLOAD_BYTES:
                    return None, _json(
                        {
                            "ok": False,
                            "status": "payload_too_large",
                            "errors": [f"payload_exceeds_{MAX_PAYLOAD_BYTES}_bytes"],
                        },
                        413,
                    )
        except Exception:
            return None, _json({"ok": False, "status": "malformed", "errors": ["body_read_failed"]}, 400)
        if not raw:
            return {}, None
        try:
            body = json.loads(bytes(raw).decode("utf-8"))
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        return body, None

    @app.options("/{full_path:path}")
    async def options_deny(full_path: str, request: Request) -> JSONResponse:
        return _json({"ok": False, "status": "method_not_allowed", "errors": ["options_denied"]}, 403)

    @app.get("/healthz")
    async def healthz(request: Request) -> JSONResponse:
        denied = await _gate_request(request, require_https_origin=False)
        if denied:
            return denied
        return _json(
            {
                "ok": True,
                "status": "healthz",
                "service": "companion_host",
                "version": "hc304b",
            }
        )

    @app.get("/readyz")
    async def readyz(request: Request) -> JSONResponse:
        denied = await _gate_request(request, require_https_origin=False)
        if denied:
            return denied
        # Operational readiness only — no vault clinical content.
        recover_abandoned_in_progress_acks(store)
        return _json(
            {
                "ok": True,
                "status": "ready",
                "service": "companion_host",
                "config": config.public_dict(),
            }
        )

    @app.post("/api/companion/pair/start")
    async def pair_start(request: Request) -> JSONResponse:
        denied = await _gate_request(request, require_https_origin=True)
        if denied:
            return denied
        if not admin_authorized_mandatory(
            request.headers.get("X-HC-Companion-Admin"), config.admin_token
        ):
            return _json(
                {"ok": False, "status": "admin_required", "errors": ["companion_admin_required"]},
                403,
            )
        authorization, duplicate_auth = _single_header(request, "authorization")
        if duplicate_auth or not authorization or not authorization.startswith("Bearer "):
            return _json(
                {"ok": False, "status": "unauthorized", "errors": ["authenticated_user_required"]},
                401,
            )
        try:
            account, _ = auth_service.resolve(authorization[7:].strip(), require_full=True)
        except AuthenticationError as exc:
            return _json(
                {"ok": False, "status": "unauthorized", "errors": [exc.code]},
                exc.status_code,
            )
        rl = PAIR_START_LIMITER.check("pair_start")
        if not rl.allowed:
            return _json({"ok": False, "status": "rate_limited", "errors": ["rate_limited"]}, 429)
        body, err = await _read_json_object(request)
        if err:
            return err
        result = companion_pair_start_handler(
            body,
            store=store,
            admin_header=request.headers.get("X-HC-Companion-Admin"),
            patient_id=account.user_id,
        )
        code = 200 if result.get("ok") else (403 if result.get("status") == "admin_required" else 400)
        log_event("pair_start", ok=bool(result.get("ok")), status=result.get("status"))
        return _json(result, code)

    @app.post("/api/companion/pair/confirm")
    async def pair_confirm(request: Request) -> JSONResponse:
        denied = await _gate_request(request, require_https_origin=True)
        if denied:
            return denied
        # Confirm remains pair-code gated (no admin), but rate-limited.
        client = request.client.host if request.client else "unknown"
        rl = PAIR_CONFIRM_LIMITER.check(f"pair_confirm:{client}")
        if not rl.allowed:
            return _json({"ok": False, "status": "rate_limited", "errors": ["rate_limited"]}, 429)
        body, err = await _read_json_object(request)
        if err:
            return err
        result = companion_pair_confirm_handler(body, store=store)
        log_event("pair_confirm", ok=bool(result.get("ok")), status=result.get("status"))
        return _json(result, 200 if result.get("ok") else 400)

    @app.get("/api/companion/devices")
    async def devices(request: Request, include_revoked: bool = False) -> JSONResponse:
        denied = await _gate_request(request, require_https_origin=True)
        if denied:
            return denied
        if not admin_authorized_mandatory(
            request.headers.get("X-HC-Companion-Admin"), config.admin_token
        ):
            return _json(
                {
                    "ok": False,
                    "status": "admin_required",
                    "errors": ["companion_admin_required"],
                    "devices": [],
                },
                403,
            )
        result = companion_devices_handler(
            include_revoked=include_revoked,
            store=store,
            admin_header=request.headers.get("X-HC-Companion-Admin"),
        )
        code = 200 if result.get("ok", True) and result.get("status") != "admin_required" else 403
        return _json(result, code)

    @app.delete("/api/companion/devices/{device_id}")
    async def revoke(device_id: str, request: Request) -> JSONResponse:
        denied = await _gate_request(request, require_https_origin=True)
        if denied:
            return denied
        if not admin_authorized_mandatory(
            request.headers.get("X-HC-Companion-Admin"), config.admin_token
        ):
            return _json(
                {"ok": False, "status": "admin_required", "errors": ["companion_admin_required"]},
                403,
            )
        result = companion_revoke_handler(
            device_id, store=store, admin_header=request.headers.get("X-HC-Companion-Admin")
        )
        code = 200 if result.get("ok") else (403 if result.get("status") == "admin_required" else 400)
        log_event("device_revoke", ok=bool(result.get("ok")), status=result.get("status"))
        return _json(result, code)

    @app.post("/api/companion/observations")
    async def observations(request: Request) -> JSONResponse:
        denied = await _gate_request(request, require_https_origin=True)
        if denied:
            return denied
        try:
            auth_values = request.headers.getlist("Authorization")
        except Exception:
            auth_values = (
                [request.headers.get("Authorization")] if request.headers.get("Authorization") else []
            )
        auth_values = [v for v in auth_values if v]
        if len(auth_values) > 1:
            return _json(
                {
                    "ok": False,
                    "status": "unauthorized",
                    "errors": ["duplicate_authorization_header"],
                },
                401,
            )
        if request.query_params.get("token") or request.query_params.get("authorization"):
            return _json(
                {
                    "ok": False,
                    "status": "unauthorized",
                    "errors": ["credentials_in_query_rejected"],
                },
                401,
            )
        auth = auth_values[0] if auth_values else ""
        # Rate-limit by peer only — never by attacker-controlled Authorization material.
        peer = request.client.host if request.client else "unknown"
        rl = OBSERVATION_LIMITER.check(f"obs:peer:{peer}")
        if not rl.allowed:
            return _json({"ok": False, "status": "rate_limited", "errors": ["rate_limited"]}, 429)

        recover_abandoned_in_progress_acks(store)

        body, err = await _read_json_object(request)
        if err:
            return err
        content_length = len(str(body).encode("utf-8")) if body is not None else 0

        trust = getattr(request.state, "hc_trust", None)
        tls_enabled = bool(trust.tls_enabled) if trust else True
        result = companion_observations_handler(
            body,
            authorization=auth_values[0] if auth_values else None,
            store=store,
            tls_enabled=tls_enabled,
            local_dev=False,
            content_length=content_length,
        )
        status = 200 if result.get("ok") or result.get("status") == "duplicate_ack" else 400
        if result.get("status") == "unauthorized":
            status = 401
        if result.get("status") == "revoked":
            status = 403
        if result.get("status") == "payload_too_large":
            status = 413
        if result.get("status") == "rate_limited":
            status = 429
        log_event(
            "observations_delivery",
            ok=bool(result.get("ok")),
            status=result.get("status"),
        )
        return _json(result, status)

    @app.get("/api/companion/status")
    async def status(request: Request) -> JSONResponse:
        denied = await _gate_request(request, require_https_origin=True)
        if denied:
            return denied
        auth = request.headers.get("Authorization")
        result = companion_status_handler(store=store, authorization=auth)
        return _json(result)

    return app


def build_activated_app(
    *,
    environ: dict[str, str] | None = None,
    repo_root: Any = None,
) -> tuple[Any, HostActivationConfig, VaultStore]:
    """
    Full fail-closed startup: validate activation → apply secrets → prepare vault → create app.
    Does not bind a socket.
    """
    from pathlib import Path

    from backend.health_vault.companion_host.activation import (
        apply_secrets_to_environ,
        load_and_validate_activation,
    )
    from backend.health_vault.companion_host.vault_boundary import prepare_monitoring_vault

    config = load_and_validate_activation(environ=environ, repo_root=Path(repo_root) if repo_root else None)
    if environ is not None:
        apply_secrets_to_environ(config, environ=environ)
    # Companion security helpers read pepper/admin from process env.
    # Tests that pass environ= must restore these keys after the request lifecycle
    # (see autouse fixtures in HC-304* tests) so other suites are not polluted.
    apply_secrets_to_environ(config, environ=None)
    store = prepare_monitoring_vault(config.monitoring_vault_root)
    recover_abandoned_in_progress_acks(store)
    app = create_companion_only_app(config=config, store=store)
    return app, config, store
