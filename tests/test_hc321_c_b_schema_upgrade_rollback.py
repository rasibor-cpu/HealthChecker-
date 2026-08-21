"""HC321-C-B: schema migration, upgrade safety, startup fail-closed proofs."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.health_vault.auth import AuthenticationService
from backend.health_vault.production_runtime import ProductionRuntimeError, create_production_vault
from backend.health_vault.recovery import (
    CURRENT_SCHEMA,
    RecoveryError,
    VaultMigrationManager,
    create_encrypted_backup,
    restore_encrypted_backup,
    verify_encrypted_backup,
)
from backend.health_vault.vault_store import VaultStore

KEY = b"v" * 32
RECOVERY = b"r" * 32


def seeded(root: Path) -> VaultStore:
    vault = VaultStore(root=root, encryption_key=KEY)
    AuthenticationService(vault, bootstrap_password="owner-password-xx")
    vault.update_profile({"display_name": "Robert"}, patient_id="00000")
    return vault


def test_old_schema_checkpoint_migrate_validate_start(tmp_path, monkeypatch):
    vault = seeded(tmp_path / "vault")
    data = vault._read_index()
    data["schema_version"] = "hc.health_vault.v0"
    vault._write_index(data)
    assert vault.get_profile("00000")["display_name"] == "Robert"

    checkpoint = tmp_path / "pre.hcb"
    manager = VaultMigrationManager()
    backup = manager.migrate_with_encrypted_checkpoint(vault, CURRENT_SCHEMA, RECOVERY, checkpoint)
    verified = verify_encrypted_backup(backup, RECOVERY)
    assert verified["ok"] is True
    assert vault._read_index()["schema_version"] == CURRENT_SCHEMA
    manager.validate_current(vault)
    assert vault.get_profile("00000")["display_name"] == "Robert"

    # Production startup accepts current schema.
    key_file = tmp_path / "vault.key"
    key_file.write_bytes(KEY)
    monkeypatch.setenv("HC_VAULT_ROOT", str(vault.root))
    monkeypatch.setenv("HC_VAULT_KEY_FILE", str(key_file))
    opened = create_production_vault(key_reader=lambda p: Path(p).read_bytes())
    assert opened._read_index()["schema_version"] == CURRENT_SCHEMA


def test_failed_migration_fail_closed_then_restore_from_checkpoint(tmp_path):
    vault = seeded(tmp_path / "vault")
    data = vault._read_index()
    data["schema_version"] = "hc.health_vault.v0"
    vault._write_index(data)
    checkpoint = tmp_path / "pre.hcb"
    create_encrypted_backup(vault, checkpoint, RECOVERY)

    def corrupt(doc):
        doc["schema_version"] = CURRENT_SCHEMA
        doc["profiles_by_user_id"] = {}
        raise RuntimeError("migration_interrupted")

    from backend.health_vault.recovery import MigrationPlan

    manager = VaultMigrationManager(
        (MigrationPlan("hc.health_vault.v0", CURRENT_SCHEMA, corrupt),)
    )
    with pytest.raises(RuntimeError, match="migration_interrupted"):
        manager.migrate(vault, CURRENT_SCHEMA, checkpoint=lambda: None)
    rolled = vault._read_index()
    assert rolled["schema_version"] == "hc.health_vault.v0"
    assert "00000" in rolled["profiles_by_user_id"]
    assert vault.get_profile("00000")["display_name"] == "Robert"

    isolated = tmp_path / "restored"
    restored = restore_encrypted_backup(checkpoint, isolated, RECOVERY, KEY, require_empty_target=True)
    assert restored.get_profile("00000")["display_name"] == "Robert"
    assert restored._read_index()["schema_version"] == "hc.health_vault.v0"


def test_future_schema_rejected_at_startup(tmp_path, monkeypatch):
    vault = seeded(tmp_path / "vault")
    data = vault._read_index()
    data["schema_version"] = "hc.health_vault.v999"
    vault._write_index(data)
    key_file = tmp_path / "vault.key"
    key_file.write_bytes(KEY)
    monkeypatch.setenv("HC_VAULT_ROOT", str(vault.root))
    monkeypatch.setenv("HC_VAULT_KEY_FILE", str(key_file))
    with pytest.raises(ProductionRuntimeError, match="schema_incompatible"):
        create_production_vault(key_reader=lambda p: Path(p).read_bytes())


def test_migration_idempotent_on_current_schema(tmp_path):
    vault = seeded(tmp_path / "vault")
    manager = VaultMigrationManager()
    manager.migrate(vault, CURRENT_SCHEMA, checkpoint=lambda: (_ for _ in ()).throw(AssertionError("no checkpoint")))
    manager.validate_current(vault)


def test_ops_docs_and_script_exist():
    root = Path(__file__).resolve().parents[1]
    doc = (root / "docs/ops/HC321_C_B_SCHEMA_UPGRADE_ROLLBACK.md").read_text(encoding="utf-8")
    script = (root / "scripts/hc_schema_migrate.ps1").read_text(encoding="utf-8")
    assert "OPERATOR" in doc or "checkpoint" in doc.lower()
    assert "migrate_with_encrypted_checkpoint" in script or "CheckpointAndMigrate" in script
    assert "vault.key" not in script.lower() or "VaultKeyFile" in script
