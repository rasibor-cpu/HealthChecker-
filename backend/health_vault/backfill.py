"""
HC-201F — Idempotent private health-record backfill.

Loads structured records from a local JSON file (never committed with real PII)
through the canonical ImportPipeline. Safe to re-run: duplicate content hashes
and soft document matches produce zero new inserts.

Usage:
  python -m backend.health_vault.backfill --input private_imports/robert_health_backfill.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from backend.health_vault.doctor_visit import DoctorVisitMode
from backend.health_vault.event_bus import EventBus
from backend.health_vault.import_pipeline import ImportPipeline
from backend.health_vault.models import (
    PROVENANCE_CONFIDENCE,
    PROVENANCE_VALUES,
    utc_now,
)
from backend.health_vault.parser_registry import ParserRegistry
from backend.health_vault.parsers import register_builtin_parsers
from backend.health_vault.timeline import build_timeline
from backend.health_vault.vault_store import VaultStore

SCHEMA_VERSION = "hc.health_vault.backfill.v1"

REQUIRED_TOP_KEYS = ("schema_version", "records")


class BackfillValidationError(ValueError):
    """Raised when the backfill payload fails schema checks."""


def validate_backfill_payload(payload: dict[str, Any]) -> list[str]:
    """Return a list of validation errors (empty = OK). Does not raise."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["payload must be a JSON object"]
    for key in REQUIRED_TOP_KEYS:
        if key not in payload:
            errors.append(f"missing required key: {key}")
    if payload.get("schema_version") not in (SCHEMA_VERSION, "hc.health_vault.backfill.v1"):
        errors.append(f"unsupported schema_version: {payload.get('schema_version')!r}")
    records = payload.get("records")
    if not isinstance(records, list):
        errors.append("records must be a list")
        return errors
    seen_ids: set[str] = set()
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            errors.append(f"records[{i}] must be an object")
            continue
        rid = rec.get("record_id")
        if not rid or not isinstance(rid, str):
            errors.append(f"records[{i}] missing record_id")
        elif rid in seen_ids:
            errors.append(f"duplicate record_id: {rid}")
        else:
            seen_ids.add(rid)
        prov = rec.get("provenance")
        if prov not in PROVENANCE_VALUES:
            errors.append(
                f"records[{i}] provenance must be one of {PROVENANCE_VALUES}, got {prov!r}"
            )
        if not isinstance(rec.get("measurements"), list):
            errors.append(f"records[{i}] measurements must be a list")
        else:
            for j, m in enumerate(rec.get("measurements") or []):
                if not isinstance(m, dict) or "metric" not in m or "value" not in m:
                    errors.append(f"records[{i}].measurements[{j}] needs metric and value")
    profile = payload.get("profile") or {}
    if profile and not isinstance(profile, dict):
        errors.append("profile must be an object when present")
    return errors


