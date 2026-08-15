"""HC311 recovery package support.

The recovery package protects a 32-byte AES data key independently from
Windows DPAPI. It is intended for controlled off-machine recovery.

No production key creation is performed by this module.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
from datetime import datetime, timezone
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


MAGIC: Final[bytes] = b"HCRP"
VERSION: Final[int] = 1
ALGORITHM_ID: Final[int] = 1       # AES-256-GCM
KDF_ID: Final[int] = 1             # scrypt

SALT_BYTES: Final[int] = 16
NONCE_BYTES: Final[int] = 12
DATA_KEY_BYTES: Final[int] = 32

SCRYPT_N: Final[int] = 32768
SCRYPT_R: Final[int] = 8
SCRYPT_P: Final[int] = 1

_HEADER = struct.Struct(">4sBBBIII16s12sI")


class RecoveryPackageError(RuntimeError):
    """Fail-closed recovery package error."""


def _require_data_key(data_key: bytes) -> None:
    if not isinstance(data_key, bytes):
        raise RecoveryPackageError("data key must be bytes")
    if len(data_key) != DATA_KEY_BYTES:
        raise RecoveryPackageError("data key must be exactly 32 bytes")


def _require_passphrase(passphrase: str) -> bytes:
    if not isinstance(passphrase, str):
        raise RecoveryPackageError("passphrase must be text")
    if len(passphrase) < 16:
        raise RecoveryPackageError(
            "recovery passphrase must contain at least 16 characters"
        )
    return passphrase.encode("utf-8")


def key_check_value(data_key: bytes) -> str:
    _require_data_key(data_key)
    return hashlib.sha256(
        b"HealthChecker-HC311-KeyCheck-v1\x00" + data_key
    ).hexdigest()


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    material = _require_passphrase(passphrase)

    try:
        return Scrypt(
            salt=salt,
            length=32,
            n=SCRYPT_N,
            r=SCRYPT_R,
            p=SCRYPT_P,
        ).derive(material)
    except Exception as exc:
        raise RecoveryPackageError("recovery KDF failure") from exc


def create_recovery_package(
    data_key: bytes,
    passphrase: str,
    *,
    key_id: str,
    machine_identity: str,
    runtime_sid: str,
) -> bytes:
    """Create an authenticated encrypted recovery package."""

    _require_data_key(data_key)

    if not key_id:
        raise RecoveryPackageError("key_id is required")
    if not machine_identity:
        raise RecoveryPackageError("machine_identity is required")
    if not runtime_sid:
        raise RecoveryPackageError("runtime_sid is required")

    metadata = {
        "format": "HC311_RECOVERY_PACKAGE",
        "version": VERSION,
        "key_id": key_id,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "data_algorithm": "AES-256-GCM",
        "recovery_algorithm": "AES-256-GCM",
        "kdf": "scrypt",
        "scrypt_n": SCRYPT_N,
        "scrypt_r": SCRYPT_R,
        "scrypt_p": SCRYPT_P,
        "machine_identity": machine_identity,
        "runtime_sid": runtime_sid,
        "key_check_value": key_check_value(data_key),
    }

    metadata_bytes = json.dumps(
        metadata,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    salt = os.urandom(SALT_BYTES)
    nonce = os.urandom(NONCE_BYTES)

    recovery_key = _derive_key(passphrase, salt)

    header = _HEADER.pack(
        MAGIC,
        VERSION,
        ALGORITHM_ID,
        KDF_ID,
        SCRYPT_N,
        SCRYPT_R,
        SCRYPT_P,
        salt,
        nonce,
        len(metadata_bytes),
    )

    aad = header + metadata_bytes

    try:
        ciphertext = AESGCM(recovery_key).encrypt(
            nonce,
            data_key,
            aad,
        )
    except Exception as exc:
        raise RecoveryPackageError("recovery encryption failure") from exc

    return header + metadata_bytes + ciphertext


def open_recovery_package(
    package: bytes,
    passphrase: str,
) -> tuple[bytes, dict]:
    """Decrypt and authenticate a recovery package."""

    if not isinstance(package, bytes):
        raise RecoveryPackageError("package must be bytes")

    if len(package) < _HEADER.size + 16:
        raise RecoveryPackageError("recovery package is truncated")

    try:
        (
            magic,
            version,
            algorithm_id,
            kdf_id,
            n,
            r,
            p,
            salt,
            nonce,
            metadata_len,
        ) = _HEADER.unpack(package[: _HEADER.size])
    except struct.error as exc:
        raise RecoveryPackageError("invalid recovery header") from exc

    if magic != MAGIC:
        raise RecoveryPackageError("invalid recovery package magic")
    if version != VERSION:
        raise RecoveryPackageError("unsupported recovery package version")
    if algorithm_id != ALGORITHM_ID:
        raise RecoveryPackageError("unsupported recovery algorithm")
    if kdf_id != KDF_ID:
        raise RecoveryPackageError("unsupported recovery KDF")

    if (n, r, p) != (SCRYPT_N, SCRYPT_R, SCRYPT_P):
        raise RecoveryPackageError("unexpected recovery KDF parameters")

    metadata_start = _HEADER.size
    metadata_end = metadata_start + metadata_len

    if metadata_end > len(package) - 16:
        raise RecoveryPackageError("invalid recovery metadata length")

    metadata_bytes = package[metadata_start:metadata_end]
    ciphertext = package[metadata_end:]

    try:
        metadata = json.loads(metadata_bytes.decode("utf-8"))
    except Exception as exc:
        raise RecoveryPackageError("invalid recovery metadata") from exc

    recovery_key = _derive_key(passphrase, salt)
    aad = package[:_HEADER.size] + metadata_bytes

    try:
        data_key = AESGCM(recovery_key).decrypt(
            nonce,
            ciphertext,
            aad,
        )
    except InvalidTag as exc:
        raise RecoveryPackageError(
            "recovery authentication failed"
        ) from exc
    except Exception as exc:
        raise RecoveryPackageError("recovery decryption failed") from exc

    _require_data_key(data_key)

    expected = metadata.get("key_check_value")
    actual = key_check_value(data_key)

    if expected != actual:
        raise RecoveryPackageError("recovered key check value mismatch")

    return data_key, metadata
