"""HC321-C-D operational readiness, redacted support bundle, recovery guidance."""

from __future__ import annotations

import io
import json
import os
import platform
import re
import zipfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.health_vault.models import utc_now
from backend.health_vault.recovery import CURRENT_SCHEMA

SECRET_PATTERNS = (
    re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*\S+"),
    re.compile(r"(?i)bearer\s+[a-z0-9\-._~+/]+=*"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)

RECOVERY_GUIDANCE = {
    "runtime_unavailable": (
        "Consumer API is unreachable on 127.0.0.1:8766. Start the HealthChecker "
        "desktop runtime (installed shortcut or start_healthchecker_production.ps1). "
        "Do not use CSS port 8765."
    ),
    "pairing_unavailable": (
        "No active companion pairing. Open Android companion → Pair with host using "
        "a fresh pair code from Settings / companion pair start. Confirm on both sides."
    ),
    "sync_stale": (
        "Health Connect sync looks stale. Confirm companion permissions, unlock the "
        "phone, open Health Connect, then tap Sync in the companion. Host cannot read "
        "Health Connect directly."
    ),
    "public_origin_unavailable": (
        "Public HTTPS origin is unavailable. Local loopback may still work. Check "
        "tunnel/certificate lifecycle runbook (HC321-B1) before exposing clinical APIs."
    ),
    "configuration_missing": (
        "Required vault key or production config is missing. Restore secrets from "
        "operator custody; never invent a vault key. See desktop runtime prerequisite docs."
    ),
    "offline_degraded": (
        "Working offline/degraded: local vault features remain on loopback when the API "
        "is up. Companion sync and public origin may be unavailable until connectivity returns."
    ),
}


class SupportabilityError(RuntimeError):
    pass


def redact_text(value: str) -> str:
    text = value
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def redact_structure(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            key_l = str(key).lower()
            if any(part in key_l for part in ("password", "token", "secret", "authorization", "cookie")):
                out[key] = "[REDACTED]"
            else:
                out[key] = redact_structure(item)
        return out
    if isinstance(value, list):
        return [redact_structure(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def build_readiness_status(
    vault,
    *,
    companion_status: dict[str, Any] | None = None,
    monitoring_status: dict[str, Any] | None = None,
    public_origin_reachable: bool | None = None,
    loopback_ok: bool = True,
) -> dict[str, Any]:
    """Privacy-safe installed runtime readiness (no PHI/secrets)."""
    index = vault._read_index()
    schema = index.get("schema_version")
    devices = []
    try:
        devices = list(vault.list_companion_devices()) if hasattr(vault, "list_companion_devices") else []
    except Exception:
        devices = []
    active_devices = [d for d in devices if not d.get("revoked")]
    hc_sync = (monitoring_status or {}).get("sync_health") or (companion_status or {}).get("companion_status") or {}
    last_sync = None
    if isinstance(hc_sync, dict):
        last_sync = hc_sync.get("last_sync_at") or hc_sync.get("last_success_at") or hc_sync.get("updated_at")

    failures: list[str] = []
    if not loopback_ok:
        failures.append("runtime_unavailable")
    if schema != CURRENT_SCHEMA:
        failures.append("configuration_missing")
    if not active_devices:
        failures.append("pairing_unavailable")
    if public_origin_reachable is False:
        failures.append("public_origin_unavailable")

    guidance = {code: RECOVERY_GUIDANCE[code] for code in failures if code in RECOVERY_GUIDANCE}
    if not failures:
        guidance["ok"] = "Runtime readiness checks passed for local consumer operation."

    return {
        "schema_version": "hc.ops_readiness.v1",
        "generated_at": utc_now(),
        "loopback_api": "up" if loopback_ok else "down",
        "vault_encrypted": bool(getattr(vault, "encrypted", False)),
        "vault_schema": schema,
        "vault_schema_ok": schema == CURRENT_SCHEMA,
        "companion_paired": bool(active_devices),
        "companion_active_device_count": len(active_devices),
        "health_connect_last_sync_at": last_sync,
        "public_origin": (
            "unknown" if public_origin_reachable is None else ("up" if public_origin_reachable else "down")
        ),
        "failure_states": failures,
        "recovery_guidance": guidance,
        "onboarding_hints": {
            "first_run": (
                "Sign in → change bootstrap password → grant privacy consent → pair Android "
                "companion if Health Connect sync is required."
            ),
            "pairing": RECOVERY_GUIDANCE["pairing_unavailable"],
            "offline_degraded": RECOVERY_GUIDANCE["offline_degraded"],
        },
        "phi_included": False,
        "secrets_included": False,
    }


def create_support_bundle(
    vault,
    destination: Path,
    *,
    readiness: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Write a redacted operator support zip. Never auto-transmits externally."""
    payload = {
        "schema_version": "hc.support_bundle.v1",
        "created_at": utc_now(),
        "bundle_id": str(uuid4()),
        "auto_transmit": False,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "python": platform.python_version(),
        },
        "release": _safe_release_metadata(),
        "readiness": readiness or build_readiness_status(vault),
        "vault_root_name": Path(vault.root).name,
        "extra": redact_structure(extra or {}),
    }
    redacted = redact_structure(payload)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp.{os.getpid()}.{uuid4().hex}")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "support_manifest.json",
                json.dumps(redacted, indent=2, sort_keys=True),
            )
            archive.writestr(
                "REDACTION.txt",
                "This bundle is redacted. Do not add vault.key, passwords, or clinical exports.\n"
                "Operator must explicitly choose to send this file; HealthChecker never auto-transmits.\n",
            )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    # Verify no obvious secrets leaked into archive bytes.
    raw = destination.read_bytes()
    if b"BEGIN " in raw and b"PRIVATE KEY" in raw:
        destination.unlink(missing_ok=True)
        raise SupportabilityError("support_bundle_secret_detected")
    return destination


def _safe_release_metadata() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    path = root / "config" / "healthchecker.release.json"
    if not path.exists():
        return {"version": "unknown"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            "version": data.get("version") or data.get("release_version"),
            "schema": data.get("schema_version") or data.get("format"),
        }
    except Exception:
        return {"version": "unreadable"}


def support_bundle_bytes(vault, *, readiness: dict[str, Any] | None = None) -> bytes:
    buffer = io.BytesIO()
    # Build via temp path helpers for consistent redaction checks.
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        path = create_support_bundle(vault, Path(td) / "support.zip", readiness=readiness)
        buffer.write(path.read_bytes())
    return buffer.getvalue()
