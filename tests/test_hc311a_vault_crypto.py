from __future__ import annotations

import unittest

from backend.health_vault.vault_crypto import (
    FORMAT_VERSION,
    HEADER_BYTES,
    MAGIC,
    NONCE_BYTES,
    VaultCryptoAuthenticationError,
    VaultCryptoFormatError,
    VaultCryptoKeyError,
    decrypt_bytes,
    encrypt_bytes,
    is_hcve_envelope,
)


TEST_KEY = bytes(range(32))
OTHER_KEY = bytes(reversed(range(32)))


class Hc311VaultCryptoTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        plaintext = b"HealthChecker HC311 protected payload"
        context = b"index.json"

        envelope = encrypt_bytes(
            plaintext,
            key=TEST_KEY,
            context=context,
        )

        self.assertTrue(is_hcve_envelope(envelope))
        self.assertNotEqual(envelope, plaintext)
        self.assertEqual(envelope[:4], MAGIC)

        recovered = decrypt_bytes(
            envelope,
            key=TEST_KEY,
            context=context,
        )

        self.assertEqual(recovered, plaintext)

    def test_unique_nonce_for_same_plaintext(self) -> None:
        first = encrypt_bytes(
            b"same plaintext",
            key=TEST_KEY,
            context=b"document",
        )

        second = encrypt_bytes(
            b"same plaintext",
            key=TEST_KEY,
            context=b"document",
        )

        nonce1 = first[6 : 6 + NONCE_BYTES]
        nonce2 = second[6 : 6 + NONCE_BYTES]

        self.assertNotEqual(first, second)
        self.assertNotEqual(nonce1, nonce2)

    def test_plaintext_marker_absent_from_envelope(self) -> None:
        plaintext = (
            b"PATIENT_SECRET_MARKER_"
            b"HC311_DO_NOT_STORE_PLAINTEXT"
        )

        envelope = encrypt_bytes(
            plaintext,
            key=TEST_KEY,
        )

        self.assertNotIn(plaintext, envelope)

    def test_wrong_key_rejected(self) -> None:
        envelope = encrypt_bytes(
            b"secret",
            key=TEST_KEY,
        )

        with self.assertRaises(
            VaultCryptoAuthenticationError
        ):
            decrypt_bytes(
                envelope,
                key=OTHER_KEY,
            )

    def test_ciphertext_tamper_rejected(self) -> None:
        envelope = bytearray(
            encrypt_bytes(
                b"secret",
                key=TEST_KEY,
            )
        )

        envelope[-17] ^= 0x01

        with self.assertRaises(
            VaultCryptoAuthenticationError
        ):
            decrypt_bytes(
                bytes(envelope),
                key=TEST_KEY,
            )

    def test_tag_tamper_rejected(self) -> None:
        envelope = bytearray(
            encrypt_bytes(
                b"secret",
                key=TEST_KEY,
            )
        )

        envelope[-1] ^= 0x01

        with self.assertRaises(
            VaultCryptoAuthenticationError
        ):
            decrypt_bytes(
                bytes(envelope),
                key=TEST_KEY,
            )

    def test_context_mismatch_rejected(self) -> None:
        envelope = encrypt_bytes(
            b"secret",
            key=TEST_KEY,
            context=b"index.json",
        )

        with self.assertRaises(
            VaultCryptoAuthenticationError
        ):
            decrypt_bytes(
                envelope,
                key=TEST_KEY,
                context=b"documents/one.bin",
            )

    def test_short_envelope_rejected(self) -> None:
        with self.assertRaises(
            VaultCryptoFormatError
        ):
            decrypt_bytes(
                b"HCVE",
                key=TEST_KEY,
            )

    def test_bad_magic_rejected(self) -> None:
        envelope = bytearray(
            encrypt_bytes(
                b"secret",
                key=TEST_KEY,
            )
        )

        envelope[0:4] = b"NOPE"

        with self.assertRaises(
            VaultCryptoFormatError
        ):
            decrypt_bytes(
                bytes(envelope),
                key=TEST_KEY,
            )

    def test_unknown_version_rejected(self) -> None:
        envelope = bytearray(
            encrypt_bytes(
                b"secret",
                key=TEST_KEY,
            )
        )

        envelope[4] = FORMAT_VERSION + 1

        with self.assertRaises(
            VaultCryptoFormatError
        ):
            decrypt_bytes(
                bytes(envelope),
                key=TEST_KEY,
            )

    def test_unknown_algorithm_rejected(self) -> None:
        envelope = bytearray(
            encrypt_bytes(
                b"secret",
                key=TEST_KEY,
            )
        )

        envelope[5] = 0x7F

        with self.assertRaises(
            VaultCryptoFormatError
        ):
            decrypt_bytes(
                bytes(envelope),
                key=TEST_KEY,
            )

    def test_invalid_key_lengths_rejected(self) -> None:
        for key in (
            b"x" * 31,
            b"x" * 33,
        ):
            with self.assertRaises(
                VaultCryptoKeyError
            ):
                encrypt_bytes(
                    b"secret",
                    key=key,
                )

    def test_non_bytes_key_rejected(self) -> None:
        with self.assertRaises(
            VaultCryptoKeyError
        ):
            encrypt_bytes(  # type: ignore[arg-type]
                b"secret",
                key="not-bytes",
            )

    def test_plaintext_json_not_file_prefix(self) -> None:
        envelope = encrypt_bytes(
            b'{"patient":"private"}',
            key=TEST_KEY,
        )

        self.assertEqual(
            envelope[:HEADER_BYTES][:4],
            MAGIC,
        )

        self.assertFalse(
            envelope.startswith(b"{")
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