def _stable_content(record: dict[str, Any]) -> bytes:
    """Deterministic payload bytes so re-runs share the same SHA-256."""
    body = {
        "record_id": record["record_id"],
        "document_type": record.get("document_type"),
        "provenance": record.get("provenance"),
        "source_system": record.get("source_system"),
        "measured_at": record.get("measured_at"),
        "original_filename": record.get("original_filename"),
        "interpretation": record.get("interpretation"),
        "context_note": record.get("context_note"),
        "measurements": record.get("measurements") or [],
        "device": record.get("device"),
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _format_medication(entry: Any) -> str:
    if isinstance(entry, str):
        return entry.strip()
    if not isinstance(entry, dict):
        return str(entry)
    name = str(entry.get("name") or entry.get("medication") or "").strip()
    dose = str(entry.get("dose") or "").strip()
    status = str(entry.get("status") or "uncertain").strip().lower()
    notes = str(entry.get("notes") or "").strip()
    parts = [p for p in (name, dose) if p]
    line = " ".join(parts) if parts else "unknown medication"
    if status:
        line = f"{line} [status:{status}]"
    if notes:
        line = f"{line} — {notes}"
    return line


def apply_profile(store: VaultStore, payload: dict[str, Any]) -> dict[str, Any]:
    """Merge diagnoses/medications into vault profile (idempotent set-union)."""
    profile_in = payload.get("profile") or {}
    patient_id = str((payload.get("patient") or {}).get("patient_id") or "default-patient")
    current = store.get_profile(patient_id=patient_id) or {"diagnoses": [], "medications": []}
    diagnoses = list(current.get("diagnoses") or [])
    medications = list(current.get("medications") or [])

    for dx in profile_in.get("diagnoses") or []:
        text = dx.strip() if isinstance(dx, str) else str(dx.get("name") or dx)
        if text and text not in diagnoses:
            diagnoses.append(text)

    for med in profile_in.get("medications") or []:
        line = _format_medication(med)
        if line and line not in medications:
            medications.append(line)

    patient = payload.get("patient") or {}
    partial: dict[str, Any] = {
        "diagnoses": diagnoses,
        "medications": medications,
    }
    if patient.get("patient_id"):
        partial["patient_id"] = patient["patient_id"]
    if patient.get("date_of_birth"):
        partial["date_of_birth"] = patient["date_of_birth"]
    # Display name stays local-only in profile; never required for pipeline.
    if patient.get("display_name"):
        partial["display_name"] = patient["display_name"]

    store.update_profile(partial, patient_id=patient_id)
    return store.get_profile(patient_id=patient_id)


def record_to_pipeline_request(record: dict[str, Any], patient_id: str) -> dict[str, Any]:
    provenance = record["provenance"]
    confidence = record.get("confidence")
    if confidence is None:
        confidence = PROVENANCE_CONFIDENCE.get(provenance, 0.7)

    measured_at = record.get("measured_at")
    measurements = []
    for item in record.get("measurements") or []:
        m = dict(item)
        if measured_at and not m.get("measured_at"):
            m["measured_at"] = measured_at
        if m.get("confidence") is None:
            m["confidence"] = confidence
        measurements.append(m)

    tags = list(record.get("tags") or [])
    tags.append("hc201f_backfill")
    tags.append(f"record_id:{record['record_id']}")
    if record.get("device"):
        tags.append(f"device:{record['device']}")

    interpretation = record.get("interpretation") or ""
    context = record.get("context_note")
    if context:
        interpretation = (interpretation + "\n" if interpretation else "") + f"Context: {context}"

    filename = record.get("original_filename") or f"{record['record_id']}.json"
    content = _stable_content(record)

    return {
        "content": content,
        "filename": filename,
        "mime_type": "application/json",
        "document_type": record.get("document_type") or "ai_assisted_import",
        "acquisition_method": "external_ai",
        "source_system": record.get("source_system") or "hc201f_private_backfill",
        "patient_id": patient_id,
        "measured_at": measured_at,
        "extracted_measurements": measurements,
        "interpretation": interpretation or None,
        "confidence": float(confidence),
        "provenance": provenance,
        "tags": tags,
        "ai_version": "hc201f_backfill",
    }


def run_backfill(
    input_path: str | Path,
    *,
    store: VaultStore | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Load, validate, and import all records. Safe to re-run."""
    path = Path(input_path)
    if not path.is_file():
        raise FileNotFoundError(f"Backfill input not found: {path}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_backfill_payload(payload)
    if errors:
        raise BackfillValidationError("; ".join(errors))

    vault = store or VaultStore()
    patient_id = str((payload.get("patient") or {}).get("patient_id") or "default-patient")

    report: dict[str, Any] = {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "input": str(path),
        "started_at": utc_now(),
        "dry_run": dry_run,
        "patient_id": patient_id,
        "imported": 0,
        "duplicates": 0,
        "failed": 0,
        "measurement_count_imported": 0,
        "records": [],
        "profile": None,
        "final_document_count": 0,
        "final_measurement_count": 0,
    }

    if dry_run:
        report["records"] = [
            {"record_id": r.get("record_id"), "status": "dry_run"} for r in payload["records"]
        ]
        report["finished_at"] = utc_now()
        return report

    apply_profile(vault, payload)

    reg = ParserRegistry()
    register_builtin_parsers(reg)
    pipeline = ImportPipeline(store=vault, registry=reg, bus=EventBus())

    for record in payload["records"]:
        req = record_to_pipeline_request(record, patient_id)
        result = pipeline.run(req)
        entry = {
            "record_id": record["record_id"],
            "ok": bool(result.get("ok")),
            "duplicate": bool(result.get("duplicate")),
            "status": result.get("status"),
            "document_id": (result.get("document") or {}).get("id"),
            "measurement_count": len(result.get("measurements") or []),
            "warnings": result.get("warnings") or [],
            "errors": result.get("errors") or [],
            "sha256": result.get("sha256"),
            "provenance": record.get("provenance"),
        }
        report["records"].append(entry)
        if result.get("duplicate"):
            report["duplicates"] += 1
        elif result.get("ok"):
            report["imported"] += 1
            report["measurement_count_imported"] += len(result.get("measurements") or [])
        else:
            report["failed"] += 1
            report["ok"] = False

    report["profile"] = vault.get_profile(patient_id=patient_id)
    report["final_document_count"] = len(vault.list_documents())
    report["final_measurement_count"] = len(vault.list_measurements())
    report["timeline_entries"] = len(build_timeline(vault))
    report["doctor_visit"] = DoctorVisitMode(vault).generate(patient_id=patient_id)
    report["finished_at"] = utc_now()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="HC-201F private health-record backfill (idempotent)"
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to private backfill JSON (e.g. private_imports/robert_health_backfill.json)",
    )
    parser.add_argument(
        "--vault-root",
        default=None,
        help="Optional vault_storage root (defaults to repository vault_storage/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate only; do not write to the vault",
    )
    parser.add_argument(
        "--report",
        default=None,
        help="Optional path to write the import report JSON (keep under private_imports/)",
    )
    args = parser.parse_args(argv)

    store = VaultStore(root=args.vault_root) if args.vault_root else VaultStore()
    try:
        report = run_backfill(args.input, store=store, dry_run=args.dry_run)
    except (BackfillValidationError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"BACKFILL_FAILED: {exc}", file=sys.stderr)
        return 2

    text = json.dumps(report, indent=2)
    print(text)
    if args.report:
        out = Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")

    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
