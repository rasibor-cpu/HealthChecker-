from pathlib import Path
import json

import pytest

from backend.health_vault.auth import AuthenticationService
from backend.health_vault.recovery import (
    CURRENT_SCHEMA,
    MigrationPlan,
    RecoveryError,
    VaultMigrationManager,
    create_encrypted_backup,
    restore_encrypted_backup,
)
from backend.health_vault.vault_store import VaultStore


KEY = b"v" * 32
RECOVERY_KEY = b"r" * 32


def populated_vault(root: Path) -> VaultStore:
    vault = VaultStore(root=root, encryption_key=KEY)
    auth = AuthenticationService(vault, bootstrap_password="temporary-owner-password")
    auth.create_user(user_id="10001", name="Second User", email_identifier="second", password="secondary-password")
    vault.update_profile({"display_name": "Robert", "dashboard_preferences": {"theme": "dark"}}, patient_id="00000")
    vault.update_profile({"display_name": "Second"}, patient_id="10001")
    return vault


def test_encrypted_backup_and_complete_restore_preserve_users(tmp_path):
    source = populated_vault(tmp_path / "source")
    backup = create_encrypted_backup(source, tmp_path / "backup.hcb", RECOVERY_KEY)
    raw = backup.read_bytes()
    assert b"Robert" not in raw and b"temporary-owner-password" not in raw
    restored = restore_encrypted_backup(backup, tmp_path / "restored", RECOVERY_KEY, KEY)
    assert restored.get_profile("00000")["display_name"] == "Robert"
    assert restored.get_profile("10001")["display_name"] == "Second"
    registry = (restored.root / "auth_registry.json").read_bytes()
    assert b"temporary-owner-password" not in registry


def test_wrong_recovery_key_and_corruption_fail_without_target_damage(tmp_path):
    source = populated_vault(tmp_path / "source")
    backup = create_encrypted_backup(source, tmp_path / "backup.hcb", RECOVERY_KEY)
    target = tmp_path / "target"
    target.mkdir()
    marker = target / "keep.txt"
    marker.write_text("authoritative", encoding="utf-8")
    with pytest.raises(RecoveryError):
        restore_encrypted_backup(backup, target, b"x" * 32, KEY)
    assert marker.read_text(encoding="utf-8") == "authoritative"
    damaged = tmp_path / "damaged.hcb"
    damaged.write_bytes(backup.read_bytes()[:-12] + b"corrupt-data")
    with pytest.raises(RecoveryError):
        restore_encrypted_backup(damaged, target, RECOVERY_KEY, KEY)
    assert marker.exists()


def test_wrong_vault_key_rejects_restore_and_rolls_back(tmp_path):
    source = populated_vault(tmp_path / "source")
    backup = create_encrypted_backup(source, tmp_path / "backup.hcb", RECOVERY_KEY)
    target = tmp_path / "target"
    target.mkdir()
    (target / "keep.txt").write_text("old", encoding="utf-8")
    with pytest.raises(Exception):
        restore_encrypted_backup(backup, target, RECOVERY_KEY, b"z" * 32)
    assert (target / "keep.txt").read_text(encoding="utf-8") == "old"


def test_backup_excludes_private_keys_logs_and_temporary_state(tmp_path):
    vault = populated_vault(tmp_path / "source")
    (vault.root / "server.key").write_text("secret", encoding="utf-8")
    (vault.root / "runtime.log").write_text("patient data", encoding="utf-8")
    (vault.root / "index.tmp").write_text("partial", encoding="utf-8")
    backup = create_encrypted_backup(vault, tmp_path / "backup.hcb", RECOVERY_KEY)
    assert b"secret" not in backup.read_bytes()
    restored = restore_encrypted_backup(backup, tmp_path / "restored", RECOVERY_KEY, KEY)
    assert not (restored.root / "server.key").exists()
    assert not (restored.root / "runtime.log").exists()
    assert not (restored.root / "index.tmp").exists()


def test_migration_requires_checkpoint_and_rolls_back_failure(tmp_path):
    vault = populated_vault(tmp_path / "vault")
    data = vault._read_index()
    data["schema_version"] = "hc.health_vault.v0"
    vault._write_index(data)
    checkpoints = []

    def broken(doc):
        doc["schema_version"] = CURRENT_SCHEMA
        doc["profiles_by_user_id"] = {}
        raise RuntimeError("interrupted")

    manager = VaultMigrationManager((MigrationPlan("hc.health_vault.v0", CURRENT_SCHEMA, broken),))
    with pytest.raises(RuntimeError, match="interrupted"):
        manager.migrate(vault, CURRENT_SCHEMA, lambda: checkpoints.append("created"))
    assert checkpoints == ["created"]
    rolled_back = vault._read_index()
    assert rolled_back["schema_version"] == "hc.health_vault.v0"
    assert "00000" in rolled_back["profiles_by_user_id"]


def test_unknown_or_newer_schema_fails_closed(tmp_path):
    vault = populated_vault(tmp_path / "vault")
    data = vault._read_index()
    data["schema_version"] = "hc.health_vault.v999"
    vault._write_index(data)
    with pytest.raises(RecoveryError, match="vault_schema_incompatible"):
        VaultMigrationManager().validate_current(vault)
    with pytest.raises(RecoveryError, match="migration_path_unavailable"):
        VaultMigrationManager().migrate(vault, CURRENT_SCHEMA, lambda: None)


def test_packaging_and_signing_configuration_contains_no_secrets():
    root = Path(__file__).resolve().parents[1]
    gradle = (root / "android/app/build.gradle.kts").read_text(encoding="utf-8")
    package_script = (root / "scripts/package_healthchecker_desktop.ps1").read_text(encoding="utf-8")
    install_script = (root / "scripts/install_healthchecker_desktop.ps1").read_text(encoding="utf-8")
    uninstall_script = (root / "scripts/uninstall_healthchecker_desktop.ps1").read_text(encoding="utf-8")
    assert "HC_ANDROID_KEYSTORE_FILE" in gradle
    assert "HC_ANDROID_KEYSTORE_PASSWORD" in gradle
    assert "HC_ANDROID_KEY_ALIAS" in gradle
    assert "HC_ANDROID_KEY_PASSWORD" in gradle
    assert "HC_ANDROID_REQUIRE_PRODUCTION_SIGNING" in gradle
    assert "hc_android_signing_env_incomplete" in gradle
    assert "debug.keystore" in gradle
    assert "storePassword = System.getenv" not in gradle
    assert 'storePassword = "' not in gradle
    assert 'keyPassword = "' not in gradle
    assert "versionCode = 321" in gradle
    assert 'versionName = "0.321.0"' in gradle
    assert '$trees = @("backend", "js", "css", "assets", "icons")' in package_script
    assert "vault_storage|hc_intake" in package_script
    assert "PreserveUserData" in install_script
    assert "HealthChecker.lnk" in install_script
    assert "release_integrity_failed" in install_script
    assert ".previous" in install_script
    assert "User data preserved" in uninstall_script
    assert "Remove-Item -LiteralPath $dataRoot" not in uninstall_script
    assert "Remove-Item -LiteralPath $DataRoot" not in uninstall_script
    assert "0.321.0" in (root / "config/healthchecker.release.json").read_text(encoding="utf-8")
