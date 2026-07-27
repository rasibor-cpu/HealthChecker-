"""HC-303A companion security helpers — hashing, redaction, payload limits."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import Any

from backend.health_vault.monitoring.privacy import redact_for_log

# Production companion delivery limits
MAX_OBSERVATIONS_PER_BATCH = 200
MAX_PAYLOAD_BYTES = 512_000  # ~512 KB
MAX_STRING_FIELD_CHARS = 512
MAX_DEVICE_META_CHARS = 256
PAIR_CODE_TTL_SECONDS = 600
PAIR_CODE_MAX_ATTEMPTS = 5
TOKEN_BYTES = 32
SENT_AT_SKEW_SECONDS = 300  # ±5 minutes
BATCH_ACK_RETENTION = 2000
ADMIN_HEADER = "X-HC-Companion-Admin"

ALLOWED_ACQUISITION_MODES = frozenset({"LIVE", "DELAYED"})
# ECG is unsupported via continuous Health Connect path in HC-303A
SUPPORTED_COMPANION_METRICS = frozenset(
    {
        "heart_rate",
        "resting_hr",
        "oxygen_saturation",
        "systolic_bp",
        "diastolic_bp",
        "sleep_duration",
        "deep_sleep_duration",
        "rem_sleep_duration",
        "steps",
        "activity_minutes",
        "exercise_minutes",
        "weight",
    }
)
UNSUPPORTED_METRICS = frozenset({"ecg_result", "heart_rhythm"})

# Generic pairing failure — do not leak session existence / expiry / reuse.
PAIR_CODE_REJECT = "invalid_or_expired_pair_code"

_PEPPER_ENV = "HC_COMPANION_PEPPER"
_DEFAULT_DEV_PEPPER_WARNING = (
    "HC_COMPANION_PEPPER unset — using vault-local pepper file. "
    "Set HC_COMPANION_PEPPER for production deployments."
)


def _vault_pepper_path(store_root: Path | None = None) -> Path:
    root = Path(store_root or Path(__file__).resolve().parents[3] / "vault_storage")
    return root / ".companion_pepper"


def get_companion_pepper(store_root: Path | None = None) -> bytes:
    """
    Keyed verification material for token/pair-code hashes.

    Prefer HC_COMPANION_PEPPER env. Otherwise persist a vault-local random pepper
    (never commit; lives under vault_storage which is gitignored for indexes).
    """
    env = os.environ.get(_PEPPER_ENV, "").strip()
    if env:
        return env.encode("utf-8")
    path = _vault_pepper_path(store_root)
    try:
        if path.exists():
            raw = path.read_text(encoding="utf-8").strip()
            if raw:
                return raw.encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = secrets.token_hex(32)
        path.write_text(raw, encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return raw.encode("utf-8")
    except OSError:
        # Last resort for ephemeral test roots that disappear mid-run
        return secrets.token_hex(32).encode("utf-8")


def hash_secret(value: str, *, store_root: Path | None = None, purpose: str = "token") -> str:
    """HMAC-SHA256 keyed hash — store hash only, never plaintext secrets."""
    pepper = get_companion_pepper(store_root)
    msg = f"{purpose}:{value}".encode("utf-8")
    return hmac.new(pepper, msg, hashlib.sha256).hexdigest()


def hash_token(token: str, *, store_root: Path | None = None) -> str:
    return hash_secret(str(token), store_root=store_root, purpose="device_token")


def hash_pair_code(code: str, *, store_root: Path | None = None) -> str:
    return hash_secret(str(code).strip().upper(), store_root=store_root, purpose="pair_code")


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def generate_pair_code() -> str:
    # Human-enterable 8-char code (no ambiguous chars); CSPRNG
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))


def generate_device_id() -> str:
    return "hc3a_" + secrets.token_hex(8)


def constant_time_token_match(
    token: str,
    token_hash: str,
    *,
    store_root: Path | None = None,
) -> bool:
    return hmac.compare_digest(
        hash_token(token, store_root=store_root),
        str(token_hash or ""),
    )


def constant_time_code_match(
    code: str,
    code_hash: str,
    *,
    store_root: Path | None = None,
) -> bool:
    return hmac.compare_digest(
        hash_pair_code(code, store_root=store_root),
        str(code_hash or ""),
    )


def parse_bearer_authorization(authorization_header: str | None) -> str | None:
    """
    Strict Bearer parsing. Fail closed on missing/malformed values.
    Does not accept raw tokens without the Bearer scheme.
    """
    if authorization_header is None:
        return None
    header = str(authorization_header)
    if "\n" in header or "\r" in header:
        return None
    parts = header.split(None, 1)
    if len(parts) != 2:
        return None
    scheme, token = parts[0], parts[1].strip()
    if scheme.lower() != "bearer":
        return None
    if not token or any(ch.isspace() for ch in token):
        return None
    return token


def payload_fingerprint(observations: list[Any], *, nonce: str, deletions: list[Any] | None = None) -> str:
    """Stable fingerprint of batch contents for nonce/payload mismatch detection."""
    keys: list[str] = []
    for row in observations or []:
        if isinstance(row, dict):
            keys.append(
                str(row.get("observation_id") or row.get("source_record_id") or "")
                + ":"
                + str(row.get("metric_type") or "")
                + ":"
                + str(row.get("measured_at") or "")
            )
        else:
            keys.append("invalid")
    del_keys = sorted(str(x) for x in (deletions or []) if str(x).strip())
    material = (
        nonce
        + "|"
        + "|".join(sorted(keys))
        + f"|count={len(observations or [])}"
        + "|del="
        + ",".join(del_keys)
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def redact_companion_log(payload: Any) -> Any:
    """Extend monitoring redaction for companion fields."""
    redacted = redact_for_log(payload)
    if isinstance(redacted, dict):
        for key in list(redacted.keys()):
            lk = str(key).lower()
            if lk in {
                "authorization",
                "bearer",
                "pair_code",
                "pairing_code",
                "device_token",
                "token",
                "token_hash",
                "pair_code_hash",
                "nonce",
                "observations",
                "patient_id",
            }:
                redacted[key] = "[redacted]"
    return redacted


def estimate_payload_bytes(body: dict[str, Any]) -> int:
    return len(json.dumps(body, default=str).encode("utf-8"))


def truncate_field(value: Any, limit: int = MAX_STRING_FIELD_CHARS) -> str:
    text = str(value or "")
    if len(text) > limit:
        raise ValueError(f"field_exceeds_{limit}_chars")
    return text


def companion_admin_authorized(admin_header: str | None) -> bool:
    """
    Local/admin control for pairing lifecycle endpoints.

    When HC_COMPANION_ADMIN_TOKEN is set, require matching X-HC-Companion-Admin.
    When unset (default local-first laptop), allow — documented as LAN-trust.
    """
    expected = os.environ.get("HC_COMPANION_ADMIN_TOKEN", "").strip()
    if not expected:
        return True
    provided = str(admin_header or "")
    return hmac.compare_digest(provided, expected)
