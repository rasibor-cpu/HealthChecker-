"""HC327 measurement patient-scope reconciliation.

Default mode is read-only dry-run.

A measurement may inherit patient_id only from its owning persisted
document_id. Clinical values, metric names, timestamps, units, categories,
reference ranges and document associations are invariant.

Production apply requires an explicit --apply flag.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from uuid import uuid4

from backend.health_vault.production_runtime import create_production_vault
from backend.health_vault.recovery import (
    create_encrypted_backup,
    restore_encrypted_backup,
)
from backend.health_vault.vault_key_protector import read_protected_key


CLINICAL_INVARIANTS = (
    "measurement_id",
    "document_id",
    "metric",
    "value",
    "units",
    "category",
    "reference_range",
    "measured_at",
)


class PatientScopeReconciliationBlocked(RuntimeError):
    pass


def build_plan(vault):
    data = vault._read_index()

    docs = {
        str(d.get("id")): d
        for d in (data.get("documents") or [])
        if d.get("id")
    }

    measurements = list(data.get("measurements") or [])

    changes = []
    orphaned = []
    conflicts = []

    for i, row in enumerate(measurements):
        document_id = str(row.get("document_id") or "")
        document = docs.get(document_id)

        if not document:
            orphaned.append({
                "measurement_id": row.get("measurement_id"),
                "document_id": document_id,
            })
            continue

        owner = str(document.get("patient_id") or "default-patient")
        current = str(row.get("patient_id") or "default-patient")

        if current == owner:
            continue

        # Only automatically reconcile legacy/default scope.
        if current not in ("", "default-patient"):
            conflicts.append({
                "measurement_id": row.get("measurement_id"),
                "document_id": document_id,
                "measurement_patient_id": current,
                "document_patient_id": owner,
            })
            continue

        if owner == "default-patient":
            continue

        before = {
            key: row.get(key)
            for key in CLINICAL_INVARIANTS
        }

        changes.append({
            "index": i,
            "measurement_id": row.get("measurement_id"),
            "document_id": document_id,
            "from_patient_id": current,
            "to_patient_id": owner,
            "invariants": before,
        })

    return data, changes, orphaned, conflicts


def reconcile(
    *,
    apply: bool,
    backup: Path | None = None,
    recovery_key_file: Path | None = None,
):
    vault = create_production_vault()

    integrity = vault.verify_integrity()
    if not integrity.get("ok"):
        raise PatientScopeReconciliationBlocked(
            "pre_integrity_failed"
        )

    data, changes, orphaned, conflicts = build_plan(vault)

    report = {
        "mode": "apply" if apply else "dry_run",
        "measurement_count": len(data.get("measurements") or []),
        "rows_requiring_patient_scope_reconciliation": len(changes),
        "orphaned_measurements": len(orphaned),
        "conflicting_nondefault_patient_ids": len(conflicts),
        "orphan_examples": orphaned[:10],
        "conflict_examples": conflicts[:10],
        "pre_integrity": integrity,
    }

    # Fail closed if ownership cannot be derived unambiguously.
    if conflicts:
        raise PatientScopeReconciliationBlocked(
            "nondefault_patient_scope_conflicts_detected"
        )

    if not apply:
        report["ok"] = True
        return report

    if not changes:
        report["result"] = "already_reconciled"
        report["ok"] = True
        return report

    if backup is None or recovery_key_file is None:
        raise PatientScopeReconciliationBlocked(
            "apply_requires_backup_and_recovery_key"
        )

    if backup.exists():
        raise PatientScopeReconciliationBlocked(
            "backup_target_exists"
        )

    vault_key = getattr(vault, "_encryption_key", None)
    if not isinstance(vault_key, bytes) or len(vault_key) != 32:
        raise PatientScopeReconciliationBlocked(
            "production_vault_key_unavailable"
        )

    recovery_key = read_protected_key(recovery_key_file)

    # Mandatory encrypted backup BEFORE any index mutation.
    create_encrypted_backup(
        vault,
        backup,
        recovery_key,
    )

    restore_target = backup.with_name(
        f".{backup.name}.verify.{uuid4().hex}"
    )

    before = vault._read_index()

    try:
        # Immediately prove that the backup can actually be restored.
        restored = restore_encrypted_backup(
            backup,
            restore_target,
            recovery_key,
            vault_key,
        )

        restored_index = restored._read_index()

        if restored_index != before:
            raise PatientScopeReconciliationBlocked(
                "backup_restore_verification_failed"
            )

        report["backup_restore_verified"] = True

        after = dict(before)
        rows = [
            dict(x)
            for x in (before.get("measurements") or [])
        ]

        for change in changes:
            i = change["index"]
            row = rows[i]

            for key, expected in change["invariants"].items():
                if row.get(key) != expected:
                    raise PatientScopeReconciliationBlocked(
                        "pre_write_measurement_changed"
                    )

            row["patient_id"] = change["to_patient_id"]

        after["measurements"] = rows

        vault._audit(
            after,
            "measurement_patient_scope_reconciled",
            {
                "measurement_count": len(rows),
                "rows_changed": len(changes),
            },
        )

        vault._write_index(after)

        verify = vault._read_index()

        if len(verify.get("measurements") or []) != len(
            before.get("measurements") or []
        ):
            raise PatientScopeReconciliationBlocked(
                "measurement_count_drift"
            )

        for change in changes:
            row = verify["measurements"][change["index"]]

            if row.get("patient_id") != change["to_patient_id"]:
                raise PatientScopeReconciliationBlocked(
                    "patient_scope_write_failed"
                )

            for key, expected in change["invariants"].items():
                if row.get(key) != expected:
                    raise PatientScopeReconciliationBlocked(
                        f"clinical_field_drift:{key}"
                    )

        post = vault.verify_integrity()

        if not post.get("ok"):
            raise PatientScopeReconciliationBlocked(
                "post_integrity_failed"
            )

        report["post_integrity"] = post
        report["result"] = "reconciled"
        report["rows_changed"] = len(changes)
        report["ok"] = True
        return report

    except Exception:
        current = vault._read_index()

        if current != before:
            vault._write_index(before)

        raise

    finally:
        shutil.rmtree(
            restore_target,
            ignore_errors=True,
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--recovery-key-file", type=Path)
    args = parser.parse_args()

    try:
        report = reconcile(
            apply=args.apply,
            backup=args.backup,
            recovery_key_file=args.recovery_key_file,
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({
            "ok": False,
            "error": str(exc),
        }, indent=2, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
