"""HC-321A local privileged/offline password recovery.

This module is deliberately not registered with FastAPI. Recovery requires an
encrypted vault plus an elevated, interactive Windows operator process.
"""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

from backend.health_vault.auth import PASSWORD_DAYS, _iso, _now, hash_password, verify_password
from backend.health_vault.models import utc_now


class PasswordRecoveryError(RuntimeError):
    """Privacy-safe recovery failure."""


@dataclass(frozen=True)
class LocalRecoveryAuthorization:
    actor: str
    reason: str
    local: bool = True
    offline: bool = True
    method: str = "local_privileged_owner_recovery"


def require_privileged_windows_operator(authorization: LocalRecoveryAuthorization) -> bool:
    if os.name != "nt" or not authorization.local or not authorization.offline:
        return False
    if authorization.method != "local_privileged_owner_recovery":
        return False
    if not authorization.actor.strip() or not authorization.reason.strip():
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


class LocalPasswordRecoveryService:
    """Atomic encrypted-registry recovery; never reads or returns an old password."""

    def __init__(
        self,
        authentication_service,
        *,
        authorization_check: Callable[[LocalRecoveryAuthorization], bool] = require_privileged_windows_operator,
    ) -> None:
        self.auth = authentication_service
        self.authorization_check = authorization_check

    def recover(
        self,
        *,
        user_id: str,
        new_password: str,
        confirmation: str,
        authorization: LocalRecoveryAuthorization,
    ) -> dict[str, object]:
        if not getattr(self.auth.vault, "encrypted", False):
            raise PasswordRecoveryError("encrypted_production_vault_required")
        if not isinstance(authorization, LocalRecoveryAuthorization) or not self.authorization_check(authorization):
            raise PasswordRecoveryError("local_recovery_authorization_required")
        uid = str(user_id or "").strip()
        if not uid or len(uid) > 128:
            raise PasswordRecoveryError("invalid_user_id")
        if not isinstance(new_password, str) or not isinstance(confirmation, str):
            raise PasswordRecoveryError("password_confirmation_required")
        if new_password != confirmation:
            raise PasswordRecoveryError("password_confirmation_mismatch")
        if len(new_password) < 8:
            raise PasswordRecoveryError("password_policy_violation")

        with self.auth._lock:
            data = self.auth._read()
            row = (data.get("accounts") or {}).get(uid)
            if not isinstance(row, dict):
                raise PasswordRecoveryError("account_not_found")
            if str(row.get("account_status") or "") == "disabled":
                raise PasswordRecoveryError("account_disabled")
            if verify_password(new_password, str(row.get("password_hash") or "")):
                raise PasswordRecoveryError("password_policy_violation")

            changed = _now()
            at = _iso(changed)
            row.update({
                "password_hash": hash_password(new_password),
                "password_changed_at": at,
                "password_expiry_date": _iso(changed + timedelta(days=PASSWORD_DAYS)),
                "must_change_password": False,
                "account_status": "active",
                "password_version": int(row.get("password_version", 1)) + 1,
                "failed_login_count": 0,
                "last_failed_login_at": None,
            })
            revoked = 0
            for session in (data.get("sessions") or {}).values():
                if session.get("user_id") == uid and not session.get("revoked_at"):
                    session["revoked_at"] = at
                    revoked += 1
            data.setdefault("audit", []).append({
                "event_id": __import__("uuid").uuid4().hex,
                "at": utc_now(),
                "action": "authorized_local_password_recovery",
                "user_id": uid,
                "outcome": "success",
                "actor": authorization.actor[:128],
                "method": authorization.method,
                "reason": authorization.reason[:160],
            })
            self.auth._write(data)

        return {
            "user_id": uid,
            "account_status": "active",
            "must_change_password": False,
            "password_changed_at": at,
            "password_expiry_date": row["password_expiry_date"],
            "failed_login_count": 0,
            "sessions_revoked": revoked,
        }


__all__ = [
    "LocalPasswordRecoveryService",
    "LocalRecoveryAuthorization",
    "PasswordRecoveryError",
    "require_privileged_windows_operator",
]
