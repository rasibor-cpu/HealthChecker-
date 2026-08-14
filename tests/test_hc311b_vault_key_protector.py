from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from backend.health_vault.vault_key_protector import (
    FORMAT_VERSION,
    HEADER_BYTES,
    MAGIC,
    VaultKeyFormatError,
    VaultKeyPersistenceError,
    VaultKeyUnprotectError,
    protect_key,
    read_protected_key,
    unprotect_key,
    write_protected_key,
)


@unittest.skipUnless(os.name == "nt", "Windows DPAPI required")
class Hc311BDpapiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.key = bytes(range(32))

    def test_memory_roundtrip(self) -> None:
        envelope = protect_key(self.key)

        self.assertTrue(envelope.startswith(MAGIC))
        self.assertNotIn(self.key, envelope)
        self.assertEqual(unprotect_key(envelope), self.key)

    def test_same_key_produces_distinct_dpapi_blobs(self) -> None:
        first = protect_key(self.key)
        second = protect_key(self.key)

        self.assertNotEqual(first, second)
        self.assertEqual(unprotect_key(first), self.key)
        self.assertEqual(unprotect_key(second), self.key)

    def test_wrong_key_length_rejected(self) -> None:
        with self.assertRaises(Exception):
            protect_key(b"x" * 31)

    def test_bad_magic_rejected(self) -> None:
        envelope = bytearray(protect_key(self.key))
        envelope[0:4] = b"NOPE"

        with self.assertRaises(VaultKeyFormatError):
            unprotect_key(bytes(envelope))

    def test_unknown_version_rejected(self) -> None:
        envelope = bytearray(protect_key(self.key))
        envelope[4] = FORMAT_VERSION + 1

        with self.assertRaises(VaultKeyFormatError):
            unprotect_key(bytes(envelope))

    def test_unknown_provider_rejected(self) -> None:
        envelope = bytearray(protect_key(self.key))
        envelope[5] = 0x7F

        with self.assertRaises(VaultKeyFormatError):
            unprotect_key(bytes(envelope))

    def test_length_tamper_rejected(self) -> None:
        envelope = bytearray(protect_key(self.key))

        # HCKP(4), version(1), provider(1), length(4)
        envelope[6:10] = (1).to_bytes(4, "big")

        with self.assertRaises(VaultKeyFormatError):
            unprotect_key(bytes(envelope))

    def test_dpapi_blob_tamper_fails_closed(self) -> None:
        envelope = bytearray(protect_key(self.key))
        envelope[-1] ^= 0x01

        with self.assertRaises(VaultKeyUnprotectError):
            unprotect_key(bytes(envelope))

    def test_temp_directory_persistence_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "vault.key"

            write_protected_key(path, self.key)

            raw = path.read_bytes()

            self.assertTrue(raw.startswith(MAGIC))
            self.assertNotIn(self.key, raw)
            self.assertEqual(
                read_protected_key(path),
                self.key,
            )

    def test_missing_key_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "missing.key"

            with self.assertRaises(VaultKeyPersistenceError):
                read_protected_key(path)

    def test_plaintext_key_not_present_in_persisted_blob(self) -> None:
        marker_key = b"HC311-PLAINTEXT-KEY-MARKER-12345"

        self.assertEqual(len(marker_key), 32)

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "vault.key"

            write_protected_key(path, marker_key)

            raw = path.read_bytes()

            self.assertNotIn(marker_key, raw)
            self.assertEqual(
                read_protected_key(path),
                marker_key,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
