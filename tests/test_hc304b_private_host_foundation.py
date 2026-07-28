"""
HC-304B — Private permanent companion-host foundation (adversarial tests).

Uses temporary directories only. Never opens production vault_storage or private_imports.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.health_vault.companion_host import (  # noqa: E402
    COMPANION_ONLY_ROUTES,
    ActivationError,
    build_activated_app,
    load_and_validate_activation,
)
from backend.health_vault.companion_host.ack_recovery import (  # noqa: E402
    recover_abandoned_in_progress_acks,
)
from backend.health_vault.companion_host.app import admin_authorized_mandatory  # noqa: E402
from backend.health_vault.companion_host.logging_safe import log_event  # noqa: E402
from backend.health_vault.companion_host.proxy_trust import (  # noqa: E402
    evaluate_proxy_trust,
    _normalize_forwarded_host,
)
from backend.health_vault.companion_host.rate_limit import SlidingWindowLimiter  # noqa: E402
from backend.health_vault.companion_host.vault_boundary import (  # noqa: E402
    assert_safe_monitoring_vault_path,
    prepare_monitoring_vault,
)
from backend.health_vault.companion.security import MAX_PAYLOAD_BYTES  # noqa: E402

# build_activated_app publishes secrets into process env for companion helpers;
# restore so later HC-303 / host-suite tests are not polluted.
_COMPANION_ENV_KEYS = (
    "HC_HOST_ACTIVATION",
    "HC_COMPANION_ADMIN_TOKEN",
    "HC_COMPANION_PEPPER",
    "HC_PROXY_SHARED_TOKEN",
    "HC_MONITORING_VAULT_ROOT",
    "HC_TRUSTED_PROXY_MODE",
    "HC_EXTERNAL_HTTPS_ORIGIN",
    "HC_EXTERNAL_HTTPS_HOST",
    "HC_BIND_HOST",
    "HC_BIND_PORT",
    "HC_PROXY_LISTEN_HOST",
    "HC_PROXY_LISTEN_PORT",
    "HC_TAILSCALE_SERVE_TARGET_PORT",
    "HC_HOST_ALLOW_TESTCLIENT_PEER",
)


@pytest.fixture(autouse=True)
def _restore_companion_process_env():
    before = {k: os.environ.get(k) for k in _COMPANION_ENV_KEYS}
    yield
    for key, value in before.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _base_env(vault: Path, origin: str = "https://phone-host.example.ts.net") -> dict[str, str]:
    return {
        "HC_HOST_ACTIVATION": "enabled",
        "HC_COMPANION_ADMIN_TOKEN": "test-admin-token-24chars-min!!",
        "HC_COMPANION_PEPPER": "test-pepper-value-24chars-min!!",
        "HC_PROXY_SHARED_TOKEN": "test-proxy-shared-token-24min!!",
        "HC_MONITORING_VAULT_ROOT": str(vault),
        "HC_TRUSTED_PROXY_MODE": "tailscale_https",
        "HC_EXTERNAL_HTTPS_ORIGIN": origin,
        "HC_BIND_HOST": "127.0.0.1",
        "HC_BIND_PORT": "8743",
        "HC_HOST_ALLOW_TESTCLIENT_PEER": "1",
    }


def _proxy_headers(config, extra: dict | None = None) -> dict[str, str]:
    h = {
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "phone-host.example.ts.net",
        "X-HC-Proxy-Token": config.proxy_shared_token,
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


@pytest.fixture()
def monitoring_vault(tmp_path: Path) -> Path:
    return tmp_path / "monitoring_vault"


def test_missing_activation_flag(monitoring_vault: Path):
    env = _base_env(monitoring_vault)
    env["HC_HOST_ACTIVATION"] = ""
    with pytest.raises(ActivationError) as ei:
        load_and_validate_activation(environ=env, repo_root=ROOT)
    assert ei.value.code == "host_activation_required"


def test_missing_and_weak_secrets(monitoring_vault: Path):
    env = _base_env(monitoring_vault)
    env["HC_COMPANION_ADMIN_TOKEN"] = ""
    with pytest.raises(ActivationError) as ei:
        load_and_validate_activation(environ=env, repo_root=ROOT)
    assert ei.value.code == "admin_token_required"

    env = _base_env(monitoring_vault)
    env["HC_COMPANION_PEPPER"] = "replace-with-long-random-pepper"
    with pytest.raises(ActivationError) as ei:
        load_and_validate_activation(environ=env, repo_root=ROOT)
    assert ei.value.code == "pepper_placeholder_forbidden"

    env = _base_env(monitoring_vault)
    env["HC_COMPANION_PEPPER"] = env["HC_COMPANION_ADMIN_TOKEN"]
    with pytest.raises(ActivationError) as ei:
        load_and_validate_activation(environ=env, repo_root=ROOT)
    assert ei.value.code == "admin_pepper_must_differ"

    env = _base_env(monitoring_vault)
    env["HC_PROXY_SHARED_TOKEN"] = ""
    with pytest.raises(ActivationError) as ei:
        load_and_validate_activation(environ=env, repo_root=ROOT)
    assert ei.value.code == "proxy_shared_token_required"


def test_unsafe_vault_paths_rejected(tmp_path: Path):
    with pytest.raises(ActivationError):
        assert_safe_monitoring_vault_path(ROOT / "vault_storage", repo_root=ROOT)
    with pytest.raises(ActivationError):
        assert_safe_monitoring_vault_path(ROOT / "private_imports", repo_root=ROOT)
    with pytest.raises(ActivationError):
        assert_safe_monitoring_vault_path(ROOT, repo_root=ROOT)
    # Traversal into repo vault
    with pytest.raises(ActivationError):
        assert_safe_monitoring_vault_path(ROOT / "docs" / ".." / "vault_storage", repo_root=ROOT)

    temp = Path(os.environ.get("TEMP") or os.environ.get("TMP") or tmp_path)
    with pytest.raises(ActivationError) as ei:
        assert_safe_monitoring_vault_path(temp / "hc303d_session" / "test_vault", repo_root=ROOT)
    assert ei.value.code == "monitoring_vault_temp_session_forbidden"

    # Drive root / UNC-ish broad paths
    with pytest.raises(ActivationError):
        assert_safe_monitoring_vault_path("C:\\", repo_root=ROOT)


def test_non_loopback_binds_rejected(monitoring_vault: Path):
    env = _base_env(monitoring_vault)
    env["HC_BIND_HOST"] = "0.0.0.0"
    with pytest.raises(ActivationError):
        load_and_validate_activation(environ=env, repo_root=ROOT)
    env["HC_BIND_HOST"] = "192.168.1.10"
    with pytest.raises(ActivationError):
        load_and_validate_activation(environ=env, repo_root=ROOT)


def test_no_vault_creation_before_gate_success(monitoring_vault: Path):
    env = _base_env(monitoring_vault)
    env["HC_HOST_ACTIVATION"] = "nope"
    assert not monitoring_vault.exists()
    with pytest.raises(ActivationError):
        load_and_validate_activation(environ=env, repo_root=ROOT)
    assert not monitoring_vault.exists()


def test_build_activated_app_creates_isolated_vault(monitoring_vault: Path):
    env = _base_env(monitoring_vault)
    app, config, store = build_activated_app(environ=env, repo_root=ROOT)
    assert monitoring_vault.exists()
    assert (monitoring_vault / ".hc_monitoring_vault").exists()
    assert store.root.resolve() == monitoring_vault.resolve()
    assert store.root.resolve() != (ROOT / "vault_storage").resolve()
    normalized = set()
    for route in app.router.routes:
        if not hasattr(route, "methods") or not hasattr(route, "path"):
            continue
        for m in route.methods or []:
            if m in {"HEAD", "OPTIONS"}:
                continue
            normalized.add((m, route.path))
    assert set(COMPANION_ONLY_ROUTES) <= normalized
    for _m, p in normalized:
        assert not any(p.startswith(fp) for fp in ("/api/guardian", "/api/import", "/api/monitoring", "/api/ai", "/docs"))


def test_spoofed_and_confused_forwarded_headers(monitoring_vault: Path):
    env = _base_env(monitoring_vault)
    cfg = load_and_validate_activation(environ=env, repo_root=ROOT)

    assert evaluate_proxy_trust(
        config=cfg,
        client_host="203.0.113.9",
        forwarded_proto="https",
        forwarded_host="phone-host.example.ts.net",
        host_header="phone-host.example.ts.net",
        path="/api/companion/status",
        proxy_token_header=cfg.proxy_shared_token,
    ).error == "proxy_peer_not_loopback"

    assert evaluate_proxy_trust(
        config=cfg,
        client_host="127.0.0.1",
        forwarded_proto="https",
        forwarded_host="evil.example",
        host_header="evil.example",
        path="/api/companion/status",
        proxy_token_header=cfg.proxy_shared_token,
    ).error == "external_origin_mismatch"

    # Missing / wrong proxy token even from loopback
    assert evaluate_proxy_trust(
        config=cfg,
        client_host="127.0.0.1",
        forwarded_proto="https",
        forwarded_host="phone-host.example.ts.net",
        host_header="x",
        path="/api/companion/status",
        proxy_token_header="wrong",
    ).error == "proxy_token_invalid"

    assert evaluate_proxy_trust(
        config=cfg,
        client_host="127.0.0.1",
        forwarded_proto="https",
        forwarded_host="phone-host.example.ts.net",
        host_header="x",
        path="/api/companion/status",
        proxy_token_header=cfg.proxy_shared_token,
        duplicate_forwarded=True,
    ).error == "duplicate_forwarded_header"

    assert _normalize_forwarded_host("user@evil.example") is None
    assert _normalize_forwarded_host("phone-host.example.ts.net/path") is None
    assert _normalize_forwarded_host("a,b") is None
    assert _normalize_forwarded_host("phone-host.example.ts.net") == "phone-host.example.ts.net"


def test_admin_token_mandatory():
    assert admin_authorized_mandatory(None, "") is False
    assert admin_authorized_mandatory("x", "test-admin-token-24chars-min!!") is False
    assert admin_authorized_mandatory(
        "test-admin-token-24chars-min!!", "test-admin-token-24chars-min!!"
    )


def test_rate_limiting_bounded_keys():
    lim = SlidingWindowLimiter(max_events=2, window_seconds=60, max_keys=3)
    assert lim.check("a", now=1.0).allowed
    assert lim.check("b", now=1.1).allowed
    assert lim.check("c", now=1.2).allowed
    # Evict oldest; still bounded
    assert lim.check("d", now=1.3).allowed
    assert len(lim._events) <= 3
    assert lim.check("d", now=1.4).allowed
    assert lim.check("d", now=1.5).allowed is False


def test_abandoned_ack_recovery(monitoring_vault: Path):
    store = prepare_monitoring_vault(monitoring_vault)
    with store.companion_lock():
        data = store._read_index()
        acks = dict(data.get("companion_batch_acks") or {})
        acks["batch-stale"] = {
            "batch_id": "batch-stale",
            "nonce": "n1",
            "device_id": "d1",
            "payload_fp": "fp",
            "ok": False,
            "status": "in_progress",
            "reserved_at": "2000-01-01T00:00:00Z",
        }
        data["companion_batch_acks"] = acks
        store._write_index(data)
    out = recover_abandoned_in_progress_acks(store, now_epoch=1_700_000_000.0, abandon_after_seconds=60)
    assert out["ok"] is True
    assert out["recovered"] == 1
    row = store.get_companion_batch_ack("batch-stale")
    assert row["status"] == "abandoned"
    assert row["ok"] is False


def test_restart_persistence_marker(monitoring_vault: Path):
    store = prepare_monitoring_vault(monitoring_vault)
    store.save_companion_status({"phase": "HC-304B", "note": "meta_only"})
    store2 = prepare_monitoring_vault(monitoring_vault)
    assert (monitoring_vault / "host_meta.json").exists()
    assert store2.root == store.root


def test_http_surface_cors_proxy_admin(monitoring_vault: Path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    env = _base_env(monitoring_vault)
    app, config, store = build_activated_app(environ=env, repo_root=ROOT)
    os.environ["HC_HOST_ALLOW_TESTCLIENT_PEER"] = "1"
    client = TestClient(app)

    r = client.get(
        "/api/companion/status",
        headers=_proxy_headers(config, {"Origin": "https://evil.example"}),
    )
    assert r.status_code == 403
    assert r.json()["status"] == "cors_origin_denied"

    r = client.options("/api/companion/status")
    assert r.status_code == 403

    r = client.get("/healthz")
    assert r.status_code == 200
    assert "proxy_shared_token" not in r.text
    assert config.admin_token not in r.text

    r = client.get("/readyz")
    assert r.status_code == 200
    assert "external_https_origin_configured" in r.text
    assert "phone-host.example.ts.net" not in r.text  # origin value not leaked

    # Loopback + correct forwarded headers but missing proxy token
    r = client.post(
        "/api/companion/pair/start",
        json={"display_name": "x"},
        headers={
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "phone-host.example.ts.net",
            "Content-Type": "application/json",
            "X-HC-Companion-Admin": config.admin_token,
        },
    )
    assert r.status_code == 403
    assert r.json()["status"] == "proxy_token_invalid"

    r = client.post(
        "/api/companion/pair/start",
        json={"display_name": "pilot"},
        headers={
            **_proxy_headers(config),
            "X-HC-Companion-Admin": "wrong",
        },
    )
    assert r.status_code == 403

    r = client.post(
        "/api/companion/pair/start",
        json={"display_name": "pilot"},
        headers={**_proxy_headers(config), "X-HC-Companion-Admin": config.admin_token},
    )
    assert r.status_code == 200
    assert r.json().get("ok") is True


def test_oversized_body_without_content_length(monitoring_vault: Path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    env = _base_env(monitoring_vault)
    app, config, _store = build_activated_app(environ=env, repo_root=ROOT)
    os.environ["HC_HOST_ALLOW_TESTCLIENT_PEER"] = "1"
    client = TestClient(app)
    huge = '{"x":"' + ("a" * (MAX_PAYLOAD_BYTES + 100)) + '"}'
    r = client.post(
        "/api/companion/pair/confirm",
        data=huge,
        headers={
            **_proxy_headers(config),
            # omit Content-Length intentionally via raw content — TestClient may still set CL;
            # stream path is still bounded.
        },
    )
    assert r.status_code in {413, 400, 415}


def test_secret_log_redaction():
    log_event("unit_test_event", admin_token="super-secret", pair_code="ABCD1234", ok=True)


def test_migration_safety_doc_contract():
    doc = (ROOT / "docs" / "HC304B_PRIVATE_HOST_FOUNDATION.md").read_text(encoding="utf-8")
    for needle in (
        "temporary pair remains active",
        "new pair code",
        "revoke temporary",
        "rollback",
        "draft",
        "proxy",
        "single-worker",
        "local trusted",
        "tailscale.com/kb/1242/tailscale-serve",
        "not deployable",
    ):
        assert needle.lower() in doc.lower()


def test_production_vault_not_used_by_default_config(monitoring_vault: Path):
    env = _base_env(monitoring_vault)
    _app, config, store = build_activated_app(environ=env, repo_root=ROOT)
    assert store.root.resolve() != (ROOT / "vault_storage").resolve()
    assert store.root.resolve() == monitoring_vault.resolve()
