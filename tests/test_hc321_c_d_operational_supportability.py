"""HC321-C-D: readiness, redacted support bundle, recovery guidance."""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.health_vault.api import create_health_vault_app
from backend.health_vault.auth import AuthenticationService
from backend.health_vault.ops_supportability import (
    RECOVERY_GUIDANCE,
    build_readiness_status,
    create_support_bundle,
    redact_structure,
)
from backend.health_vault.vault_store import VaultStore

KEY = b"s" * 32


def _owner_client(tmp_path):
    vault = VaultStore(root=tmp_path / "vault", encryption_key=KEY)
    AuthenticationService(vault, bootstrap_password="owner-password-xx")
    app = create_health_vault_app(store=vault, production=True, bootstrap_password="owner-password-xx")
    client = TestClient(app)
    boot = client.post("/api/auth/login", json={"user_id": "00000", "password": "owner-password-xx"})
    token = boot.json()["token"]
    changed = client.post(
        "/api/auth/password/change",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "owner-password-xx", "new_password": "owner-password-yy"},
    )
    return client, changed.json()["token"], vault


def test_readiness_and_support_bundle_redaction(tmp_path):
    client, token, vault = _owner_client(tmp_path)
    headers = {"Authorization": f"Bearer {token}"}

    readiness = client.get("/api/ops/readiness", headers=headers)
    assert readiness.status_code == 200
    body = readiness.json()["readiness"]
    assert body["phi_included"] is False
    assert body["secrets_included"] is False
    assert "owner-password" not in str(body).lower()
    assert "onboarding_hints" in body
    assert RECOVERY_GUIDANCE["pairing_unavailable"] in body["onboarding_hints"]["pairing"]

    denied = client.post("/api/ops/support-bundle", headers=headers, json={})
    assert denied.status_code == 400

    exported = client.post(
        "/api/ops/support-bundle",
        headers=headers,
        json={"confirm_export": True},
    )
    assert exported.status_code == 200
    assert exported.headers.get("x-hc-auto-transmit") == "never"
    raw = exported.content
    assert raw[:2] == b"PK"
    assert b"vault.key" not in raw
    assert b"owner-password" not in raw
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        manifest = archive.read("support_manifest.json").decode("utf-8")
        assert "auto_transmit" in manifest
        assert "false" in manifest.lower() or '"auto_transmit": false' in manifest


def test_support_bundle_file_and_secret_redaction(tmp_path):
    vault = VaultStore(root=tmp_path / "vault", encryption_key=KEY)
    AuthenticationService(vault, bootstrap_password="owner-password-xx")
    status = build_readiness_status(vault, loopback_ok=False, public_origin_reachable=False)
    assert "runtime_unavailable" in status["failure_states"]
    assert "public_origin_unavailable" in status["failure_states"]
    dirty = {"Authorization": "Bearer super-secret-token-value", "note": "ok"}
    assert redact_structure(dirty)["Authorization"] == "[REDACTED]"
    path = create_support_bundle(
        vault,
        tmp_path / "bundle.zip",
        readiness=status,
        extra={"password": "should-not-appear", "safe": "yes"},
    )
    raw = path.read_bytes()
    assert b"should-not-appear" not in raw
    assert b"super-secret-token-value" not in raw


def test_settings_surface_mentions_support_and_ops():
    root = Path(__file__).resolve().parents[1]
    html = (root / "index.html").read_text(encoding="utf-8")
    js = (root / "js/health_vault/consumer_surfaces.js").read_text(encoding="utf-8")
    assert "consumer_settings_support_bundle" in html
    assert "/api/ops/readiness" in js
    assert "confirm_export" in js
