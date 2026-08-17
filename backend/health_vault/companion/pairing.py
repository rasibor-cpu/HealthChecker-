"""HC-303A companion device pairing — one-time codes, revocable identities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from backend.health_vault.companion.security import (
    PAIR_CODE_MAX_ATTEMPTS,
    PAIR_CODE_REJECT,
    PAIR_CODE_TTL_SECONDS,
    constant_time_code_match,
    constant_time_token_match,
    generate_device_id,
    generate_pair_code,
    generate_token,
    hash_pair_code,
    hash_token,
    parse_bearer_authorization,
    redact_companion_log,
)
from backend.health_vault.event_bus import EventBus, get_event_bus
from backend.health_vault.models import utc_now
from backend.health_vault.vault_store import VaultStore

COMPANION_PAIRED = "CompanionDevicePaired"
COMPANION_REVOKED = "CompanionDeviceRevoked"


def _parse_ts(value: str) -> datetime:
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(text)


class CompanionPairingService:
    """
    Explicit one-time device pairing.

    Host stores pair_code_hash and token_hash only. Plaintext token returned once at confirm.
    """

    def __init__(self, store: VaultStore | None = None, bus: EventBus | None = None) -> None:
        self.store = store or VaultStore()
        self.bus = bus or get_event_bus()
        self._root = self.store.root

    def start_pairing(
        self,
        *,
        patient_id: str,
        display_name: str | None = None,
        now: str | None = None,
    ) -> dict[str, Any]:
        """
        Begin pairing for a patient identity resolved by HC-318 authentication.
        Callers cannot inject an arbitrary patient_id.
        """
        owner_id = str(patient_id or "").strip()
        if not owner_id or owner_id == "default-patient":
            return {"ok": False, "status": "identity_required", "errors": ["authenticated_user_required"]}
        now_ts = now or utc_now()
        expires = (
            _parse_ts(now_ts) + timedelta(seconds=PAIR_CODE_TTL_SECONDS)
        ).astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        code = generate_pair_code()
        session = {
            "session_id": str(uuid4()),
            "pair_code_hash": hash_pair_code(code, store_root=self._root),
            "display_name_hint": (display_name or "Android Companion").strip()[:80],
            "patient_id": owner_id,
            "created_at": now_ts,
            "expires_at": expires,
            "consumed": False,
            "failed_attempts": 0,
        }
        self.store.save_companion_pair_session(session)
        self.bus.publish(
            "CompanionPairingStarted",
            redact_companion_log(
                {"session_id": session["session_id"], "expires_at": expires}
            ),
        )
        return {
            "ok": True,
            "session_id": session["session_id"],
            "pair_code": code,  # returned once to admin UI / operator
            "expires_at": expires,
            "ttl_seconds": PAIR_CODE_TTL_SECONDS,
            "instructions": (
                "Enter this one-time pairing code in the HealthChecker+ Android companion. "
                "Code expires in 10 minutes. TLS is required outside documented local-dev mode."
            ),
        }

    def confirm_pairing(
        self,
        *,
        pair_code: str,
        device_label: str | None = None,
        platform: str = "android",
        app_version: str | None = None,
        now: str | None = None,
    ) -> dict[str, Any]:
        now_ts = now or utc_now()
        code = str(pair_code or "").strip().upper()
        if not code or len(code) > 32:
            return {"ok": False, "errors": [PAIR_CODE_REJECT]}

        code_hash = hash_pair_code(code, store_root=self._root)
        throttle = self.store.get_companion_pair_throttle(code_hash)
        if throttle.get("blocked") or int(throttle.get("attempts") or 0) >= PAIR_CODE_MAX_ATTEMPTS:
            return {"ok": False, "errors": [PAIR_CODE_REJECT]}

        session = self.store.get_companion_pair_session_by_code_hash(code_hash)
        if not session:
            self.store.bump_companion_pair_throttle(
                code_hash, now=now_ts, limit=PAIR_CODE_MAX_ATTEMPTS
            )
            return {"ok": False, "errors": [PAIR_CODE_REJECT]}

        if not constant_time_code_match(
            code, str(session.get("pair_code_hash") or ""), store_root=self._root
        ):
            self.store.bump_companion_pair_throttle(
                code_hash, now=now_ts, limit=PAIR_CODE_MAX_ATTEMPTS
            )
            return {"ok": False, "errors": [PAIR_CODE_REJECT]}

        if session.get("consumed"):
            self.store.bump_companion_pair_throttle(
                code_hash, now=now_ts, limit=PAIR_CODE_MAX_ATTEMPTS
            )
            return {"ok": False, "errors": [PAIR_CODE_REJECT]}

        try:
            if _parse_ts(now_ts) > _parse_ts(str(session.get("expires_at"))):
                self.store.bump_companion_pair_throttle(
                    code_hash, now=now_ts, limit=PAIR_CODE_MAX_ATTEMPTS
                )
                return {"ok": False, "errors": [PAIR_CODE_REJECT]}
        except Exception:
            return {"ok": False, "errors": [PAIR_CODE_REJECT]}

        # Device identity is always host-generated — never caller-selected.
        token = generate_token()
        device_id = generate_device_id()

        consumed = self.store.consume_companion_pair_session(
            str(session["session_id"]),
            now=now_ts,
            device_id=device_id,
        )
        if not consumed:
            # Lost race to concurrent confirmation
            return {"ok": False, "errors": [PAIR_CODE_REJECT]}

        owner_id = str(consumed.get("patient_id") or "").strip()
        if not owner_id or owner_id == "default-patient":
            return {"ok": False, "status": "identity_required", "errors": ["authenticated_user_required"]}

        device = {
            "device_id": device_id,
            "display_name": (device_label or session.get("display_name_hint") or "Android Companion")[
                :80
            ],
            "platform": (platform or "android")[:40],
            "app_version": (str(app_version)[:40] if app_version else None),
            "patient_id": owner_id,
            "paired_at": now_ts,
            "last_seen_at": now_ts,
            "token_hash": hash_token(token, store_root=self._root),
            "token_prefix": token[:6],
            "scopes": ["health_connect.observations"],
            "revoked": False,
            "revoked_at": None,
            "schema_version": "hc.companion_device.v1",
        }
        self.store.upsert_companion_device(device)
        self.store.clear_companion_pair_throttle(code_hash)

        self.bus.publish(
            COMPANION_PAIRED,
            redact_companion_log({"device_id": device_id, "platform": platform}),
        )
        return {
            "ok": True,
            "device_id": device_id,
            "device_token": token,  # shown once — companion must store in Keystore
            "scopes": device["scopes"],
            "paired_at": now_ts,
            "warning": (
                "Store device_token in Android Keystore-backed secure storage. "
                "The host retains only a hash. Token will not be shown again."
            ),
        }

    def revoke_device(
        self, device_id: str, *, patient_id: str | None = None, now: str | None = None
    ) -> dict[str, Any]:
        now_ts = now or utc_now()
        device = self.store.get_companion_device(device_id)
        if not device:
            return {"ok": False, "errors": ["revoke_failed"]}
        if patient_id is not None and str(device.get("patient_id") or "") != str(patient_id):
            return {"ok": False, "status": "forbidden", "errors": ["device_owner_mismatch"]}
        device = dict(device)
        device["revoked"] = True
        device["revoked_at"] = now_ts
        device["token_hash"] = hash_token(generate_token(), store_root=self._root)
        self.store.upsert_companion_device(device)
        self.bus.publish(COMPANION_REVOKED, redact_companion_log({"device_id": device_id}))
        return {"ok": True, "device_id": device_id, "revoked": True, "revoked_at": now_ts}

    def list_devices(
        self, *, include_revoked: bool = False, patient_id: str | None = None
    ) -> list[dict[str, Any]]:
        rows = []
        for d in self.store.list_companion_devices():
            if d.get("revoked") and not include_revoked:
                continue
            if patient_id is not None and str(d.get("patient_id") or "") != str(patient_id):
                continue
            rows.append(
                {
                    "device_id": d.get("device_id"),
                    "display_name": d.get("display_name"),
                    "platform": d.get("platform"),
                    "app_version": d.get("app_version"),
                    "paired_at": d.get("paired_at"),
                    "last_seen_at": d.get("last_seen_at"),
                    "revoked": bool(d.get("revoked")),
                    "scopes": d.get("scopes") or [],
                }
            )
        return rows

    def authenticate(self, authorization_header: str | None) -> dict[str, Any] | None:
        """Return device record if Bearer token is valid and not revoked."""
        token = parse_bearer_authorization(authorization_header)
        if not token:
            return None
        thash = hash_token(token, store_root=self._root)
        device = self.store.get_companion_device_by_token_hash(thash)
        if not device:
            return None
        if device.get("revoked"):
            return None
        if not constant_time_token_match(
            token, str(device.get("token_hash") or ""), store_root=self._root
        ):
            return None
        return dict(device)
