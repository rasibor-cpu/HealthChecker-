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
from typing import Any, Callable
from uuid import uuid4

from backend.health_vault.models import utc_now
from backend.health_vault.vault_crypto import decrypt_bytes, encrypt_bytes
from backend.health_vault.vault_store import VaultStore


BACKUP_FORMAT = "hc.backup.v1"
CURRENT_SCHEMA = "hc.health_vault.v1"
BACKUP_CONTEXT = b"healthchecker/backup/v1"
EXCLUDED_NAMES = {"vault.key", "server.key", "keystore.properties"}
# Operator policy placeholders — legal/business owner fills concrete SLOs.
DEFAULT_RPO_POLICY = "OPERATOR_POLICY_PLACEHOLDER_RPO"
DEFAULT_RTO_POLICY = "OPERATOR_POLICY_PLACEHOLDER_RTO"


class RecoveryError(RuntimeError):
    pass


def _eligible_files(root: Path) -> list[Path]:
    files = []
    for path in root.rglob("*"):
        if not path.is_file() or path.name in EXCLUDED_NAMES:
            continue
        if path.name.startswith(".") and ".restore." in path.name:
            continue
        if ".tmp." in path.name or path.suffix in {".tmp", ".log", ".pid"}:
            continue
        files.append(path)
    return sorted(files, key=lambda p: p.relative_to(root).as_posix())


