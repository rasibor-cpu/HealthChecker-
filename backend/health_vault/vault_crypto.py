"""HC311 authenticated encryption foundation for Health Vault storage.

This module implements the HCVE v1 binary envelope only.

It deliberately does NOT:
- create or persist production keys;
- invoke Windows DPAPI;
- access the live Health Vault;
- migrate existing plaintext data;
- modify VaultStore integration.

HCVE v1 binary layout:

    MAGIC(4)
    FORMAT_VERSION(1)
    ALGORITHM_ID(1)
    NONCE(12)
    CIPHERTEXT_AND_16_BYTE_GCM_TAG(variable)

The immutable header and caller-supplied storage context are authenticated
as AES-GCM associated authenticated data (AAD).
"""

from __future__ import annotations

import os
import struct
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


MAGIC: Final[bytes] = b"HCVE"
FORMAT_VERSION: Final[int] = 1
ALGORITHM_AES256_GCM: Final[int] = 1

KEY_BYTES: Final[int] = 32
NONCE_BYTES: Final[int] = 12
TAG_BYTES: Final[int] = 16

_HEADER: Final[struct.Struct] = struct.Struct(">4sBB12s")
HEADER_BYTES: Final[int] = _HEADER.size
MIN_ENVELOPE_BYTES: Final[int] = HEADER_BYTES + TAG_BYTES


class VaultCryptoError(RuntimeError):
    """Base class for HC311 cryptographic storage errors."""


class VaultCryptoFormatError(VaultCryptoError):
    """HCVE envelope is malformed or unsupported."""


class VaultCryptoAuthenticationError(VaultCryptoError):
    """AES-GCM authentication failed."""


class VaultCryptoKeyError(VaultCryptoError):
    """Encryption key violates the HCVE key contract."""


def _validated_key(key: bytes) -> bytes:
    if not isinstance(key, bytes):
        raise VaultCryptoKeyError("vault_key_must_be_bytes")

    if len(key) != KEY_BYTES:
        raise VaultCryptoKeyError("vault_key_must_be_32_bytes")

    return key


def _build_header(nonce: bytes) -> bytes:
    if len(nonce) != NONCE_BYTES:
        raise VaultCryptoFormatError(
            "hcve_nonce_must_be_12_bytes"
        )

    return _HEADER.pack(
        MAGIC,
        FORMAT_VERSION,
        ALGORITHM_AES256_GCM,
        nonce,
    )


def is_hcve_envelope(payload: bytes) -> bool:
    """Return True only for a plausibly sized HCVE envelope."""

    return (
        isinstance(payload, bytes)
        and len(payload) >= MIN_ENVELOPE_BYTES
        and payload.startswith(MAGIC)
    )


def encrypt_bytes(
    plaintext: bytes,
    *,
    key: bytes,
    context: bytes = b"",
) -> bytes:
    """Encrypt plaintext into an HCVE v1 AES-256-GCM envelope."""

    key = _validated_key(key)

    if not isinstance(plaintext, bytes):
        raise TypeError("plaintext_must_be_bytes")

    if not isinstance(context, bytes):
        raise TypeError("context_must_be_bytes")

    nonce = os.urandom(NONCE_BYTES)
    header = _build_header(nonce)

    ciphertext_and_tag = AESGCM(key).encrypt(
        nonce,
        plaintext,
        header + context,
    )

    return header + ciphertext_and_tag


def decrypt_bytes(
    envelope: bytes,
    *,
    key: bytes,
    context: bytes = b"",
) -> bytes:
    """Authenticate and decrypt an HCVE v1 envelope.

    All corruption, key mismatch, context mismatch, malformed format,
    unsupported version, or unsupported algorithm conditions fail closed.
    """

    key = _validated_key(key)

    if not isinstance(envelope, bytes):
        raise TypeError("envelope_must_be_bytes")

    if not isinstance(context, bytes):
        raise TypeError("context_must_be_bytes")

    if len(envelope) < MIN_ENVELOPE_BYTES:
        raise VaultCryptoFormatError(
            "hcve_envelope_too_short"
        )

    try:
        magic, version, algorithm, nonce = _HEADER.unpack(
            envelope[:HEADER_BYTES]
        )
    except struct.error as exc:
        raise VaultCryptoFormatError(
            "hcve_header_invalid"
        ) from exc

    if magic != MAGIC:
        raise VaultCryptoFormatError(
            "hcve_magic_invalid"
        )

    if version != FORMAT_VERSION:
        raise VaultCryptoFormatError(
            "hcve_version_unsupported"
        )

    if algorithm != ALGORITHM_AES256_GCM:
        raise VaultCryptoFormatError(
            "hcve_algorithm_unsupported"
        )

    ciphertext_and_tag = envelope[HEADER_BYTES:]

    try:
        return AESGCM(key).decrypt(
            nonce,
            ciphertext_and_tag,
            envelope[:HEADER_BYTES] + context,
        )
    except InvalidTag as exc:
        raise VaultCryptoAuthenticationError(
            "hcve_authentication_failed"
        ) from exc


__all__ = [
    "ALGORITHM_AES256_GCM",
    "FORMAT_VERSION",
    "HEADER_BYTES",
    "KEY_BYTES",
    "MAGIC",
    "MIN_ENVELOPE_BYTES",
    "NONCE_BYTES",
    "TAG_BYTES",
    "VaultCryptoAuthenticationError",
    "VaultCryptoError",
    "VaultCryptoFormatError",
    "VaultCryptoKeyError",
    "decrypt_bytes",
    "encrypt_bytes",
    "is_hcve_envelope",
]
