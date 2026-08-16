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

PASSWORD_DAYS = 30
SESSION_HOURS = 12
CHANGE_SESSION_MINUTES = 10
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
_AUTH_CONTEXT = b"auth/registry.v1"


class AuthenticationError(ValueError):
    def __init__(self, code: str = "unauthorized", status_code: int = 401) -> None:
        super().__init__(code)
        self.code, self.status_code = code, status_code


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

    def __init__(self, vault, *, bootstrap_password: str | None = None) -> None:
        self.vault, self.path = vault, Path(vault.root) / "auth_registry.json"
        self._lock, self._key = threading.RLock(), getattr(vault, "_encryption_key", None)
        self._dummy_password_hash = hash_password(secrets.token_urlsafe(24))
        if not self.path.exists():
            self._write({"schema_version": "hc.auth.registry.v1", "accounts": {}, "sessions": {}, "audit": []})
        self.bootstrap_owner(bootstrap_password or os.environ.get("HC_BOOTSTRAP_PASSWORD") or "123456")

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

    def _issue_session(self, data: dict[str, Any], account: UserAccount, scope: str) -> str:
        token = secrets.token_urlsafe(32)
        lifetime = timedelta(minutes=CHANGE_SESSION_MINUTES) if scope == "password_change" else timedelta(hours=SESSION_HOURS)
        data["sessions"][self._token_hash(token)] = {
            "session_id": str(uuid4()), "user_id": account.user_id, "scope": scope,
            "password_version": int(data["accounts"][account.user_id].get("password_version", 1)),
            "issued_at": utc_now(), "expires_at": _iso(_now() + lifetime), "revoked_at": None,
        }
        return token

    @_synchronized
    def login(self, user_id: str, password: str) -> dict[str, Any]:
        uid, data = str(user_id), self._read()
        row = data.get("accounts", {}).get(uid)
        credential_hash = str(row.get("password_hash")) if row else self._dummy_password_hash
        valid_password = verify_password(password, credential_hash)
        if not row or not valid_password:
            self._audit(data, "login_failed", uid, "denied")
            self._write(data)
            raise AuthenticationError("invalid_credentials")
        account = UserAccount.from_dict(row)
        if account.account_status in {"disabled", "locked"}:
            raise AuthenticationError("invalid_credentials")
        expired = bool(_parse(account.password_expiry_date) and _parse(account.password_expiry_date) <= _now())
        restricted = account.must_change_password or expired
        if expired:
            row["account_status"] = "password_expired"
        scope = "password_change" if restricted else "full"
        token = self._issue_session(data, account, scope)
        self._audit(data, "login_succeeded", uid)
        self._write(data)
        return {"token": token, "user_id": uid, "patient_id": uid, "name": account.name,
                "must_change_password": restricted, "password_expiry_date": account.password_expiry_date, "scope": scope}

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
    def change_password(self, token: str, current_password: str, new_password: str) -> dict[str, Any]:
        account, _ = self.resolve(token, require_full=False)
        if len(new_password) < 8 or new_password == current_password:
            raise AuthenticationError("password_policy_violation", 400)
        data = self._read()
        row = data["accounts"][account.user_id]
        if not verify_password(current_password, row["password_hash"]):
            raise AuthenticationError("invalid_credentials")
        changed = _now()
        row.update({"password_hash": hash_password(new_password), "password_changed_at": _iso(changed),
                    "password_expiry_date": _iso(changed + timedelta(days=PASSWORD_DAYS)),
                    "must_change_password": False, "account_status": "active",
                    "password_version": int(row.get("password_version", 1)) + 1})
        data["accounts"][account.user_id] = row
        for session in data["sessions"].values():
            if session.get("user_id") == account.user_id and not session.get("revoked_at"):
                session["revoked_at"] = utc_now()
        updated, new_token = UserAccount.from_dict(row), None
        new_token = self._issue_session(data, updated, "full")
        self._audit(data, "password_changed", account.user_id)
        self._write(data)
        return {"token": new_token, "user_id": account.user_id, "patient_id": account.user_id,
                "name": account.name, "must_change_password": False, "password_changed_at": row["password_changed_at"],
                "password_expiry_date": row["password_expiry_date"], "scope": "full"}

    @_synchronized
    def logout(self, token: str) -> None:
        data, key = self._read(), self._token_hash(token)
        session = data.get("sessions", {}).get(key)
        if session and not session.get("revoked_at"):
            session["revoked_at"] = utc_now()
            self._audit(data, "logout", str(session.get("user_id")))
            self._write(data)

    def safe_session(self, token: str) -> dict[str, Any]:
        account, session = self.resolve(token, require_full=False)
        return {"authenticated": True, "user_id": account.user_id, "patient_id": account.user_id,
                "name": account.name, "role": account.role, "scope": session["scope"],
                "must_change_password": account.must_change_password or session["scope"] != "full",
                "password_expiry_date": account.password_expiry_date}
