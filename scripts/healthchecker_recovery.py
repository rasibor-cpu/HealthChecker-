"""Operator entry point for encrypted HealthChecker backup and isolated restore."""

from __future__ import annotations

import argparse
from pathlib import Path

from backend.health_vault.production_runtime import create_production_vault
from backend.health_vault.recovery import (
    VaultMigrationManager,
    create_encrypted_backup,
    restore_encrypted_backup,
)
from backend.health_vault.vault_key_protector import read_protected_key


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="HealthChecker encrypted recovery utility")
    commands = result.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("--output", type=Path, required=True)
    backup.add_argument("--recovery-key-file", type=Path, required=True)
    restore = commands.add_parser("restore-isolated")
    restore.add_argument("--backup", type=Path, required=True)
    restore.add_argument("--target", type=Path, required=True)
    restore.add_argument("--recovery-key-file", type=Path, required=True)
    restore.add_argument("--vault-key-file", type=Path, required=True)
    commands.add_parser("validate-schema")
    return result


def main() -> int:
    args = parser().parse_args()
    if args.command == "backup":
        create_encrypted_backup(
            create_production_vault(), args.output, read_protected_key(args.recovery_key_file)
        )
        print("backup_complete")
    elif args.command == "restore-isolated":
        restore_encrypted_backup(
            args.backup,
            args.target,
            read_protected_key(args.recovery_key_file),
            read_protected_key(args.vault_key_file),
        )
        print("isolated_restore_complete")
    else:
        VaultMigrationManager().validate_current(create_production_vault())
        print("schema_compatible")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
