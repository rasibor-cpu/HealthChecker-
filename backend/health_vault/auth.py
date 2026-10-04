"""HC-318B production account, password, and opaque-session foundation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
from functools import wraps
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.health_vault.models import UserAccount, utc_now
from backend.health_vault.vault_crypto import decrypt_bytes, encrypt_bytes
from backend.health_vault.auth_factors import matching_totp_counter, new_recovery_codes, new_totp_secret

PASSWORD_DAYS = 90
SESSION_HOURS = 12
CHANGE_SESSION_MINUTES = 10
RECOVERY_MINUTES = 10
TOTP_SETUP_MINUTES = 10
TOTP_LOGIN_MINUTES = 5
TOTP_STEP_SECONDS = 30
TRUSTED_DEVICE_DAYS = 30
# Temporary consumer bootstrap only. Never log, return, or persist this value.
_CONSUMER_BOOTSTRAP = "0" * 6


def is_consumer_bootstrap_password(password: str) -> bool:
    return isinstance(password, str) and password.strip() == _CONSUMER_BOOTSTRAP


def validate_permanent_password(
    new_password: str,
    *,
    confirmation: str | None = None,
    current_password: str | None = None,
) -> None:
    if not isinstance(new_password, str) or not new_password.strip():
        raise AuthenticationError("password_policy_violation", 400)
    if confirmation is not None and new_password != confirmation:
        raise AuthenticationError("password_confirmation_mismatch", 400)
    if len(new_password) < 8 or is_consumer_bootstrap_password(new_password):
        raise AuthenticationError("password_policy_violation", 400)
    if current_password is not None and new_password == current_password:
        raise AuthenticationError("password_policy_violation", 400)


def _env_positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


# Configurable brute-force controls (fail-closed defaults). Evaluated at call time
# so tests and operators can tune without process restart of module constants alone.
def max_failed_logins() -> int:
    return _env_positive_int("HC_AUTH_MAX_FAILED_LOGINS", 5)


def lockout_minutes() -> int:
    return _env_positive_int("HC_AUTH_LOCKOUT_MINUTES", 15)


def min_seconds_between_attempts() -> int:
    return _env_positive_int("HC_AUTH_MIN_SECONDS_BETWEEN_ATTEMPTS", 1)


# Back-compat aliases for importers/tests.
MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15
MIN_SECONDS_BETWEEN_ATTEMPTS = 1
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
_AUTH_CONTEXT = b"auth/registry.v1"


class AuthenticationError(ValueError):
    def __init__(self, code: str = "unauthorized", status_code: int = 401) -> None:
        super().__init__(code)
        self.code, self.status_code = code, status_code


class AuthenticationStateError(RuntimeError):
    """Fail-closed missing or corrupt production authentication state."""


def _synchronized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc) if value else None


def hash_password(password: str) -> str:
    if not isinstance(password, str) or not password:
        raise ValueError("password_required")
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    b64 = lambda raw: base64.urlsafe_b64encode(raw).decode("ascii")
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${b64(salt)}${b64(derived)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_text, hash_text = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt, expected = base64.urlsafe_b64decode(salt_text), base64.urlsafe_b64decode(hash_text)
        actual = hashlib.scrypt(password.encode(), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, UnicodeError):
        return False


class AuthenticationService:
    """Encrypted account registry with hash-only opaque sessions."""

    def __init__(
        self,
        vault,
        *,
        bootstrap_password: str | None = None,
        allow_development_bootstrap: bool = False,
    ) -> None:
        self.vault, self.path = vault, Path(vault.root) / "auth_registry.json"
        self.enrollment_marker = Path(vault.root) / ".auth_enrolled"
        self._lock, self._key = threading.RLock(), getattr(vault, "_encryption_key", None)
        self._dummy_password_hash = hash_password(secrets.token_urlsafe(24))
        if not self.path.exists():
            if self.enrollment_marker.exists():
                raise AuthenticationStateError("auth_registry_missing_after_enrollment")
            enrollment_password = bootstrap_password
            if enrollment_password is None and allow_development_bootstrap:
                enrollment_password = "123456"
            if not enrollment_password:
                raise AuthenticationStateError("auth_bootstrap_credential_required")
            self._write({"schema_version": "hc.auth.registry.v1", "accounts": {}, "sessions": {}, "audit": []})
            self.bootstrap_owner(enrollment_password)
            self._write_enrollment_marker()
        else:
            try:
                data = self._read()
                if not isinstance(data.get("accounts"), dict) or "00000" not in data["accounts"]:
                    raise AuthenticationStateError("auth_registry_owner_missing")
                self._write_enrollment_marker()
            except AuthenticationStateError:
                raise
            except Exception as exc:
                raise AuthenticationStateError("auth_registry_invalid") from exc

    def _write_enrollment_marker(self) -> None:
        if self.enrollment_marker.exists():
            return
        try:
            self.enrollment_marker.write_text("hc.auth.enrolled.v1\n", encoding="ascii")
        except OSError as exc:
            raise AuthenticationStateError("auth_enrollment_marker_failed") from exc

    def _read(self) -> dict[str, Any]:
        with self._lock:
            raw = self.path.read_bytes()
            if self._key is not None:
                raw = decrypt_bytes(raw, key=self._key, context=_AUTH_CONTEXT)
            return json.loads(raw.decode())

    def _write(self, data: dict[str, Any]) -> None:
        with self._lock:
            raw = json.dumps(data, indent=2, sort_keys=True).encode()
            if self._key is not None:
                raw = encrypt_bytes(raw, key=self._key, context=_AUTH_CONTEXT)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_bytes(raw)
            os.replace(tmp, self.path)

    @staticmethod
    def _audit(data: dict[str, Any], action: str, user_id: str | None, outcome: str = "success") -> None:
        data.setdefault("audit", []).append({"event_id": str(uuid4()), "at": utc_now(), "action": action,
                                              "user_id": user_id, "outcome": outcome})

    @_synchronized
    def bootstrap_owner(self, password: str) -> bool:
        data = self._read()
        if "00000" in data["accounts"]:
            return False
        account = UserAccount("00000", "Robert Asibor", "00000", hash_password(password), None, None,
                              True, "password_change_required", "owner")
        data["accounts"][account.user_id] = account.to_dict(include_secret=True)
        self._audit(data, "owner_bootstrapped", account.user_id)
        self._write(data)
        self.vault.ensure_user_profile(account.user_id)
        return True

    @_synchronized
    def create_user(self, *, user_id: str, name: str, email_identifier: str, password: str,
                    role: str = "user", must_change_password: bool = True) -> UserAccount:
        uid, data = str(user_id), self._read()
        if not uid or len(uid) > 128:
            raise ValueError("invalid_user_id")
        if uid in data["accounts"]:
            raise ValueError("account_exists")
        if must_change_password and is_consumer_bootstrap_password(password):
            pass
        elif not isinstance(password, str) or not password:
            raise ValueError("password_required")
        elif is_consumer_bootstrap_password(password):
            raise ValueError("password_policy_violation")
        changed = None if must_change_password else utc_now()
        expiry = None if must_change_password else _iso(_now() + timedelta(days=PASSWORD_DAYS))
        account = UserAccount(uid, name, email_identifier, hash_password(password), changed, expiry,
                              must_change_password, "password_change_required" if must_change_password else "active", role)
        data["accounts"][uid] = account.to_dict(include_secret=True)
        self._audit(data, "account_created", uid)
        self._write(data)
        self.vault.ensure_user_profile(uid)
        return account

    def get_account(self, user_id: str) -> UserAccount | None:
        row = self._read().get("accounts", {}).get(str(user_id))
        return UserAccount.from_dict(row) if row else None

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def _issue_session(
        self,
        data: dict[str, Any],
        account: UserAccount,
        scope: str,
        *,
        auth_method: str = "password",
        trusted_device_id: str | None = None,
    ) -> str:
        token = secrets.token_urlsafe(32)
        if scope == "password_change":
            lifetime = timedelta(minutes=CHANGE_SESSION_MINUTES)
        elif scope == "password_recovery":
            lifetime = timedelta(minutes=RECOVERY_MINUTES)
        else:
            lifetime = timedelta(hours=SESSION_HOURS)
        data["sessions"][self._token_hash(token)] = {
            "session_id": str(uuid4()), "user_id": account.user_id, "scope": scope,
            "password_version": int(data["accounts"][account.user_id].get("password_version", 1)),
            "issued_at": utc_now(), "expires_at": _iso(_now() + lifetime), "revoked_at": None,
            "auth_method": auth_method,
            "trusted_device_id": trusted_device_id,
        }
        return token

    def _issue_login_challenge(
        self, data: dict[str, Any], user_id: str, scope: str = "full"
    ) -> dict[str, Any]:
        challenge = secrets.token_urlsafe(32)
        data.setdefault("login_challenges", {})[self._token_hash(challenge)] = {
            "user_id": user_id,
            "scope": scope,
            "expires_at": _iso(_now() + timedelta(minutes=TOTP_LOGIN_MINUTES)),
            "failed_count": 0,
        }
        return {
            "requires_totp": True,
            "challenge_token": challenge,
            "user_id": user_id,
            "must_change_password": scope != "full",
            "scope": scope,
        }

    def _clear_lockout_state(self, row: dict[str, Any]) -> None:
        row["failed_login_count"] = 0
        row["last_failed_login_at"] = None
        row["locked_until"] = None
        if row.get("account_status") == "locked":
            row["account_status"] = "active"

    def _apply_expired_lockout_recovery(self, row: dict[str, Any]) -> bool:
        """Safe recovery path: expired lockouts auto-clear before credential check."""
        locked_until = _parse(row.get("locked_until"))
        if locked_until and locked_until <= _now():
            self._clear_lockout_state(row)
            return True
        return False

    @_synchronized
    def login(self, user_id: str, password: str) -> dict[str, Any]:
        uid, data = str(user_id), self._read()
        row = data.get("accounts", {}).get(uid)

        # HC-321-C1: account lockout + inter-attempt rate limit (generic error; no user enum).
        if row:
            locked_until = _parse(row.get("locked_until"))
            if locked_until and locked_until > _now():
                self._audit(data, "login_locked", uid, "denied")
                self._write(data)
                raise AuthenticationError("invalid_credentials")
            recovered = self._apply_expired_lockout_recovery(row)
            if recovered:
                self._audit(data, "login_lockout_auto_recovered", uid, "success")

        credential_hash = str(row.get("password_hash")) if row else self._dummy_password_hash
        valid_password = verify_password(password, credential_hash)
        if not row or not valid_password:
            if row:
                # Inter-attempt rate signal: rapid wrong passwords still advance lockout.
                last_failed = _parse(row.get("last_failed_login_at"))
                rapid = bool(
                    last_failed
                    and (_now() - last_failed).total_seconds() < min_seconds_between_attempts()
                )
                fails = int(row.get("failed_login_count", 0)) + 1
                row["failed_login_count"] = fails
                row["last_failed_login_at"] = utc_now()
                if fails >= max_failed_logins():
                    row["account_status"] = "locked"
                    row["locked_until"] = _iso(_now() + timedelta(minutes=lockout_minutes()))
                    self._audit(data, "login_lockout_engaged", uid, "denied")
                self._audit(
                    data,
                    "login_rate_limited" if rapid else "login_failed",
                    uid,
                    "denied",
                )
            else:
                self._audit(data, "login_failed", uid, "denied")
            self._write(data)
            raise AuthenticationError("invalid_credentials")
        account = UserAccount.from_dict(row)
        if account.account_status in {"disabled", "locked"}:
            raise AuthenticationError("invalid_credentials")
        expired = bool(_parse(account.password_expiry_date) and _parse(account.password_expiry_date) <= _now())
        restricted = account.must_change_password or expired
        scope = "password_change" if restricted else "full"
        if expired:
            row["account_status"] = "password_expired"
        self._clear_lockout_state(row)
        if bool(row.get("totp_enabled")):
            self._audit(data, "login_password_verified_totp_required", uid)
            challenge_result = self._issue_login_challenge(data, uid, scope)
            self._write(data)
            return challenge_result
        token = self._issue_session(data, account, scope)
        self._audit(data, "login_succeeded", uid)
        self._write(data)
        return {"token": token, "user_id": uid, "patient_id": uid, "name": account.name,
                "must_change_password": restricted, "password_expiry_date": account.password_expiry_date,
                "password_expires_at": account.password_expiry_date, "scope": scope,
                "recovery_enrolled": len(list(row.get("recovery_questions") or [])) >= 3}

    @staticmethod
    def _totp_attempt_locked(row: dict[str, Any]) -> bool:
        locked_until = _parse(row.get("totp_locked_until"))
        return bool(locked_until and locked_until > _now())

    def _record_totp_failure(self, data: dict[str, Any], row: dict[str, Any], user_id: str) -> None:
        failures = int(row.get("totp_failed_count") or 0) + 1
        row["totp_failed_count"] = failures
        row["totp_last_failed_at"] = utc_now()
        if failures >= max_failed_logins():
            row["totp_locked_until"] = _iso(_now() + timedelta(minutes=lockout_minutes()))
            self._audit(data, "totp_lockout_engaged", user_id, "denied")
        self._audit(data, "totp_verification_failed", user_id, "denied")

    @staticmethod
    def _consume_recovery_code(row: dict[str, Any], code: str) -> bool:
        if not isinstance(code, str) or not code:
            return False
        codes = row.get("totp_recovery_codes")
        if not isinstance(codes, list):
            return False
        candidate = code.strip().lower()
        for entry in codes:
            if not isinstance(entry, dict) or entry.get("used_at"):
                continue
            encoded = str(entry.get("code_hash") or "")
            if encoded and verify_password(candidate, encoded):
                entry["used_at"] = utc_now()
                return True
        return False

    def _verify_factor(
        self,
        data: dict[str, Any],
        row: dict[str, Any],
        user_id: str,
        *,
        code: str | None = None,
        recovery_code: str | None = None,
        consume_recovery: bool = True,
    ) -> tuple[bool, str | None]:
        if self._totp_attempt_locked(row):
            self._audit(data, "totp_verification_locked", user_id, "denied")
            return False, None
        if recovery_code:
            valid = self._consume_recovery_code(row, recovery_code) if consume_recovery else any(
                isinstance(item, dict)
                and not item.get("used_at")
                and verify_password(recovery_code.strip().lower(), str(item.get("code_hash") or ""))
                for item in row.get("totp_recovery_codes", [])
            )
            if valid:
                row["totp_failed_count"] = 0
                row["totp_locked_until"] = None
                self._audit(data, "totp_recovery_code_used", user_id)
                return True, "recovery_code"
        secret = str(row.get("totp_secret") or "")
        counter = matching_totp_counter(secret, code or "") if secret else None
        last_counter = int(row.get("totp_last_counter", -1))
        if counter is not None and counter > last_counter:
            row["totp_last_counter"] = counter
            row["totp_failed_count"] = 0
            row["totp_locked_until"] = None
            self._audit(data, "totp_verified", user_id)
            return True, "totp"
        self._record_totp_failure(data, row, user_id)
        return False, None

    @_synchronized
    def verify_login_totp(
        self, challenge_token: str, *, code: str | None = None, recovery_code: str | None = None
    ) -> dict[str, Any]:
        data = self._read()
        challenges = data.setdefault("login_challenges", {})
        challenge_key = self._token_hash(str(challenge_token or ""))
        challenge = challenges.get(challenge_key)
        if not challenge or (_parse(challenge.get("expires_at")) or _now()) <= _now():
            challenges.pop(challenge_key, None)
            self._write(data)
            raise AuthenticationError("invalid_credentials")
        user_id = str(challenge.get("user_id") or "")
        row = data.get("accounts", {}).get(user_id)
        if not row or not row.get("totp_enabled") or self._totp_attempt_locked(row):
            challenges.pop(challenge_key, None)
            self._write(data)
            raise AuthenticationError("invalid_credentials")
        valid, method = self._verify_factor(
            data, row, user_id, code=code, recovery_code=recovery_code
        )
        challenge["failed_count"] = int(challenge.get("failed_count") or 0) + (0 if valid else 1)
        if valid:
            challenges.pop(challenge_key, None)
            row["totp_failed_count"] = 0
            row["totp_locked_until"] = None
            account = UserAccount.from_dict(row)
            scope = str(challenge.get("scope") or "full")
            if scope not in {"full", "password_change"}:
                challenges.pop(challenge_key, None)
                self._write(data)
                raise AuthenticationError("invalid_credentials")
            token = self._issue_session(data, account, scope, auth_method=method or "totp")
            self._audit(data, "login_succeeded", user_id)
            self._write(data)
            return {
                "token": token, "user_id": user_id, "patient_id": user_id, "name": account.name,
                "must_change_password": scope != "full", "password_expiry_date": account.password_expiry_date,
                "password_expires_at": account.password_expiry_date, "scope": scope,
                "recovery_enrolled": len(list(row.get("recovery_questions") or [])) >= 3,
                "totp_enabled": True,
            }
        if int(challenge["failed_count"]) >= max_failed_logins():
            challenges.pop(challenge_key, None)
        self._write(data)
        raise AuthenticationError("invalid_credentials")

    @_synchronized
    def totp_setup_start(self, token: str, current_password: str) -> dict[str, Any]:
        account, _ = self.resolve(token, require_full=True)
        data = self._read()
        row = data["accounts"][account.user_id]
        if not verify_password(current_password, row["password_hash"]):
            self._record_totp_failure(data, row, account.user_id)
            self._write(data)
            raise AuthenticationError("invalid_credentials")
        if row.get("totp_enabled"):
            raise AuthenticationError("totp_already_enabled", 409)
        secret = new_totp_secret()
        row["totp_pending_secret"] = secret
        row["totp_pending_expires_at"] = _iso(_now() + timedelta(minutes=TOTP_SETUP_MINUTES))
        self._audit(data, "totp_setup_started", account.user_id)
        self._write(data)
        label = account.email_identifier or account.user_id
        issuer = "HealthChecker"
        import urllib.parse

        uri = (
            f"otpauth://totp/{urllib.parse.quote(issuer + ':' + label, safe='')}?"
            f"secret={secret}&issuer={urllib.parse.quote(issuer)}&algorithm=SHA1&digits=6&period=30"
        )
        return {"secret": secret, "provisioning_uri": uri, "expires_in_seconds": TOTP_SETUP_MINUTES * 60}

    @staticmethod
    def _revoke_sessions_for_user(data: dict[str, Any], user_id: str) -> None:
        for session in data.get("sessions", {}).values():
            if session.get("user_id") == user_id and not session.get("revoked_at"):
                session["revoked_at"] = utc_now()
        for device in data.get("trusted_devices", {}).values():
            if device.get("user_id") == user_id and not device.get("revoked_at"):
                device["revoked_at"] = utc_now()

    @staticmethod
    def _trusted_device_key(device_id: str) -> str:
        return hashlib.sha256(device_id.encode()).hexdigest()

    @_synchronized
    def issue_trusted_device(self, token: str, device_id: str, label: str = "Android device") -> dict[str, Any]:
        account, session = self.resolve(token, require_full=True)
        data = self._read()
        row = data["accounts"][account.user_id]
        if session.get("auth_method") == "trusted_device":
            raise AuthenticationError("recent_authentication_required", 403)
        if row.get("totp_enabled") and session.get("auth_method") != "totp":
            raise AuthenticationError("second_factor_required", 403)
        safe_id = str(device_id or "").strip()
        if not 16 <= len(safe_id) <= 128 or not safe_id.isascii():
            raise AuthenticationError("invalid_device_id", 400)
        safe_label = " ".join(str(label or "Android device").split())[:64] or "Android device"
        devices = data.setdefault("trusted_devices", {})
        key = self._trusted_device_key(safe_id)
        existing = devices.get(key)
        if existing and existing.get("user_id") != account.user_id:
            raise AuthenticationError("invalid_device_id", 400)
        secret = secrets.token_urlsafe(32)
        devices[key] = {
            "device_id": safe_id,
            "user_id": account.user_id,
            "token_hash": self._token_hash(secret),
            "label": safe_label,
            "created_at": utc_now(),
            "last_used_at": None,
            "expires_at": _iso(_now() + timedelta(days=TRUSTED_DEVICE_DAYS)),
            "password_version": int(row.get("password_version", 1)),
            "totp_factor_version": int(row.get("totp_factor_version", 0)),
            "revoked_at": None,
        }
        data["sessions"][self._token_hash(token)]["trusted_device_id"] = safe_id
        self._audit(data, "trusted_device_issued", account.user_id)
        self._write(data)
        return {
            "device_id": safe_id,
            "trusted_device_token": secret,
            "expires_at": devices[key]["expires_at"],
        }

    @_synchronized
    def restore_trusted_device(self, device_id: str, device_token: str) -> dict[str, Any]:
        data = self._read()
        safe_id = str(device_id or "").strip()
        key = self._trusted_device_key(safe_id) if safe_id else ""
        device = data.get("trusted_devices", {}).get(key)
        generic = AuthenticationError("invalid_credentials")
        if not isinstance(device, dict):
            raise generic
        user_id = str(device.get("user_id") or "")
        row = data.get("accounts", {}).get(user_id)
        expires = _parse(device.get("expires_at"))
        valid = bool(
            row
            and not device.get("revoked_at")
            and expires
            and expires > _now()
            and int(device.get("password_version", 0)) == int(row.get("password_version", 1))
            and int(device.get("totp_factor_version", 0)) == int(row.get("totp_factor_version", 0))
            and row.get("account_status") not in {"disabled", "locked", "password_expired"}
            and not row.get("must_change_password")
            and hmac.compare_digest(
                str(device.get("token_hash") or ""), self._token_hash(str(device_token or ""))
            )
        )
        if not valid:
            raise generic
        account = UserAccount.from_dict(row)
        if _parse(account.password_expiry_date) and _parse(account.password_expiry_date) <= _now():
            device["revoked_at"] = utc_now()
            self._write(data)
            raise generic
        new_secret = secrets.token_urlsafe(32)
        device["token_hash"] = self._token_hash(new_secret)
        device["last_used_at"] = utc_now()
        session_token = self._issue_session(
            data,
            account,
            "full",
            auth_method="trusted_device",
            trusted_device_id=safe_id,
        )
        self._audit(data, "trusted_device_restored", user_id)
        self._write(data)
        return {
            "token": session_token,
            "trusted_device_token": new_secret,
            "device_id": safe_id,
            "user_id": user_id,
            "patient_id": user_id,
            "name": account.name,
            "must_change_password": False,
            "password_expiry_date": account.password_expiry_date,
            "password_expires_at": account.password_expiry_date,
            "scope": "full",
            "recovery_enrolled": len(list(row.get("recovery_questions") or [])) >= 3,
            "totp_enabled": bool(row.get("totp_enabled")),
        }

    @_synchronized
    def list_trusted_devices(self, token: str) -> list[dict[str, Any]]:
        account, _ = self.resolve(token, require_full=True)
        data = self._read()
        return [
            {
                "device_id": device.get("device_id"),
                "label": device.get("label"),
                "created_at": device.get("created_at"),
                "last_used_at": device.get("last_used_at"),
                "expires_at": device.get("expires_at"),
                "revoked": bool(device.get("revoked_at")),
            }
            for device in data.get("trusted_devices", {}).values()
            if device.get("user_id") == account.user_id
        ]

    @_synchronized
    def revoke_trusted_device(self, token: str, device_id: str) -> dict[str, Any]:
        account, _ = self.resolve(token, require_full=True)
        data = self._read()
        key = self._trusted_device_key(str(device_id or "").strip())
        device = data.get("trusted_devices", {}).get(key)
        if not device or device.get("user_id") != account.user_id:
            raise AuthenticationError("trusted_device_not_found", 404)
        if not device.get("revoked_at"):
            device["revoked_at"] = utc_now()
            self._audit(data, "trusted_device_revoked", account.user_id)
            self._write(data)
        return {"ok": True, "device_id": device.get("device_id")}


    @_synchronized
    def totp_setup_confirm(self, token: str, code: str) -> dict[str, Any]:
        account, _ = self.resolve(token, require_full=True)
        data = self._read()
        row = data["accounts"][account.user_id]
        secret = str(row.get("totp_pending_secret") or "")
        expires = _parse(row.get("totp_pending_expires_at"))
        counter = matching_totp_counter(secret, code) if secret and expires and expires > _now() else None
        if counter is None:
            self._record_totp_failure(data, row, account.user_id)
            self._write(data)
            raise AuthenticationError("invalid_totp", 401)
        codes = new_recovery_codes()
        row.update({
            "totp_enabled": True,
            "totp_secret": secret,
            "totp_last_counter": counter,
            "totp_recovery_codes": [{"code_hash": hash_password(value), "used_at": None} for value in codes],
            "totp_failed_count": 0,
            "totp_locked_until": None,
            "totp_factor_version": int(row.get("totp_factor_version", 0)) + 1,
        })
        row.pop("totp_pending_secret", None)
        row.pop("totp_pending_expires_at", None)
        self._revoke_sessions_for_user(data, account.user_id)
        self._audit(data, "totp_enabled", account.user_id)
        self._write(data)
        return {"ok": True, "recovery_codes": codes}

    @_synchronized
    def totp_disable(
        self, token: str, current_password: str, *, code: str | None = None, recovery_code: str | None = None
    ) -> dict[str, Any]:
        account, _ = self.resolve(token, require_full=True)
        data = self._read()
        row = data["accounts"][account.user_id]
        if not row.get("totp_enabled"):
            raise AuthenticationError("invalid_credentials")
        if not verify_password(current_password, row["password_hash"]):
            self._record_totp_failure(data, row, account.user_id)
            self._write(data)
            raise AuthenticationError("invalid_credentials")
        valid, _ = self._verify_factor(data, row, account.user_id, code=code, recovery_code=recovery_code)
        if not valid:
            self._write(data)
            raise AuthenticationError("invalid_credentials")
        row["totp_enabled"] = False
        row["totp_factor_version"] = int(row.get("totp_factor_version", 0)) + 1
        row.pop("totp_secret", None)
        row.pop("totp_last_counter", None)
        row.pop("totp_recovery_codes", None)
        row.pop("totp_pending_secret", None)
        row.pop("totp_pending_expires_at", None)
        self._revoke_sessions_for_user(data, account.user_id)
        self._audit(data, "totp_disabled", account.user_id)
        self._write(data)
        return {"ok": True}

    @_synchronized
    def totp_regenerate_recovery_codes(
        self, token: str, current_password: str, *, code: str | None = None, recovery_code: str | None = None
    ) -> dict[str, Any]:
        account, _ = self.resolve(token, require_full=True)
        data = self._read()
        row = data["accounts"][account.user_id]
        if not row.get("totp_enabled"):
            raise AuthenticationError("invalid_credentials")
        if not verify_password(current_password, row["password_hash"]):
            self._record_totp_failure(data, row, account.user_id)
            self._write(data)
            raise AuthenticationError("invalid_credentials")
        valid, _ = self._verify_factor(data, row, account.user_id, code=code, recovery_code=recovery_code)
        if not valid:
            self._write(data)
            raise AuthenticationError("invalid_credentials")
        codes = new_recovery_codes()
        row["totp_recovery_codes"] = [
            {"code_hash": hash_password(value), "used_at": None} for value in codes
        ]
        row["totp_factor_version"] = int(row.get("totp_factor_version", 0)) + 1
        self._revoke_sessions_for_user(data, account.user_id)
        self._audit(data, "totp_recovery_codes_regenerated", account.user_id)
        self._write(data)
        return {"ok": True, "recovery_codes": codes}

    @_synchronized
    def unlock_after_cooldown(self, user_id: str, password: str) -> dict[str, Any]:
        """Explicit recovery entrypoint: same fail-closed rules as login after lockout expiry.

        While locked_until is in the future this fails closed with invalid_credentials.
        After cooldown, a correct password restores access (via login).
        """
        return self.login(user_id, password)

    def resolve(self, token: str, *, require_full: bool = True) -> tuple[UserAccount, dict[str, Any]]:
        data = self._read()
        session = data.get("sessions", {}).get(self._token_hash(token))
        if not session or session.get("revoked_at") or (_parse(session.get("expires_at")) or _now()) <= _now():
            raise AuthenticationError()
        row = data.get("accounts", {}).get(str(session.get("user_id")))
        if not row or row.get("account_status") in {"disabled", "locked"}:
            raise AuthenticationError()
        if int(session.get("password_version", 0)) != int(row.get("password_version", 1)):
            raise AuthenticationError()
        account = UserAccount.from_dict(row)
        expiry = _parse(account.password_expiry_date)
        restricted = account.must_change_password or bool(expiry and expiry <= _now()) or session.get("scope") != "full"
        if require_full and restricted:
            raise AuthenticationError("password_change_required", 403)
        return account, session

    @_synchronized
    def change_password(
        self,
        token: str,
        current_password: str,
        new_password: str,
        confirmation: str | None = None,
        recovery_answers: Any = None,
    ) -> dict[str, Any]:
        from backend.health_vault.consumer_recovery import parse_enrollment_pairs

        account, session = self.resolve(token, require_full=False)
        if session.get("scope") == "password_recovery":
            raise AuthenticationError("unauthorized", 401)
        validate_permanent_password(
            new_password, confirmation=confirmation, current_password=current_password
        )
        data = self._read()
        row = data["accounts"][account.user_id]
        if not verify_password(current_password, row["password_hash"]):
            raise AuthenticationError("invalid_credentials")
        enrolled = list(row.get("recovery_questions") or [])
        restricted = account.must_change_password or session.get("scope") != "full"
        if not enrolled and restricted:
            try:
                pairs = parse_enrollment_pairs(recovery_answers)
            except ValueError as exc:
                raise AuthenticationError(str(exc), 400) from exc
            row["recovery_questions"] = [
                {"question_id": qid, "answer_hash": hash_password(answer)} for qid, answer in pairs
            ]
        changed = _now()
        row.update({"password_hash": hash_password(new_password), "password_changed_at": _iso(changed),
                    "password_expiry_date": _iso(changed + timedelta(days=PASSWORD_DAYS)),
                    "must_change_password": False, "account_status": "active",
                    "password_version": int(row.get("password_version", 1)) + 1})
        self._clear_lockout_state(row)
        data["accounts"][account.user_id] = row
        self._revoke_sessions_for_user(data, account.user_id)
        updated = UserAccount.from_dict(row)
        if row.get("totp_enabled"):
            challenge_result = self._issue_login_challenge(data, account.user_id)
            self._audit(data, "password_changed_totp_required", account.user_id)
            self._write(data)
            return challenge_result
        new_token = self._issue_session(data, updated, "full")
        self._audit(data, "password_changed", account.user_id)
        self._write(data)
        return {"token": new_token, "user_id": account.user_id, "patient_id": account.user_id,
                "name": account.name, "must_change_password": False, "password_changed_at": row["password_changed_at"],
                "password_expiry_date": row["password_expiry_date"],
                "password_expires_at": row["password_expiry_date"], "scope": "full",
                "recovery_enrolled": len(list(row.get("recovery_questions") or [])) >= 3}

    def _verify_recovery_answers(self, enrolled: list[dict[str, Any]], submitted: Any) -> bool:
        from backend.health_vault.consumer_recovery import REQUIRED_ENROLLMENT_COUNT, normalize_answer

        submitted_map: dict[str, str] = {}
        if isinstance(submitted, list):
            for item in submitted:
                if isinstance(item, dict) and item.get("question_id"):
                    submitted_map[str(item.get("question_id"))] = str(item.get("answer") or "")
        ok = len(enrolled) == REQUIRED_ENROLLMENT_COUNT
        for row in enrolled:
            qid = str(row.get("question_id") or "")
            encoded = str(row.get("answer_hash") or "")
            candidate = normalize_answer(submitted_map.get(qid, ""))
            if not encoded or not verify_password(candidate, encoded):
                ok = False
        if set(submitted_map) - {str(row.get("question_id") or "") for row in enrolled}:
            ok = False
        return ok

    @_synchronized
    def recovery_start(self, user_id: str) -> dict[str, Any]:
        from backend.health_vault.consumer_recovery import dummy_question_ids, public_questions

        uid = str(user_id or "").strip()
        data = self._read()
        data.setdefault("recovery_challenges", {})
        row = data.get("accounts", {}).get(uid)
        enrolled = list((row or {}).get("recovery_questions") or [])
        locked = False
        if row:
            locked_until = _parse(row.get("recovery_locked_until"))
            locked = bool(locked_until and locked_until > _now())
        real = bool(row and enrolled and not locked)
        qids = [str(item.get("question_id")) for item in enrolled] if real else dummy_question_ids(uid or "unknown")
        if not real:
            verify_password("x", self._dummy_password_hash)
        challenge_id = secrets.token_urlsafe(24)
        data["recovery_challenges"][challenge_id] = {
            "user_id": uid if real else "",
            "question_ids": qids,
            "expires_at": _iso(_now() + timedelta(minutes=RECOVERY_MINUTES)),
            "failed_count": 0,
            "verified": False,
        }
        self._audit(data, "recovery_started", uid if row else None, "success")
        self._write(data)
        return {"recovery_id": challenge_id, "questions": public_questions(qids)}

    @_synchronized
    def recovery_verify(self, recovery_id: str, answers: Any) -> dict[str, Any]:
        data = self._read()
        data.setdefault("recovery_challenges", {})
        challenge = data["recovery_challenges"].get(str(recovery_id or ""))
        generic = AuthenticationError("invalid_recovery", 401)
        if not challenge or (_parse(challenge.get("expires_at")) or _now()) <= _now():
            verify_password("x", self._dummy_password_hash)
            raise generic
        uid = str(challenge.get("user_id") or "")
        row = data.get("accounts", {}).get(uid) if uid else None
        if row:
            locked_until = _parse(row.get("recovery_locked_until"))
            if locked_until and locked_until > _now():
                verify_password("x", self._dummy_password_hash)
                raise generic
        enrolled = list((row or {}).get("recovery_questions") or [])
        ok = bool(row and enrolled and self._verify_recovery_answers(enrolled, answers))
        if not ok:
            verify_password("x", self._dummy_password_hash)
            challenge["failed_count"] = int(challenge.get("failed_count") or 0) + 1
            if row:
                fails = int(row.get("recovery_failed_count") or 0) + 1
                row["recovery_failed_count"] = fails
                row["last_recovery_failed_at"] = utc_now()
                if fails >= max_failed_logins():
                    row["recovery_locked_until"] = _iso(_now() + timedelta(minutes=lockout_minutes()))
                    self._audit(data, "recovery_lockout_engaged", uid, "denied")
            self._audit(data, "recovery_failed", uid or None, "denied")
            self._write(data)
            raise generic
        challenge["verified"] = True
        token = self._issue_session(data, UserAccount.from_dict(row), "password_recovery")
        self._audit(data, "recovery_verified", uid)
        self._write(data)
        return {"token": token, "user_id": uid, "scope": "password_recovery"}

    @_synchronized
    def recovery_complete(self, token: str, new_password: str, confirmation: str | None = None) -> dict[str, Any]:
        account, session = self.resolve(token, require_full=False)
        if session.get("scope") != "password_recovery":
            raise AuthenticationError("unauthorized", 401)
        validate_permanent_password(new_password, confirmation=confirmation)
        data = self._read()
        row = data["accounts"][account.user_id]
        if verify_password(new_password, row["password_hash"]):
            raise AuthenticationError("password_policy_violation", 400)
        changed = _now()
        row.update({
            "password_hash": hash_password(new_password),
            "password_changed_at": _iso(changed),
            "password_expiry_date": _iso(changed + timedelta(days=PASSWORD_DAYS)),
            "must_change_password": False,
            "account_status": "active",
            "password_version": int(row.get("password_version", 1)) + 1,
            "recovery_failed_count": 0,
            "recovery_locked_until": None,
        })
        self._revoke_sessions_for_user(data, account.user_id)
        self._audit(data, "password_recovered", account.user_id)
        self._write(data)
        return {
            "ok": True,
            "user_id": account.user_id,
            "must_change_password": False,
            "password_changed_at": row["password_changed_at"],
            "password_expiry_date": row["password_expiry_date"],
            "password_expires_at": row["password_expiry_date"],
        }

    @_synchronized
    def replace_recovery_questions(self, token: str, current_password: str, recovery_answers: Any) -> dict[str, Any]:
        from backend.health_vault.consumer_recovery import parse_enrollment_pairs

        account, _ = self.resolve(token, require_full=True)
        data = self._read()
        row = data["accounts"][account.user_id]
        if not verify_password(current_password, row["password_hash"]):
            raise AuthenticationError("invalid_credentials")
        try:
            pairs = parse_enrollment_pairs(recovery_answers)
        except ValueError as exc:
            raise AuthenticationError(str(exc), 400) from exc
        row["recovery_questions"] = [
            {"question_id": qid, "answer_hash": hash_password(answer)} for qid, answer in pairs
        ]
        self._audit(data, "recovery_questions_replaced", account.user_id)
        self._write(data)
        return {"ok": True, "recovery_enrolled": True, "user_id": account.user_id}

    @_synchronized
    def logout(self, token: str, trusted_device_id: str | None = None) -> None:
        data, key = self._read(), self._token_hash(token)
        session = data.get("sessions", {}).get(key)
        if session and not session.get("revoked_at"):
            session["revoked_at"] = utc_now()
            user_id = str(session.get("user_id"))
            device_id = str(trusted_device_id or session.get("trusted_device_id") or "").strip()
            if device_id:
                device_key = self._trusted_device_key(device_id)
                device = data.get("trusted_devices", {}).get(device_key)
                if device and device.get("user_id") == user_id and not device.get("revoked_at"):
                    device["revoked_at"] = utc_now()
                    self._audit(data, "trusted_device_revoked", user_id)
            self._audit(data, "logout", user_id)
            self._write(data)

    def safe_session(self, token: str) -> dict[str, Any]:
        account, session = self.resolve(token, require_full=False)
        row = self._read().get("accounts", {}).get(account.user_id) or {}
        enrolled = list(row.get("recovery_questions") or [])
        return {"authenticated": True, "user_id": account.user_id, "patient_id": account.user_id,
                "name": account.name, "role": account.role, "scope": session["scope"],
                "must_change_password": account.must_change_password or session["scope"] != "full",
                "password_expiry_date": account.password_expiry_date,
                "password_expires_at": account.password_expiry_date,
                "recovery_enrolled": len(enrolled) >= 3,
                "totp_enabled": bool(row.get("totp_enabled"))}

    # --- HC321-C-C admin lifecycle (least privilege; auditable; no silent escalation) ---

    ALLOWED_ROLES = frozenset({"owner", "admin", "user"})
    PRIVILEGED_ROLES = frozenset({"owner", "admin"})

    def require_roles(self, token: str, allowed: set[str] | frozenset[str]) -> UserAccount:
        account, _ = self.resolve(token, require_full=True)
        if account.role not in allowed:
            raise AuthenticationError("forbidden", 403)
        return account

    def _safe_account_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "user_id": row.get("user_id"),
            "name": row.get("name"),
            "email_identifier": row.get("email_identifier"),
            "role": row.get("role") or "user",
            "account_status": row.get("account_status"),
            "must_change_password": bool(row.get("must_change_password")),
            "password_expiry_date": row.get("password_expiry_date"),
        }

    @_synchronized
    def list_accounts(self, actor_token: str) -> list[dict[str, Any]]:
        self.require_roles(actor_token, self.PRIVILEGED_ROLES)
        data = self._read()
        return [self._safe_account_row(row) for row in data.get("accounts", {}).values()]

    @_synchronized
    def admin_create_user(
        self,
        actor_token: str,
        *,
        user_id: str,
        name: str,
        email_identifier: str,
        password: str,
        role: str = "user",
    ) -> dict[str, Any]:
        actor = self.require_roles(actor_token, self.PRIVILEGED_ROLES)
        role_norm = str(role or "user").strip().lower()
        if role_norm not in self.ALLOWED_ROLES:
            raise AuthenticationError("invalid_role", 400)
        if role_norm == "owner":
            raise AuthenticationError("owner_role_not_assignable", 403)
        if role_norm == "admin" and actor.role != "owner":
            raise AuthenticationError("forbidden", 403)
        if not is_consumer_bootstrap_password(password) and len(password) < 8:
            raise AuthenticationError("password_policy_violation", 400)
        account = self.create_user(
            user_id=user_id,
            name=name,
            email_identifier=email_identifier,
            password=password,
            role=role_norm,
            must_change_password=True,
        )
        data = self._read()
        self._audit(data, "admin_account_created", actor.user_id)
        data.setdefault("audit", []).append(
            {
                "event_id": str(uuid4()),
                "at": utc_now(),
                "action": "privilege_change",
                "user_id": actor.user_id,
                "outcome": "success",
                "target_user_id": account.user_id,
                "detail": {"role": role_norm},
            }
        )
        self._write(data)
        return self._safe_account_row(account.to_dict(include_secret=True))

    @_synchronized
    def set_account_status(self, actor_token: str, user_id: str, status: str) -> dict[str, Any]:
        actor = self.require_roles(actor_token, self.PRIVILEGED_ROLES)
        status_norm = str(status or "").strip().lower()
        if status_norm not in {"active", "disabled"}:
            raise AuthenticationError("invalid_account_status", 400)
        uid = str(user_id)
        if uid == actor.user_id and status_norm == "disabled":
            raise AuthenticationError("cannot_disable_self", 400)
        data = self._read()
        row = data.get("accounts", {}).get(uid)
        if not row:
            raise AuthenticationError("account_not_found", 404)
        target_role = str(row.get("role") or "user")
        if target_role == "owner" and actor.role != "owner":
            raise AuthenticationError("forbidden", 403)
        if target_role == "admin" and actor.role != "owner" and status_norm == "disabled":
            raise AuthenticationError("forbidden", 403)
        row["account_status"] = status_norm
        if status_norm == "disabled":
            self._revoke_sessions_for_user(data, uid)
        self._audit(data, f"account_{status_norm}", actor.user_id)
        data.setdefault("audit", []).append(
            {
                "event_id": str(uuid4()),
                "at": utc_now(),
                "action": "privilege_change",
                "user_id": actor.user_id,
                "outcome": "success",
                "target_user_id": uid,
                "detail": {"account_status": status_norm},
            }
        )
        self._write(data)
        return self._safe_account_row(row)

    @_synchronized
    def set_role(self, actor_token: str, user_id: str, role: str) -> dict[str, Any]:
        actor = self.require_roles(actor_token, {"owner"})
        role_norm = str(role or "").strip().lower()
        if role_norm not in self.ALLOWED_ROLES or role_norm == "owner":
            raise AuthenticationError("invalid_role", 400)
        uid = str(user_id)
        if uid == actor.user_id:
            raise AuthenticationError("cannot_change_own_role", 400)
        data = self._read()
        row = data.get("accounts", {}).get(uid)
        if not row:
            raise AuthenticationError("account_not_found", 404)
        if str(row.get("role") or "") == "owner":
            raise AuthenticationError("owner_role_immutable", 403)
        previous = row.get("role")
        row["role"] = role_norm
        data.setdefault("audit", []).append(
            {
                "event_id": str(uuid4()),
                "at": utc_now(),
                "action": "privilege_change",
                "user_id": actor.user_id,
                "outcome": "success",
                "target_user_id": uid,
                "detail": {"from_role": previous, "to_role": role_norm},
            }
        )
        self._write(data)
        return self._safe_account_row(row)

    @_synchronized
    def revoke_all_sessions(self, actor_token: str, user_id: str | None = None) -> dict[str, Any]:
        account, _ = self.resolve(actor_token, require_full=True)
        target = str(user_id or account.user_id)
        if target != account.user_id and account.role not in self.PRIVILEGED_ROLES:
            raise AuthenticationError("forbidden", 403)
        data = self._read()
        if target != account.user_id:
            row = data.get("accounts", {}).get(target)
            if not row:
                raise AuthenticationError("account_not_found", 404)
            if str(row.get("role") or "") == "owner" and account.role != "owner":
                raise AuthenticationError("forbidden", 403)
        revoked = 0
        for session in data.get("sessions", {}).values():
            if session.get("user_id") == target and not session.get("revoked_at"):
                session["revoked_at"] = utc_now()
                revoked += 1
        for device in data.get("trusted_devices", {}).values():
            if device.get("user_id") == target and not device.get("revoked_at"):
                device["revoked_at"] = utc_now()
        self._audit(data, "sessions_revoked", account.user_id)
        self._write(data)
        return {"ok": True, "user_id": target, "sessions_revoked": revoked}
