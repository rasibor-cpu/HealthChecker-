"""Cryptographic helpers for optional authenticator-app sign-in factors."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def totp_code(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{number % 1_000_000:06d}"


def matching_totp_counter(secret: str, code: str, *, now: float | None = None) -> int | None:
    if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit():
        return None
    counter = int((time.time() if now is None else now) // 30)
    for candidate in (counter - 1, counter, counter + 1):
        if hmac.compare_digest(totp_code(secret, candidate), code):
            return candidate
    return None


def new_recovery_codes(count: int = 10) -> list[str]:
    return [secrets.token_hex(20) for _ in range(count)]