def _decode_backup(backup: Path, recovery_key: bytes) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Decrypt and integrity-verify a backup; never writes payload bytes to disk."""
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
            return manifest, payloads
    except RecoveryError:
        raise
    except Exception as exc:
        raise RecoveryError("backup_authentication_failed") from exc


def create_encrypted_backup(vault: VaultStore, destination: Path, recovery_key: bytes) -> Path:
    """Create an authenticated backup without writing plaintext staging files."""
    if not vault.encrypted:
        raise RecoveryError("encrypted_vault_required")
    if len(recovery_key) < 32:
        raise RecoveryError("recovery_key_insufficient")
    vault._read_index()  # authenticate source before capture
    entries: dict[str, bytes] = {}
    for path in _eligible_files(vault.root):
        rel = path.relative_to(vault.root).as_posix()
        entries[rel] = path.read_bytes()
    manifest = {
        "format": BACKUP_FORMAT,
        "vault_schema": vault._read_index().get("schema_version"),
        "created_at": utc_now(),
        "file_count": len(entries),
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in entries.items()},
        "excludes_secrets": sorted(EXCLUDED_NAMES),
        "rpo_policy": DEFAULT_RPO_POLICY,
        "rto_policy": DEFAULT_RTO_POLICY,
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


def verify_encrypted_backup(backup: Path, recovery_key: bytes) -> dict[str, Any]:
    """Fail-closed integrity verification without activating or mutating any vault."""
    manifest, payloads = _decode_backup(backup, recovery_key)
    return {
        "ok": True,
        "format": manifest.get("format"),
        "vault_schema": manifest.get("vault_schema"),
        "file_count": len(payloads),
        "created_at": manifest.get("created_at"),
        "content_fingerprint": vault_payload_fingerprint(payloads),
        "excludes_secrets": list(manifest.get("excludes_secrets") or sorted(EXCLUDED_NAMES)),
        "rpo_policy": manifest.get("rpo_policy") or DEFAULT_RPO_POLICY,
        "rto_policy": manifest.get("rto_policy") or DEFAULT_RTO_POLICY,
    }


def vault_payload_fingerprint(payloads: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(payloads):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(payloads[name]).digest())
    return digest.hexdigest()


def vault_content_fingerprint(vault: VaultStore) -> str:
    """Deterministic content fingerprint of eligible vault files (no key material)."""
    payloads = {
        path.relative_to(vault.root).as_posix(): path.read_bytes()
        for path in _eligible_files(vault.root)
    }
    return vault_payload_fingerprint(payloads)


def find_previous_vault(target_root: Path) -> Path | None:
    parent = target_root.parent
    prefix = f".{target_root.name}.previous."
    candidates = sorted(
        (p for p in parent.iterdir() if p.is_dir() and p.name.startswith(prefix)),
        key=lambda p: p.stat().st_mtime_ns,
        reverse=True,
    )
    return candidates[0] if candidates else None


def recover_interrupted_restore(target_root: Path) -> Path:
    """If promotion aborted mid-swap, restore the preserved previous vault."""
    if target_root.exists():
        raise RecoveryError("target_vault_present")
    previous = find_previous_vault(target_root)
    if previous is None:
        raise RecoveryError("previous_vault_unavailable")
    os.replace(previous, target_root)
    return target_root


def restore_encrypted_backup(
    backup: Path,
    target_root: Path,
    recovery_key: bytes,
    vault_key: bytes,
    *,
    require_empty_target: bool = False,
) -> VaultStore:
    """Validate fully, restore beside the target, then atomically promote it.

    Existing vault is moved aside only after staged restore authenticates.
    Failures roll back to the previous vault; interrupted promotion can be
    recovered via recover_interrupted_restore().
    """
    verification = verify_encrypted_backup(backup, recovery_key)
    manifest, payloads = _decode_backup(backup, recovery_key)
    if require_empty_target and target_root.exists() and any(target_root.iterdir()):
        raise RecoveryError("isolated_restore_target_not_empty")

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
        if vault_content_fingerprint(candidate) != verification["content_fingerprint"]:
            raise RecoveryError("restored_fingerprint_mismatch")
        if target_root.exists():
            os.replace(target_root, previous)
        os.replace(staged, target_root)
        promoted = True
        restored = VaultStore(root=target_root, encryption_key=vault_key)
        restored._read_index()
        if vault_content_fingerprint(restored) != verification["content_fingerprint"]:
            raise RecoveryError("activated_fingerprint_mismatch")
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


def _migrate_v0_to_v1(doc: dict[str, Any]) -> dict[str, Any]:
    """Supported legacy bridge: stamp current schema without rewriting clinical payload."""
    upgraded = json.loads(json.dumps(doc))
    upgraded["schema_version"] = CURRENT_SCHEMA
    return upgraded


DEFAULT_MIGRATION_PLANS: tuple[MigrationPlan, ...] = (
    MigrationPlan("hc.health_vault.v0", CURRENT_SCHEMA, _migrate_v0_to_v1),
)


class VaultMigrationManager:
    """Explicit schema gate. Unknown/newer stores never open silently."""

    def __init__(self, plans: tuple[MigrationPlan, ...] | None = None) -> None:
        selected = DEFAULT_MIGRATION_PLANS if plans is None else plans
        self.plans = {plan.source_schema: plan for plan in selected}

    def validate_current(self, vault: VaultStore) -> None:
        if vault._read_index().get("schema_version") != CURRENT_SCHEMA:
            raise RecoveryError("vault_schema_incompatible")

    def _append_migration_audit(self, vault: VaultStore, event: dict[str, Any]) -> None:
        """Privacy-safe migration audit: schema ids/outcomes only, no PHI payloads."""
        path = Path(vault.root) / "migration_audit.json"
        try:
            existing: list[Any] = []
            if path.exists():
                raw = path.read_bytes()
                key = getattr(vault, "_encryption_key", None)
                if key is not None and raw and raw[:1] != b"{":
                    raw = decrypt_bytes(raw, key=key, context=b"healthchecker/migration_audit/v1")
                loaded = json.loads(raw.decode("utf-8"))
                if isinstance(loaded, dict) and isinstance(loaded.get("events"), list):
                    existing = loaded["events"]
            events = existing + [event]
            payload = json.dumps(
                {"schema_version": "hc.migration_audit.v1", "events": events[-50:]},
                indent=2,
                sort_keys=True,
            ).encode("utf-8")
            key = getattr(vault, "_encryption_key", None)
            if key is not None:
                payload = encrypt_bytes(payload, key=key, context=b"healthchecker/migration_audit/v1")
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(payload)
            os.replace(tmp, path)
        except Exception:
            # Audit write failure must not leave a half-migrated active index;
            # migrate() already fail-closes the index independently.
            pass

    def migrate(self, vault: VaultStore, target_schema: str, checkpoint: Callable[[], None]) -> None:
        data = vault._read_index()
        original = json.loads(json.dumps(data))
        source_schema = str(data.get("schema_version"))
        if source_schema == target_schema:
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
            self._append_migration_audit(
                vault,
                {
                    "at": utc_now(),
                    "action": "migrate",
                    "from_schema": source_schema,
                    "to_schema": target_schema,
                    "outcome": "success",
                },
            )
        except Exception:
            vault._write_index(original)
            self._append_migration_audit(
                vault,
                {
                    "at": utc_now(),
                    "action": "migrate",
                    "from_schema": source_schema,
                    "to_schema": target_schema,
                    "outcome": "rolled_back",
                },
            )
            raise

    def migrate_with_encrypted_checkpoint(
        self,
        vault: VaultStore,
        target_schema: str,
        recovery_key: bytes,
        checkpoint_path: Path,
    ) -> Path:
        """Create an encrypted pre-migration backup, then migrate fail-closed."""
        backup = create_encrypted_backup(vault, checkpoint_path, recovery_key)
        verify_encrypted_backup(backup, recovery_key)
        self.migrate(vault, target_schema, checkpoint=lambda: None)
        self.validate_current(vault) if target_schema == CURRENT_SCHEMA else None
        return backup
