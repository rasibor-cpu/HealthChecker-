"""Encrypted, atomic backup/restore and version-gated vault migration."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable
from uuid import uuid4

from backend.health_vault.vault_crypto import decrypt_bytes, encrypt_bytes
from backend.health_vault.vault_store import VaultStore


BACKUP_FORMAT = "hc.backup.v1"
CURRENT_SCHEMA = "hc.health_vault.v1"
BACKUP_CONTEXT = b"healthchecker/backup/v1"
EXCLUDED_NAMES = {"vault.key", "server.key", "keystore.properties"}


class RecoveryError(RuntimeError):
    pass


def _eligible_files(root: Path) -> list[Path]:
    files = []
    for path in root.rglob("*"):
        if not path.is_file() or path.name in EXCLUDED_NAMES:
            continue
        if ".tmp." in path.name or path.suffix in {".tmp", ".log", ".pid"}:
            continue
        files.append(path)
    return sorted(files, key=lambda p: p.relative_to(root).as_posix())


def create_encrypted_backup(vault: VaultStore, destination: Path, recovery_key: bytes) -> Path:
    """Create an authenticated backup without writing plaintext staging files."""
    if not vault.encrypted:
        raise RecoveryError("encrypted_vault_required")
    vault._read_index()  # authenticate source before capture
    entries: dict[str, bytes] = {}
    for path in _eligible_files(vault.root):
        rel = path.relative_to(vault.root).as_posix()
        entries[rel] = path.read_bytes()
    manifest = {
        "format": BACKUP_FORMAT,
        "vault_schema": vault._read_index().get("schema_version"),
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in entries.items()},
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True, separators=(",", ":")))
        for name, data in entries.items():
            archive.writestr(f"payload/{name}", data)
    sealed = encrypt_bytes(stream.getvalue(), key=recovery_key, context=BACKUP_CONTEXT)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp.{os.getpid()}.{uuid4().hex}")
    try:
        temporary.write_bytes(sealed)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def restore_encrypted_backup(backup: Path, target_root: Path, recovery_key: bytes, vault_key: bytes) -> VaultStore:
    """Validate fully, restore beside the target, then atomically promote it."""
    try:
        plaintext = decrypt_bytes(backup.read_bytes(), key=recovery_key, context=BACKUP_CONTEXT)
        with zipfile.ZipFile(io.BytesIO(plaintext), "r") as archive:
            names = set(archive.namelist())
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("format") != BACKUP_FORMAT:
                raise RecoveryError("backup_format_incompatible")
            files = manifest.get("files")
            if not isinstance(files, dict) or not files:
                raise RecoveryError("backup_incomplete")
            payloads: dict[str, bytes] = {}
            for rel, expected in files.items():
                pure = PurePosixPath(rel)
                if pure.is_absolute() or ".." in pure.parts or rel in EXCLUDED_NAMES:
                    raise RecoveryError("backup_path_invalid")
                member = f"payload/{rel}"
                if member not in names:
                    raise RecoveryError("backup_incomplete")
                data = archive.read(member)
                if hashlib.sha256(data).hexdigest() != expected:
                    raise RecoveryError("backup_integrity_failed")
                payloads[rel] = data
    except RecoveryError:
        raise
    except Exception as exc:
        raise RecoveryError("backup_authentication_failed") from exc

    staged = target_root.with_name(f".{target_root.name}.restore.{uuid4().hex}")
    previous = target_root.with_name(f".{target_root.name}.previous.{uuid4().hex}")
    promoted = False
    try:
        for rel, data in payloads.items():
            path = staged.joinpath(*PurePosixPath(rel).parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        candidate = VaultStore(root=staged, encryption_key=vault_key)
        index = candidate._read_index()
        if index.get("schema_version") != manifest.get("vault_schema"):
            raise RecoveryError("restored_schema_mismatch")
        if target_root.exists():
            os.replace(target_root, previous)
        os.replace(staged, target_root)
        promoted = True
        restored = VaultStore(root=target_root, encryption_key=vault_key)
        restored._read_index()
        shutil.rmtree(previous, ignore_errors=True)
        return restored
    except Exception:
        shutil.rmtree(staged, ignore_errors=True)
        if promoted and target_root.exists():
            failed = target_root.with_name(f".{target_root.name}.failed.{uuid4().hex}")
            os.replace(target_root, failed)
            shutil.rmtree(failed, ignore_errors=True)
        if previous.exists() and not target_root.exists():
            os.replace(previous, target_root)
        raise


@dataclass(frozen=True)
class MigrationPlan:
    source_schema: str
    target_schema: str
    apply: Callable[[dict], dict]


class VaultMigrationManager:
    """Explicit schema gate. Unknown/newer stores never open silently."""

    def __init__(self, plans: tuple[MigrationPlan, ...] = ()) -> None:
        self.plans = {plan.source_schema: plan for plan in plans}

    def validate_current(self, vault: VaultStore) -> None:
        if vault._read_index().get("schema_version") != CURRENT_SCHEMA:
            raise RecoveryError("vault_schema_incompatible")

    def migrate(self, vault: VaultStore, target_schema: str, checkpoint: Callable[[], None]) -> None:
        data = vault._read_index()
        original = json.loads(json.dumps(data))
        if data.get("schema_version") == target_schema:
            return
        checkpoint()
        try:
            while data.get("schema_version") != target_schema:
                plan = self.plans.get(str(data.get("schema_version")))
                if plan is None:
                    raise RecoveryError("migration_path_unavailable")
                data = plan.apply(json.loads(json.dumps(data)))
                if data.get("schema_version") != plan.target_schema:
                    raise RecoveryError("migration_contract_failed")
            vault._write_index(data)
            if vault._read_index().get("schema_version") != target_schema:
                raise RecoveryError("migration_postcheck_failed")
        except Exception:
            vault._write_index(original)
            raise
