"""HC311-B Windows DPAPI protection for Health Vault data keys.

Security properties:
- Windows DPAPI CurrentUser scope.
- Versioned binary key envelope.
- Plaintext data keys are never intentionally persisted.
- Missing, malformed, corrupted, or undecryptable blobs fail closed.
- Production key creation is NOT performed automatically.

This module does not access the live Health Vault.
"""

from __future__ import annotations

import ctypes
import os
import struct
from ctypes import wintypes
from pathlib import Path
from typing import Final


MAGIC: Final[bytes] = b"HCKP"
FORMAT_VERSION: Final[int] = 1
PROVIDER_DPAPI_CURRENT_USER: Final[int] = 1
KEY_BYTES: Final[int] = 32

_HEADER: Final[struct.Struct] = struct.Struct(">4sBBI")
HEADER_BYTES: Final[int] = _HEADER.size

_ENTROPY: Final[bytes] = b"HealthChecker-HC311-B-DPAPI-v1"


class VaultKeyProtectionError(RuntimeError):
    """Base error for HC311 key protection."""


class VaultKeyFormatError(VaultKeyProtectionError):
    """Persisted key envelope is malformed or unsupported."""


class VaultKeyUnprotectError(VaultKeyProtectionError):
    """DPAPI could not recover the protected key."""


class VaultKeyPersistenceError(VaultKeyProtectionError):
    """Protected key blob could not be persisted safely."""


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _blob_from_bytes(value: bytes):
    if value:
        buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
        blob = _DATA_BLOB(
            len(value),
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
        )
        return blob, buffer

    blob = _DATA_BLOB(0, None)
    return blob, None


def _crypt32():
    if os.name != "nt":
        raise VaultKeyProtectionError("windows_dpapi_required")

    dll = ctypes.WinDLL("crypt32", use_last_error=True)

    dll.CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB),
    ]
    dll.CryptProtectData.restype = wintypes.BOOL

    dll.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p,
        ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB),
    ]
    dll.CryptUnprotectData.restype = wintypes.BOOL

    return dll


def _local_free(pointer) -> None:
    if pointer:
        ctypes.windll.kernel32.LocalFree(pointer)


def protect_key(key: bytes) -> bytes:
    """Protect exactly one AES-256 key with CurrentUser DPAPI."""

    if not isinstance(key, bytes):
        raise VaultKeyProtectionError("key_must_be_bytes")

    if len(key) != KEY_BYTES:
        raise VaultKeyProtectionError("key_must_be_32_bytes")

    crypt32 = _crypt32()

    input_blob, input_buffer = _blob_from_bytes(key)
    entropy_blob, entropy_buffer = _blob_from_bytes(_ENTROPY)
    output_blob = _DATA_BLOB()

    # Keep backing buffers alive through the native call.
    _ = (input_buffer, entropy_buffer)

    ok = crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        "HealthChecker HC311 data key",
        ctypes.byref(entropy_blob),
        None,
        None,
        0,
        ctypes.byref(output_blob),
    )

    if not ok:
        error = ctypes.get_last_error()
        raise VaultKeyProtectionError(
            f"dpapi_protect_failed:{error}"
        )

    try:
        protected = ctypes.string_at(
            output_blob.pbData,
            output_blob.cbData,
        )
    finally:
        _local_free(output_blob.pbData)

    header = _HEADER.pack(
        MAGIC,
        FORMAT_VERSION,
        PROVIDER_DPAPI_CURRENT_USER,
        len(protected),
    )

    return header + protected


def unprotect_key(envelope: bytes) -> bytes:
    """Validate and unprotect an HC311 DPAPI key envelope."""

    if not isinstance(envelope, bytes):
        raise VaultKeyFormatError("key_envelope_must_be_bytes")

    if len(envelope) < HEADER_BYTES + 1:
        raise VaultKeyFormatError("key_envelope_too_short")

    try:
        magic, version, provider, protected_length = _HEADER.unpack(
            envelope[:HEADER_BYTES]
        )
    except struct.error as exc:
        raise VaultKeyFormatError(
            "key_envelope_header_invalid"
        ) from exc

    if magic != MAGIC:
        raise VaultKeyFormatError("key_envelope_magic_invalid")

    if version != FORMAT_VERSION:
        raise VaultKeyFormatError(
            "key_envelope_version_unsupported"
        )

    if provider != PROVIDER_DPAPI_CURRENT_USER:
        raise VaultKeyFormatError(
            "key_envelope_provider_unsupported"
        )

    protected = envelope[HEADER_BYTES:]

    if protected_length != len(protected):
        raise VaultKeyFormatError(
            "key_envelope_length_mismatch"
        )

    crypt32 = _crypt32()

    protected_blob, protected_buffer = _blob_from_bytes(protected)
    entropy_blob, entropy_buffer = _blob_from_bytes(_ENTROPY)
    output_blob = _DATA_BLOB()

    _ = (protected_buffer, entropy_buffer)

    ok = crypt32.CryptUnprotectData(
        ctypes.byref(protected_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        0,
        ctypes.byref(output_blob),
    )

    if not ok:
        error = ctypes.get_last_error()
        raise VaultKeyUnprotectError(
            f"dpapi_unprotect_failed:{error}"
        )

    try:
        key = ctypes.string_at(
            output_blob.pbData,
            output_blob.cbData,
        )
    finally:
        _local_free(output_blob.pbData)

    if len(key) != KEY_BYTES:
        raise VaultKeyUnprotectError(
            "unprotected_key_wrong_length"
        )

    return key


def write_protected_key(path: Path, key: bytes) -> None:
    """Atomically persist only a DPAPI-protected key envelope."""

    path = Path(path)

    envelope = protect_key(key)

    path.parent.mkdir(parents=True, exist_ok=True)

    temp = path.with_name(
        f"{path.name}.tmp.{os.getpid()}"
    )

    try:
        with open(temp, "xb") as handle:
            handle.write(envelope)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temp, path)

    except Exception as exc:
        try:
            temp.unlink(missing_ok=True)
        except Exception:
            pass

        if isinstance(exc, VaultKeyProtectionError):
            raise

        raise VaultKeyPersistenceError(
            "protected_key_persistence_failed"
        ) from exc


def read_protected_key(path: Path) -> bytes:
    """Load and unprotect an existing protected key; never create one."""

    path = Path(path)

    if not path.is_file():
        raise VaultKeyPersistenceError(
            "protected_key_file_missing"
        )

    try:
        envelope = path.read_bytes()
    except OSError as exc:
        raise VaultKeyPersistenceError(
            "protected_key_file_unreadable"
        ) from exc

    return unprotect_key(envelope)


__all__ = [
    "FORMAT_VERSION",
    "HEADER_BYTES",
    "KEY_BYTES",
    "MAGIC",
    "PROVIDER_DPAPI_CURRENT_USER",
    "VaultKeyFormatError",
    "VaultKeyPersistenceError",
    "VaultKeyProtectionError",
    "VaultKeyUnprotectError",
    "protect_key",
    "read_protected_key",
    "unprotect_key",
    "write_protected_key",
]
