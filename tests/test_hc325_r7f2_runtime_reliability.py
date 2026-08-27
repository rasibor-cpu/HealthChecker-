"""HC325-R7F2 — supervisor probe + event-loop reliability.

Fictional fixtures only. Does not talk to live :8766, restart the host,
touch CSS :8765, mutate production vault/auth, or change production.json.
"""

from __future__ import annotations

import ast
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.health_vault.api import create_health_vault_app
from backend.health_vault.dashboard_service import DashboardService
from backend.health_vault.vault_store import VaultStore

ROOT = Path(__file__).resolve().parents[1]
API_PY = ROOT / "backend/health_vault/api.py"
LAUNCHER = ROOT / "scripts" / "start_healthchecker_production.ps1"
BOOTSTRAP = "0" * 6
ANSWERS = [
    {"question_id": "CQ01", "answer": "Westfield School"},
    {"question_id": "CQ02", "answer": "Toronto"},
    {"question_id": "CQ03", "answer": "Buster"},
]
SLOW_SECONDS = 2.5
HEALTHZ_BUDGET_SECONDS = 1.0
FORBIDDEN_PORTS = {8765, 8766}
VAULT_TOUCH_METHODS = (
    "get_profile",
    "update_profile",
    "list_measurements",
    "list_observations",
    "list_documents",
    "_read_index",
    "_write_index",
)


def _authed_app(tmp_path: Path):
    store = VaultStore(root=tmp_path / "vault", encryption_key=b"R" * 32)
    app = create_health_vault_app(
        store, production=True, bootstrap_password="Owner-Temp-Password"
    )
    client = TestClient(app)
    user_id = "10001"
    app.state.auth_service.create_user(
        user_id=user_id,
        name="Consumer",
        email_identifier=user_id,
        password=BOOTSTRAP,
        must_change_password=True,
    )
    login = client.post("/api/auth/login", json={"user_id": user_id, "password": BOOTSTRAP})
    token = login.json()["token"]
    changed = client.post(
        "/api/auth/password/change",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "current_password": BOOTSTRAP,
            "new_password": "Consumer-Permanent-2026",
            "confirm_password": "Consumer-Permanent-2026",
            "recovery_answers": ANSWERS,
        },
    )
    assert changed.status_code == 200, changed.text
    authed = client.post(
        "/api/auth/login",
        json={"user_id": user_id, "password": "Consumer-Permanent-2026"},
    )
    assert authed.status_code == 200
    bearer = authed.json()["token"]
    headers = {"Authorization": f"Bearer {bearer}", "Accept": "application/json"}
    return app, client, headers, store


def _free_loopback_port() -> int:
    for _ in range(40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        if port not in FORBIDDEN_PORTS:
            return port
    raise RuntimeError("no isolated loopback port available")


def _start_uvicorn(app, port: int):
    import uvicorn

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="error",
        access_log=False,
        lifespan="on",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 8
    while time.time() < deadline:
        if server.started:
            return server, thread
        time.sleep(0.05)
    raise RuntimeError("isolated uvicorn did not start")


def _stop_uvicorn(server, thread: threading.Thread) -> None:
    server.should_exit = True
    thread.join(timeout=8)


def _http_get(url: str, headers: dict[str, str] | None = None, timeout: float = 15.0):
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), response.headers.get("Content-Type"), response.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.headers.get("Content-Type") if exc.headers else None, exc.read()


def _healthz_fn():
    tree = ast.parse(API_PY.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name != "create_health_vault_app":
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.AsyncFunctionDef) and child.name == "healthz":
                return child
    raise AssertionError("healthz handler not found")


def test_healthz_source_has_no_patient_or_vault_access():
    fn = _healthz_fn()
    names = {node.id for node in ast.walk(fn) if isinstance(node, ast.Name)}
    attrs = {node.attr for node in ast.walk(fn) if isinstance(node, ast.Attribute)}
    forbidden = {
        "vault",
        "store",
        "dashboard_service",
        "records_service",
        "auth_service",
        "patient_id",
        "_get_authenticated_patient",
        "get_summary",
        "get_preferences",
        "list_measurements",
    }
    assert not (names & forbidden)
    assert not (attrs & forbidden)


