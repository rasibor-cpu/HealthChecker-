"""HC-326: fail-closed reconciliation of legacy measurement normalization metadata.

Dry-run is the default. Production writes require --apply and an encrypted
backup that is immediately restore-verified before the authoritative index is
changed. Existing clinical/core fields are invariant.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from uuid import uuid4

from backend.health_vault.metric_normalization import normalize_measurement
from backend.health_vault.production_runtime import create_production_vault
from backend.health_vault.recovery import create_encrypted_backup, restore_encrypted_backup
from backend.health_vault.vault_key_protector import read_protected_key

CORE_FIELDS = (
    "metric", "value", "units", "category", "reference_range", "unit_compatible",
)
BACKFILL_FIELDS = (
    "normalization_version", "observation_class", "original_analyte_name",
    "original_metric", "original_units", "original_value", "semantics_version",
)

class ReconciliationBlocked(RuntimeError):
    pass


def _candidate(row: dict) -> dict:
    normalized = normalize_measurement(row)
    if any(normalized.get(k) != row.get(k) for k in CORE_FIELDS):
        raise ReconciliationBlocked(f"core_field_drift:{row.get('measurement_id')}")
    out = dict(row)
    for key in BACKFILL_FIELDS:
        if out.get(key) in (None, "") and normalized.get(key) not in (None, ""):
            out[key] = normalized[key]
    return out


def reconcile(*, apply: bool, backup: Path | None, recovery_key_file: Path | None) -> dict:
    vault = create_production_vault()
    before_integrity = vault.verify_integrity()
    if not before_integrity.get("ok"):
        raise ReconciliationBlocked("pre_integrity_failed")

    index = vault._read_index()
    rows = list(index.get("measurements") or [])
    candidates = [_candidate(row) for row in rows]
    changed = [i for i, (a, b) in enumerate(zip(rows, candidates)) if a != b]
    field_counts = {k: sum(1 for i in changed if rows[i].get(k) != candidates[i].get(k)) for k in BACKFILL_FIELDS}
    report = {
        "mode": "apply" if apply else "dry_run",
        "measurement_count": len(rows),
        "rows_requiring_reconciliation": len(changed),
        "field_counts": field_counts,
        "pre_integrity": before_integrity,
    }
    if not apply:
        return report
    if not changed:
        report["result"] = "already_reconciled"
        return report
    if backup is None or recovery_key_file is None:
        raise ReconciliationBlocked("apply_requires_backup_and_recovery_key")
    if backup.exists():
        raise ReconciliationBlocked("backup_target_exists")

    recovery_key = read_protected_key(recovery_key_file)
    create_encrypted_backup(vault, backup, recovery_key)
    restore_target = backup.with_name(f".{backup.name}.verify.{uuid4().hex}")
    try:
        restored = restore_encrypted_backup(backup, restore_target, recovery_key, vault.encryption_key)
        if restored._read_index() != index:
            raise ReconciliationBlocked("backup_restore_reconciliation_failed")
        new_index = dict(index)
        new_index["measurements"] = candidates
        vault._audit(new_index, "legacy_measurement_metadata_reconciled", {
            "measurement_count": len(rows), "rows_changed": len(changed), "fields": field_counts,
        })
        vault._write_index(new_index)
        after = vault._read_index()
        if len(after.get("measurements") or []) != len(rows):
            raise ReconciliationBlocked("measurement_count_drift")
        for old, new in zip(rows, after.get("measurements") or []):
            if any(old.get(k) != new.get(k) for k in CORE_FIELDS):
                raise ReconciliationBlocked("post_write_core_field_drift")
        post_integrity = vault.verify_integrity()
        if not post_integrity.get("ok"):
            raise ReconciliationBlocked("post_integrity_failed")
        report["post_integrity"] = post_integrity
        report["result"] = "reconciled"
        return report
    except Exception:
        # Fail closed: restore the authenticated pre-change index if a write occurred.
        try:
            current = vault._read_index()
            if current != index:
                vault._write_index(index)
        finally:
            raise
    finally:
        shutil.rmtree(restore_target, ignore_errors=True)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="Apply after verified encrypted backup; default is dry-run")
    p.add_argument("--backup", type=Path)
    p.add_argument("--recovery-key-file", type=Path)
    p.add_argument("--report", type=Path)
    args = p.parse_args()
    try:
        result = reconcile(apply=args.apply, backup=args.backup, recovery_key_file=args.recovery_key_file)
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
        text = json.dumps(result, indent=2, sort_keys=True)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(text, encoding="utf-8")
        print(text)
        return 1
    result["ok"] = True
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")
    print(text)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
