from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.health_vault.models import MedicalDocument
from backend.health_vault.vault_crypto import (
    MAGIC,
    VaultCryptoAuthenticationError,
)
from backend.health_vault.vault_store import VaultStore


TEST_KEY = bytes(range(32))
OTHER_KEY = bytes(reversed(range(32)))


def _document(document_id: str = "doc-hc311") -> MedicalDocument:
    return MedicalDocument(
        id=document_id,
        original_filename="hc311.txt",
        mime_type="text/plain",
        source_system="hc311-test",
        sha256=None,
    )


class Hc311CEncryptedVaultStoreTests(unittest.TestCase):
    def test_default_mode_remains_plaintext_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = VaultStore(root=Path(temp) / "vault")

            raw = store.index_path.read_bytes()

            self.assertTrue(raw.startswith(b"{"))
            self.assertFalse(store.encrypted)

    def test_encrypted_empty_index_is_hcve_not_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = VaultStore(
                root=Path(temp) / "vault",
                encryption_key=TEST_KEY,
            )

            raw = store.index_path.read_bytes()

            self.assertTrue(store.encrypted)
            self.assertTrue(raw.startswith(MAGIC))
            self.assertFalse(raw.startswith(b"{"))
            self.assertNotIn(b"schema_version", raw)

            data = store._read_index()
            self.assertEqual(
                data["schema_version"],
                "hc.health_vault.v1",
            )

    def test_encrypted_index_persists_across_reinstantiation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "vault"

            store1 = VaultStore(
                root=root,
                encryption_key=TEST_KEY,
            )

            store1.update_profile(
                {"hc311_marker": "sensitive-profile-value"}
            )

            self.assertNotIn(
                b"sensitive-profile-value",
                store1.index_path.read_bytes(),
            )

            store2 = VaultStore(
                root=root,
                encryption_key=TEST_KEY,
            )

            self.assertEqual(
                store2.get_profile()["hc311_marker"],
                "sensitive-profile-value",
            )

    def test_wrong_index_key_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "vault"

            store = VaultStore(
                root=root,
                encryption_key=TEST_KEY,
            )

            store.update_profile({"secret": "value"})

            wrong = VaultStore(
                root=root,
                encryption_key=OTHER_KEY,
            )

            with self.assertRaises(
                VaultCryptoAuthenticationError
            ):
                wrong.get_profile()

    def test_index_tamper_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "vault"

            store = VaultStore(
                root=root,
                encryption_key=TEST_KEY,
            )

            store.update_profile({"secret": "value"})

            raw = bytearray(store.index_path.read_bytes())
            raw[-1] ^= 1
            store.index_path.write_bytes(bytes(raw))

            with self.assertRaises(
                VaultCryptoAuthenticationError
            ):
                store.get_profile()

    def test_document_blob_is_encrypted_and_readable_via_api(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "vault"
            store = VaultStore(
                root=root,
                encryption_key=TEST_KEY,
            )

            content = (
                b"HC311_PRIVATE_DOCUMENT_MARKER_"
                b"DO_NOT_PERSIST_AS_PLAINTEXT"
            )

            document = _document()

            result = store.store(
                document=document,
                measurements=[],
                content=content,
            )

            uri = result["document"]["storage_uri"]
            path = store.resolve_storage_path(
                uri,
                document.id,
            )

            self.assertIsNotNone(path)

            raw = path.read_bytes()

            self.assertTrue(raw.startswith(MAGIC))
            self.assertNotEqual(raw, content)
            self.assertNotIn(content, raw)

            recovered = store.read_document_bytes(
                uri,
                document.id,
            )

            self.assertEqual(recovered, content)

    def test_document_size_remains_plaintext_size(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = VaultStore(
                root=Path(temp) / "vault",
                encryption_key=TEST_KEY,
            )

            content = b"123456789"

            result = store.store(
                document=_document("size-doc"),
                measurements=[],
                content=content,
            )

            self.assertEqual(
                result["document"]["size_bytes"],
                len(content),
            )

    def test_encrypted_document_tamper_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = VaultStore(
                root=Path(temp) / "vault",
                encryption_key=TEST_KEY,
            )

            document = _document("tamper-doc")

            result = store.store(
                document=document,
                measurements=[],
                content=b"sensitive bytes",
            )

            uri = result["document"]["storage_uri"]
            path = store.resolve_storage_path(
                uri,
                document.id,
            )

            raw = bytearray(path.read_bytes())
            raw[-1] ^= 1
            path.write_bytes(bytes(raw))

            with self.assertRaises(
                VaultCryptoAuthenticationError
            ):
                store.read_document_bytes(
                    uri,
                    document.id,
                )

    def test_batch_store_encrypts_document_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = VaultStore(
                root=Path(temp) / "vault",
                encryption_key=TEST_KEY,
            )

            document = _document("batch-doc")
            content = b"HC311 batch protected content"

            with store.observation_batch() as batch:
                result = store.batch_store(
                    batch,
                    document=document,
                    measurements=[],
                    content=content,
                )

            uri = result["document"]["storage_uri"]
            path = store.resolve_storage_path(
                uri,
                document.id,
            )

            raw = path.read_bytes()

            self.assertTrue(raw.startswith(MAGIC))
            self.assertNotIn(content, raw)

            self.assertEqual(
                store.read_document_bytes(
                    uri,
                    document.id,
                ),
                content,
            )

    def test_plaintext_document_mode_preserves_existing_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = VaultStore(
                root=Path(temp) / "vault",
            )

            document = _document("plain-doc")
            content = b"legacy plaintext semantics"

            result = store.store(
                document=document,
                measurements=[],
                content=content,
            )

            uri = result["document"]["storage_uri"]
            path = store.resolve_storage_path(
                uri,
                document.id,
            )

            self.assertEqual(
                path.read_bytes(),
                content,
            )

            self.assertEqual(
                store.read_document_bytes(
                    uri,
                    document.id,
                ),
                content,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
