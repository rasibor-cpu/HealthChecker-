import os
import unittest

from backend.health_vault.vault_recovery import (
    MAGIC,
    RecoveryPackageError,
    create_recovery_package,
    key_check_value,
    open_recovery_package,
)


class HC311F8BRecoveryTests(unittest.TestCase):

    def setUp(self):
        self.key = os.urandom(32)
        self.passphrase = "Temporary-HC311-Recovery-Passphrase-Only"
        self.kwargs = {
            "key_id": "TEMP-HC311-KEY-001",
            "machine_identity": "TEST-MACHINE",
            "runtime_sid": "S-1-5-18",
        }

    def package(self):
        return create_recovery_package(
            self.key,
            self.passphrase,
            **self.kwargs,
        )

    def test_roundtrip(self):
        package = self.package()
        recovered, metadata = open_recovery_package(
            package,
            self.passphrase,
        )
        self.assertEqual(recovered, self.key)
        self.assertEqual(metadata["key_id"], "TEMP-HC311-KEY-001")
        self.assertEqual(metadata["runtime_sid"], "S-1-5-18")

    def test_magic(self):
        self.assertTrue(self.package().startswith(MAGIC))

    def test_plaintext_key_absent_from_package(self):
        self.assertNotIn(self.key, self.package())

    def test_passphrase_absent_from_package(self):
        package = self.package()
        self.assertNotIn(self.passphrase.encode("utf-8"), package)

    def test_wrong_passphrase_fails_closed(self):
        with self.assertRaises(RecoveryPackageError):
            open_recovery_package(
                self.package(),
                "Wrong-Recovery-Passphrase-For-Test",
            )

    def test_ciphertext_tamper_fails_closed(self):
        package = bytearray(self.package())
        package[-1] ^= 0x01
        with self.assertRaises(RecoveryPackageError):
            open_recovery_package(
                bytes(package),
                self.passphrase,
            )

    def test_metadata_tamper_fails_closed(self):
        package = bytearray(self.package())
        index = package.find(b"TEMP-HC311-KEY-001")
        self.assertGreater(index, 0)
        package[index] ^= 0x01

        with self.assertRaises(RecoveryPackageError):
            open_recovery_package(
                bytes(package),
                self.passphrase,
            )

    def test_bad_magic_rejected(self):
        package = bytearray(self.package())
        package[0] ^= 0x01
        with self.assertRaises(RecoveryPackageError):
            open_recovery_package(
                bytes(package),
                self.passphrase,
            )

    def test_truncated_package_rejected(self):
        with self.assertRaises(RecoveryPackageError):
            open_recovery_package(
                self.package()[:20],
                self.passphrase,
            )

    def test_short_passphrase_rejected(self):
        with self.assertRaises(RecoveryPackageError):
            create_recovery_package(
                self.key,
                "short",
                **self.kwargs,
            )

    def test_wrong_key_length_rejected(self):
        with self.assertRaises(RecoveryPackageError):
            create_recovery_package(
                os.urandom(31),
                self.passphrase,
                **self.kwargs,
            )

    def test_packages_are_nondeterministic(self):
        a = self.package()
        b = self.package()
        self.assertNotEqual(a, b)

    def test_key_check_value_is_non_key_digest(self):
        check = key_check_value(self.key)
        self.assertEqual(len(check), 64)
        self.assertNotEqual(check, self.key.hex())


if __name__ == "__main__":
    unittest.main(verbosity=2)