def test_healthz_unauthenticated_json_ok(tmp_path: Path):
    store = VaultStore(root=tmp_path / "vault", encryption_key=b"R" * 32)
    app = create_health_vault_app(
        store, production=True, bootstrap_password="Owner-Temp-Password"
    )
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert "application/json" in (response.headers.get("content-type") or "")
    assert response.json() == {"status": "ok"}
    body = json.dumps(response.json())
    for leaked in ("vault", "C:\\", "/Users/", "password", "token", "patient", "8766"):
        assert leaked.lower() not in body.lower()


def test_healthz_does_not_invoke_vault_or_dashboard(tmp_path: Path):
    app, client, _headers, store = _authed_app(tmp_path)
    hits: list[str] = []

    def _wrap(name: str, original):
        def wrapped(*args, **kwargs):
            hits.append(name)
            return original(*args, **kwargs)

        return wrapped

    for name in VAULT_TOUCH_METHODS:
        if hasattr(store, name):
            setattr(store, name, _wrap(name, getattr(store, name)))

    original_summary = DashboardService.get_summary

    def boom_summary(self, patient_id: str):
        hits.append("get_summary")
        return original_summary(self, patient_id)

    with patch.object(DashboardService, "get_summary", boom_summary):
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert hits == []


def test_healthz_not_in_api_auth_boundary():
    text = API_PY.read_text(encoding="utf-8")
    start = text.index("production_public_api = {")
    end = text.index("}", start)
    block = text[start:end]
    assert "/healthz" not in block
    assert 'async def healthz' in text


def test_implicated_async_routes_offload_vault_work():
    text = API_PY.read_text(encoding="utf-8")
    summary = text[text.index("async def get_dashboard_summary") : text.index("async def get_dashboard_preferences")]
    prefs = text[text.index("async def get_dashboard_preferences") : text.index("async def save_dashboard_preferences")]
    records = text[text.index("async def list_health_records") : text.index("async def upload_health_record")]
    assert "await run_in_threadpool(dashboard_service.get_summary" in summary
    assert "dashboard_service.get_summary(pid)" not in summary
    assert "await run_in_threadpool(dashboard_service.get_preferences" in prefs
    assert "await run_in_threadpool(" in records
    assert "records_service.consumer_records_payload" in records
    assert "from fastapi.concurrency import run_in_threadpool" in text


def test_supervisor_probe_contract_in_launcher():
    text = LAUNCHER.read_text(encoding="utf-8")
    assert 'Path = "/healthz"' in text
    assert "$TimeoutMs = 2000" in text or "TimeoutMs = 2000" in text
    assert "$probeFailureThreshold = 3" in text
    assert "$healthPollSeconds = 1" in text
    assert 'State "degraded"' in text
    assert 'State "running"' in text
    assert 'State "restarting"' in text
    assert 'State "failed"' in text
    assert 'State "stopped"' in text
    assert "http://${BindAddress}:${Port}/healthz" in text or "${Path}" in text
    assert 'Create("http://${BindAddress}:${Port}/")' not in text
    assert "production.json" in text
    assert "C:\\ProgramData\\HealthChecker\\config\\production.json" in text


def test_r7f_blocking_handler_starves_healthz():
    app = FastAPI()

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/slow")
    async def slow() -> JSONResponse:
        time.sleep(SLOW_SECONDS)
        return JSONResponse({"ok": True})

    port = _free_loopback_port()
    server, thread = _start_uvicorn(app, port)
    results: dict[str, object] = {}
    try:
        def hit_slow() -> None:
            results["slow"] = _http_get(f"http://127.0.0.1:{port}/slow")

        def hit_healthz() -> None:
            started = time.perf_counter()
            results["health"] = _http_get(f"http://127.0.0.1:{port}/healthz")
            results["health_elapsed"] = time.perf_counter() - started

        slow_thread = threading.Thread(target=hit_slow)
        health_thread = threading.Thread(target=hit_healthz)
        slow_thread.start()
        time.sleep(0.25)
        health_thread.start()
        slow_thread.join(timeout=20)
        health_thread.join(timeout=20)
        assert results["slow"][0] == 200
        assert results["health"][0] == 200
        assert results["health_elapsed"] >= 2.0
    finally:
        _stop_uvicorn(server, thread)


