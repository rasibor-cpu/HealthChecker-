"""HC321-C-A: encrypted backup, verify, isolated restore, fail-closed DR proofs."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from backend.health_vault.auth import AuthenticationService
from backend.health_vault.recovery import (
    DEFAULT_RPO_POLICY,
    DEFAULT_RTO_POLICY,
    RecoveryError,
    create_encrypted_backup,
    recover_interrupted_restore,
    restore_encrypted_backup,
    vault_content_fingerprint,
    verify_encrypted_backup,
)
from backend.health_vault.vault_store import VaultStore

KEY = b"v" * 32
RECOVERY_KEY = b"r" * 32
ROOT = Path(__file__).resolve().parents[1]
SECRET_PATTERNS = [
    re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)(api[_-]?key|secret|password)\s*[:=]\s*['\"][^'\"]{12,}"),
    re.compile(r"(?i)HC_ANDROID_KEYSTORE_PASSWORD\s*=\s*\S+"),
]


def populated_vault(root: Path) -> VaultStore:
    vault = VaultStore(root=root, encryption_key=KEY)
    auth = AuthenticationService(vault, bootstrap_password="temporary-owner-password")
    auth.create_user(
        user_id="10001",
        name="Second User",
        email_identifier="second",
        password="secondary-password",
    )
    vault.update_profile(
        {"display_name": "Robert", "dashboard_preferences": {"theme": "dark"}},
        patient_id="00000",
    )
    vault.update_profile({"display_name": "Second"}, patient_id="10001")
    return vault


def test_create_verify_isolated_restore_compare_chain(tmp_path):
    source = populated_vault(tmp_path / "source")
    source_fp = vault_content_fingerprint(source)
    backup = create_encrypted_backup(source, tmp_path / "backup.hcb", RECOVERY_KEY)
    raw = backup.read_bytes()
    assert b"Robert" not in raw
    assert b"temporary-owner-password" not in raw
    assert b"secondary-password" not in raw

    verified = verify_encrypted_backup(backup, RECOVERY_KEY)
    assert verified["ok"] is True
    assert verified["file_count"] >= 1
    assert verified["content_fingerprint"] == source_fp
    assert verified["rpo_policy"] == DEFAULT_RPO_POLICY
    assert verified["rto_policy"] == DEFAULT_RTO_POLICY
    assert "vault.key" in verified["excludes_secrets"]

    isolated = tmp_path / "isolated_restore"
    restored = restore_encrypted_backup(
        backup, isolated, RECOVERY_KEY, KEY, require_empty_target=True
    )
    assert vault_content_fingerprint(restored) == source_fp
    assert restored.get_profile("00000")["display_name"] == "Robert"
    assert restored.get_profile("10001")["display_name"] == "Second"
    # Source vault untouched.
    assert source.get_profile("00000")["display_name"] == "Robert"
    assert vault_content_fingerprint(source) == source_fp


def test_corruption_and_wrong_key_fail_closed_preserve_working_vault(tmp_path):
    source = populated_vault(tmp_path / "source")
    backup = create_encrypted_backup(source, tmp_path / "backup.hcb", RECOVERY_KEY)
    working = populated_vault(tmp_path / "working")
    # Sidecar proves rejection paths leave the working vault root intact.
    keep = working.root / "keep_alive.flag"
    keep.write_text("authoritative-working-vault", encoding="utf-8")
    before = list(p.name for p in working.root.iterdir())

    with pytest.raises(RecoveryError):
        verify_encrypted_backup(backup, b"x" * 32)
    assert keep.read_text(encoding="utf-8") == "authoritative-working-vault"

    damaged = tmp_path / "damaged.hcb"
    damaged.write_bytes(backup.read_bytes()[:-16] + b"CORRUPTED-PAYLOAD!!")
    with pytest.raises(RecoveryError):
        verify_encrypted_backup(damaged, RECOVERY_KEY)
    with pytest.raises(RecoveryError):
        restore_encrypted_backup(damaged, working.root, RECOVERY_KEY, KEY)
    assert keep.exists()
    assert sorted(p.name for p in working.root.iterdir()) == sorted(before)

    with pytest.raises(Exception):
        restore_encrypted_backup(backup, working.root, RECOVERY_KEY, b"z" * 32)
    assert keep.read_text(encoding="utf-8") == "authoritative-working-vault"
    assert working.get_profile("00000")["display_name"] == "Robert"


def test_interrupted_restore_recovers_previous_vault(tmp_path):
    source = populated_vault(tmp_path / "source")
    backup = create_encrypted_backup(source, tmp_path / "backup.hcb", RECOVERY_KEY)
    target = tmp_path / "target"
    working = populated_vault(target)
    working.update_profile({"display_name": "LiveBeforeRestore"}, patient_id="00000")

    # Simulate interrupted promotion: live vault moved aside, target missing.
    previous = target.with_name(f".{target.name}.previous.sim")
    os.replace(target, previous)
    assert not target.exists()
    recovered = recover_interrupted_restore(target)
    assert recovered == target
    restored_live = VaultStore(root=target, encryption_key=KEY)
    assert restored_live.get_profile("00000")["display_name"] == "LiveBeforeRestore"

    # Successful restore still works after recovery.
    restore_encrypted_backup(backup, target, RECOVERY_KEY, KEY)
    assert VaultStore(root=target, encryption_key=KEY).get_profile("00000")["display_name"] == "Robert"


def test_isolated_restore_refuses_non_empty_target(tmp_path):
    source = populated_vault(tmp_path / "source")
    backup = create_encrypted_backup(source, tmp_path / "backup.hcb", RECOVERY_KEY)
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "noise.txt").write_text("nope", encoding="utf-8")
    with pytest.raises(RecoveryError, match="isolated_restore_target_not_empty"):
        restore_encrypted_backup(
            backup, occupied, RECOVERY_KEY, KEY, require_empty_target=True
        )
    assert (occupied / "noise.txt").read_text(encoding="utf-8") == "nope"


def test_secrets_excluded_and_changed_artifacts_secret_scan():
    production_files = [
        ROOT / "backend/health_vault/recovery.py",
        ROOT / "docs/ops/HC321_C_A_BACKUP_RESTORE_DR.md",
        ROOT / "scripts/hc_backup_restore_dr.ps1",
    ]
    for path in production_files:
        text = path.read_text(encoding="utf-8")
        for pattern in SECRET_PATTERNS:
            assert not pattern.search(text), f"secret-like pattern in {path}"
    # Test fixtures may contain synthetic passwords; ensure no PEM private-key blocks.
    test_text = Path(__file__).read_text(encoding="utf-8")
    assert not SECRET_PATTERNS[0].search(test_text)
    pem_marker = "-----BEGIN " + "RSA PRIVATE KEY-----"
    assert pem_marker not in test_text


def test_backup_artifact_contains_no_plaintext_health_markers(tmp_path):
    source = populated_vault(tmp_path / "source")
    (source.root / "server.key").write_text("tls-private", encoding="utf-8")
    backup = create_encrypted_backup(source, tmp_path / "backup.hcb", RECOVERY_KEY)
    raw = backup.read_bytes()
    for needle in (b"Robert", b"Second", b"tls-private", b"temporary-owner", b"secondary-password"):
        assert needle not in raw
