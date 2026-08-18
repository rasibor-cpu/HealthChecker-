"""One-time, fail-closed HC-320C classified plaintext-to-HCVE migration.

The source and recovery snapshot are read-only. Only explicitly user-scoped
records are admitted; ambiguous and synthetic material remains recoverable in
the snapshot and is never copied into a production account.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

from backend.health_vault.auth import AuthenticationService
from backend.health_vault.recovery import create_encrypted_backup, restore_encrypted_backup
from backend.health_vault.vault_crypto import MAGIC, encrypt_bytes
from backend.health_vault.vault_key_protector import read_protected_key, write_protected_key
from backend.health_vault.vault_store import VaultStore


OWNER = "00000"
AMBIGUOUS_DOCUMENT = "2fa74ddb-5eca-4a1f-9c68-ff66a416da3c"
SYNTHETIC_WORDS = ("synthetic", "fixture", "test", "demo", "trial", "sample", "mock")
OWNER_FIELDS = ("patient_id", "user_id", "owner_user_id", "account_id")
EXPECTED_DOCUMENTS = 6
EXPECTED_EXPLICIT = 1
EXPECTED_SYNTHETIC = 4
EXPECTED_AMBIGUOUS = 1
AUTH_CONTEXT = b"auth/registry.v1"


class MigrationBlocked(RuntimeError):
    pass


def _owner(row: dict) -> str:
    return next((str(row.get(key)).strip() for key in OWNER_FIELDS if row.get(key) is not None), "")


def _synthetic(row: dict) -> bool:
    safe = {key: row.get(key) for key in ("source", "provenance", "tags", "category", "origin", "import_meta", "meta") if key in row}
    text = json.dumps(safe, sort_keys=True).lower()
    return any(word in text for word in SYNTHETIC_WORDS)


def _document_disposition(data: dict) -> tuple[set[str], set[str], set[str]]:
    imports = {}
    for row in data.get("imports") or []:
        if isinstance(row, dict):
            imports.setdefault(str(row.get("document_id") or ""), []).append(row)
    migrate, exclude, quarantine = set(), set(), set()
    for row in data.get("documents") or []:
        if not isinstance(row, dict) or not row.get("id"):
            raise MigrationBlocked("document_contract_invalid")
        document_id = str(row["id"])
        linked = imports.get(document_id, [])
        if _synthetic(row) or (linked and all(_synthetic(item) for item in linked)):
            exclude.add(document_id)
        elif _owner(row) == OWNER:
            migrate.add(document_id)
        else:
            quarantine.add(document_id)
    if (
        len(data.get("documents") or []) != EXPECTED_DOCUMENTS
        or len(migrate) != EXPECTED_EXPLICIT
        or len(exclude) != EXPECTED_SYNTHETIC
        or len(quarantine) != EXPECTED_AMBIGUOUS
        or quarantine != {AMBIGUOUS_DOCUMENT}
    ):
        raise MigrationBlocked("authorized_disposition_drift")
    return migrate, exclude, quarantine


def build_production_index(source: dict) -> tuple[dict, dict]:
    migrate_ids, excluded_ids, quarantine_ids = _document_disposition(source)
    empty = VaultStore.__new__(VaultStore)._empty()
    selected_document = [row for row in source["documents"] if str(row.get("id")) in migrate_ids]
    empty["documents"] = selected_document

    linked_collections = ("measurements", "imports", "import_log")
    for name in linked_collections:
        empty[name] = [
            row for row in (source.get(name) or [])
            if isinstance(row, dict)
            and not _synthetic(row)
            and (_owner(row) == OWNER or str(row.get("document_id") or "") in migrate_ids)
        ]

    explicit_collections = (
        "alerts", "data_gaps", "encounters", "medications", "observations",
        "timeline_events", "cgm_sensors", "batch_audits", "ai_import_audits",
        "guardian_audits", "monitoring_audits",
    )
    for name in explicit_collections:
        empty[name] = [
            row for row in (source.get(name) or [])
            if isinstance(row, dict) and not _synthetic(row) and _owner(row) == OWNER
        ]

    intelligence = source.get("health_intelligence") or {}
    empty["health_intelligence"] = {
        "observations": [
            row for row in (intelligence.get("observations") or [])
            if isinstance(row, dict) and not _synthetic(row) and _owner(row) == OWNER
        ],
        "disclaimer": intelligence.get("disclaimer") or "",
    }
    trends = source.get("trends") or {}
    owner_trends = trends.get(OWNER) if isinstance(trends, dict) else None
    empty["trends"] = {OWNER: owner_trends} if owner_trends is not None else {}
    profiles = source.get("profiles_by_user_id") or {}
    empty["profiles_by_user_id"] = {OWNER: profiles.get(OWNER) or {"diagnoses": [], "medications": []}}
    empty["profile"] = {"diagnoses": [], "medications": []}
    empty["audit"] = [{"action": "hc320c_classified_migration", "patient_id": OWNER}]

    counts = {
        "source_documents": EXPECTED_DOCUMENTS,
        "migrated_documents": len(migrate_ids),
        "excluded_documents": len(excluded_ids),
        "quarantined_documents": len(quarantine_ids),
        "migrated_measurements": len(empty["measurements"]),
        "migrated_timeline_events": len(empty["timeline_events"]),
        "migrated_observations": len(empty["health_intelligence"]["observations"]),
        "migrated_alerts": len(empty["alerts"]),
        "migrated_trend_scopes": len(empty["trends"]),
    }
    return empty, counts


def _verify_snapshot(source: Path, snapshot: Path) -> None:
    for source_file in sorted(path for path in source.rglob("*") if path.is_file()):
        relative = source_file.relative_to(source)
        copy = snapshot / relative
        if not copy.is_file():
            raise MigrationBlocked("recovery_snapshot_incomplete")
        if hashlib.sha256(source_file.read_bytes()).digest() != hashlib.sha256(copy.read_bytes()).digest():
            raise MigrationBlocked("recovery_snapshot_mismatch")


def _filtered_registry(source: Path) -> dict:
    registry = json.loads((source / "auth_registry.json").read_text(encoding="utf-8"))
    account = (registry.get("accounts") or {}).get(OWNER)
    if not isinstance(account, dict) or not str(account.get("password_hash") or "").startswith("scrypt$"):
        raise MigrationBlocked("owner_authentication_invalid")
    audits = [row for row in registry.get("audit") or [] if isinstance(row, dict) and row.get("user_id") in (None, OWNER)]
    return {"schema_version": "hc.auth.registry.v1", "accounts": {OWNER: account}, "sessions": {}, "audit": audits}


def migrate(args: argparse.Namespace) -> dict:
    source, snapshot, target = args.source.resolve(), args.snapshot.resolve(), args.target.resolve()
    key_file, recovery_key_file = args.key_file.resolve(), args.recovery_key_file.resolve()
    if target.exists() or key_file.exists() or recovery_key_file.exists():
        raise MigrationBlocked("production_target_or_key_already_exists")
    _verify_snapshot(source, snapshot)
    source_index = json.loads((source / "index.json").read_text(encoding="utf-8"))
    production_index, counts = build_production_index(source_index)
    registry = _filtered_registry(source)
    selected_id = str(production_index["documents"][0]["id"])

    stage = target.with_name(f".{target.name}.migration.{uuid4().hex}")
    key_stage = key_file.with_name(f".{key_file.name}.migration.{uuid4().hex}")
    recovery_key_stage = recovery_key_file.with_name(f".{recovery_key_file.name}.migration.{uuid4().hex}")
    restore_target = args.restore_target.resolve()
    if restore_target.exists():
        raise MigrationBlocked("restore_target_exists")
    promoted_key = promoted_recovery_key = promoted_target = False
    try:
        vault_key, recovery_key = os.urandom(32), os.urandom(32)
        write_protected_key(key_stage, vault_key)
        write_protected_key(recovery_key_stage, recovery_key)
        if read_protected_key(key_stage) != vault_key or read_protected_key(recovery_key_stage) != recovery_key:
            raise MigrationBlocked("protected_key_verification_failed")

        candidate = VaultStore(root=stage, encryption_key=vault_key)
        candidate._write_index(production_index)
        source_payload = source / "documents" / f"{selected_id}.bin"
        target_payload = candidate.documents_dir / f"{selected_id}.bin"
        target_payload.write_bytes(encrypt_bytes(source_payload.read_bytes(), key=vault_key, context=candidate._document_crypto_context(selected_id)))
        (stage / "auth_registry.json").write_bytes(encrypt_bytes(json.dumps(registry, indent=2, sort_keys=True).encode(), key=vault_key, context=AUTH_CONTEXT))
        (stage / ".auth_enrolled").write_text("hc.auth.enrolled.v1\n", encoding="ascii")

        reopened = VaultStore(root=stage, encryption_key=vault_key)
        verified = reopened._read_index()
        if reopened.index_path.read_bytes()[:4] != MAGIC or target_payload.read_bytes()[:4] != MAGIC:
            raise MigrationBlocked("hcve_encryption_verification_failed")
        if len(verified.get("documents") or []) != 1 or reopened.read_document_bytes(None, selected_id) != source_payload.read_bytes():
            raise MigrationBlocked("production_readability_verification_failed")
        auth = AuthenticationService(reopened)
        if auth.get_account(OWNER) is None or len(auth._read().get("accounts") or {}) != 1:
            raise MigrationBlocked("authentication_registry_verification_failed")

        key_file.parent.mkdir(parents=True, exist_ok=True)
        recovery_key_file.parent.mkdir(parents=True, exist_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(key_stage, key_file); promoted_key = True
        os.replace(recovery_key_stage, recovery_key_file); promoted_recovery_key = True
        os.replace(stage, target); promoted_target = True

        live = VaultStore(root=target, encryption_key=read_protected_key(key_file))
        live._read_index()
        create_encrypted_backup(live, args.backup.resolve(), read_protected_key(recovery_key_file))
        restored = restore_encrypted_backup(
            args.backup.resolve(), restore_target,
            read_protected_key(recovery_key_file), read_protected_key(key_file),
        )
        if restored._read_index() != live._read_index() or restored.read_document_bytes(None, selected_id) != live.read_document_bytes(None, selected_id):
            raise MigrationBlocked("backup_restore_reconciliation_failed")
        return counts
    except Exception:
        if promoted_target and target.exists():
            shutil.rmtree(target, ignore_errors=True)
        if promoted_key:
            key_file.unlink(missing_ok=True)
        if promoted_recovery_key:
            recovery_key_file.unlink(missing_ok=True)
        args.backup.resolve().unlink(missing_ok=True)
        if restore_target.exists():
            shutil.rmtree(restore_target, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        key_stage.unlink(missing_ok=True)
        recovery_key_stage.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--recovery-key-file", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--restore-target", type=Path, required=True)
    args = parser.parse_args()
    counts = migrate(args)
    print(json.dumps({"result": "migration_complete", "counts": counts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