def test_r7f_offloaded_handler_keeps_healthz_responsive():
    app = FastAPI()

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/slow")
    async def slow() -> JSONResponse:
        await run_in_threadpool(time.sleep, SLOW_SECONDS)
        return JSONResponse({"ok": True})

    port = _free_loopback_port()
    server, thread = _start_uvicorn(app, port)
    results: dict[str, object] = {}
    try:
        def hit_slow() -> None:
            results["slow"] = _http_get(f"http://127.0.0.1:{port}/slow")

        def hit_healthz() -> None:
            started = time.perf_counter()
            results["health"] = _http_get(f"http://127.0.0.1:{port}/healthz")
            results["health_elapsed"] = time.perf_counter() - started

        slow_thread = threading.Thread(target=hit_slow)
        health_thread = threading.Thread(target=hit_healthz)
        slow_thread.start()
        time.sleep(0.25)
        health_thread.start()
        slow_thread.join(timeout=20)
        health_thread.join(timeout=20)
        assert results["slow"][0] == 200
        body = json.loads(results["health"][2])
        assert results["health"][0] == 200
        assert body == {"status": "ok"}
        assert results["health_elapsed"] < HEALTHZ_BUDGET_SECONDS
    finally:
        _stop_uvicorn(server, thread)


def test_offloaded_dashboard_summary_does_not_block_healthz(tmp_path: Path):
    app, sync_client, headers, _store = _authed_app(tmp_path)
    sync_client.close()
    original = DashboardService.get_summary

    def slow_summary(self, patient_id: str):
        time.sleep(SLOW_SECONDS)
        return original(self, patient_id)

    port = _free_loopback_port()
    results: dict[str, object] = {}
    with patch.object(DashboardService, "get_summary", slow_summary):
        server, thread = _start_uvicorn(app, port)
        try:
            def hit_summary() -> None:
                results["summary"] = _http_get(
                    f"http://127.0.0.1:{port}/api/dashboard/summary",
                    headers=headers,
                )

            def hit_healthz() -> None:
                started = time.perf_counter()
                results["health"] = _http_get(f"http://127.0.0.1:{port}/healthz")
                results["health_elapsed"] = time.perf_counter() - started

            summary_thread = threading.Thread(target=hit_summary)
            health_thread = threading.Thread(target=hit_healthz)
            summary_thread.start()
            time.sleep(0.25)
            health_thread.start()
            summary_thread.join(timeout=20)
            health_thread.join(timeout=20)
        finally:
            _stop_uvicorn(server, thread)

    assert results["health"][0] == 200
    assert json.loads(results["health"][2]) == {"status": "ok"}
    assert results["health_elapsed"] < HEALTHZ_BUDGET_SECONDS
    assert results["summary"][0] == 200
    summary_body = json.loads(results["summary"][2])
    assert "widgets" in summary_body or "patient_id" in summary_body


def test_authenticated_route_contracts_unchanged(tmp_path: Path):
    _app, client, headers, _store = _authed_app(tmp_path)
    summary = client.get("/api/dashboard/summary", headers=headers)
    prefs = client.get("/api/dashboard/preferences", headers=headers)
    records = client.get("/api/records", headers=headers)
    session = client.get("/api/auth/session", headers=headers)
    assert summary.status_code == 200
    assert prefs.status_code == 200
    assert records.status_code == 200
    assert session.status_code == 200
    assert "application/json" in (summary.headers.get("content-type") or "")
    summary_body = summary.json()
    prefs_body = prefs.json()
    records_body = records.json()
    assert "widgets" in summary_body or "patient_id" in summary_body
    assert "theme" in prefs_body
    assert "records" in records_body or "items" in records_body or "ok" in records_body
    unauth_summary = client.get("/api/dashboard/summary")
    assert unauth_summary.status_code in {401, 403}
    catalog = client.get("/api/auth/recovery/catalog")
    assert catalog.status_code == 200
    assert "questions" in catalog.json()
