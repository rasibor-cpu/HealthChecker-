"""
HC-301 — Import two recent confirmed health records into the vault (script only).

NEVER run automatically on app startup. Invoke manually:

  python scripts/import_recent_hc301_records.py
  python scripts/import_recent_hc301_records.py --dry-run

Idempotent: skips when matching patient_id + metric + measured_at + value + source_system
already exist. Timestamps are stored as ISO Z (converted from America/New_York offsets).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.health_vault.import_pipeline import ImportPipeline  # noqa: E402
from backend.health_vault.import_service import ImportService  # noqa: E402
from backend.health_vault.metric_normalization import normalize_measurement  # noqa: E402
from backend.health_vault.vault_store import VaultStore  # noqa: E402

DEFAULT_PATIENT_ID = "default-patient"


def to_iso_z(local_iso: str) -> str:
    """Convert offset-aware ISO (America/New_York wall time) to UTC Z."""
    dt = datetime.fromisoformat(local_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _metric_fingerprint(
    patient_id: str,
    metric: str,
    value: Any,
    measured_at: str,
    source_system: str,
) -> str:
    return f"{patient_id}|{metric}|{value}|{measured_at}|{source_system}"


def _canonical_fp(
    patient_id: str,
    metric: str,
    value: Any,
    measured_at: str,
    source_system: str,
) -> str:
    norm = normalize_measurement({"metric": metric, "value": value, "units": None})
    return _metric_fingerprint(
        patient_id,
        str(norm.get("metric") or metric),
        norm.get("value", value),
        measured_at,
        source_system,
    )


def already_present(
    store: VaultStore,
    measured_at: str,
    metrics: list[dict[str, Any]],
    *,
    patient_id: str,
    source_system: str,
) -> bool:
    """Skip when all metrics match patient_id + metric + measured_at + value + source_system."""
    docs = {d.get("id"): d for d in store.list_documents()}
    existing: set[str] = set()
    for m in store.list_measurements():
        doc = docs.get(m.get("document_id")) or {}
        pid = str(m.get("patient_id") or doc.get("patient_id") or DEFAULT_PATIENT_ID)
        src = str(doc.get("source_system") or m.get("source_system") or "")
        existing.add(
            _metric_fingerprint(
                pid,
                str(m.get("metric")),
                m.get("value"),
                str(m.get("measured_at") or ""),
                src,
            )
        )
    for m in metrics:
        fp = _canonical_fp(
            patient_id,
            str(m["metric"]),
            m["value"],
            measured_at,
            source_system,
        )
        if fp not in existing:
            return False
    return True


def import_record(
    service: ImportService,
    store: VaultStore,
    *,
    label: str,
    request: dict[str, Any],
    dry_run: bool = False,
) -> str:
    measured_at = str(request["measured_at"])
    metrics = list(request.get("extracted_measurements") or [])
    patient_id = str(request.get("patient_id") or DEFAULT_PATIENT_ID)
    source_system = str(request.get("source_system") or "")

    if dry_run:
        print(f"{label}: DRY-RUN would import")
        print(json.dumps(_dry_run_payload(request), indent=2, default=str))
        return "dry-run"

    if already_present(
        store,
        measured_at,
        metrics,
        patient_id=patient_id,
        source_system=source_system,
    ):
        print(f"{label}: skipped (duplicate fingerprint patient|metric|value|measured_at|source)")
        return "skipped"

    result = service.import_health_record(request)
    if result.get("duplicate"):
        print(f"{label}: skipped (pipeline duplicate)")
        return "skipped"
    if not result.get("ok"):
        print(f"{label}: failed — {result.get('errors') or result}")
        return "failed"
    print(f"{label}: imported (document_id={ (result.get('document') or {}).get('id') })")
    return "imported"


def _dry_run_payload(request: dict[str, Any]) -> dict[str, Any]:
    """Serializable preview of what ImportService would receive (no binary content dump)."""
    content = request.get("content")
    content_preview: Any
    if isinstance(content, (bytes, bytearray)):
        try:
            content_preview = json.loads(bytes(content).decode("utf-8"))
        except Exception:
            content_preview = f"<{len(content)} bytes>"
    else:
        content_preview = content
    out = {k: v for k, v in request.items() if k != "content"}
    out["content"] = content_preview
    return out


def build_records() -> list[tuple[str, dict[str, Any]]]:
    # Jul 25 2026 23:48 America/New_York (EDT, UTC-4) → Z
    bp_at = to_iso_z("2026-07-25T23:48:00-04:00")
    # Jul 26 2026 05:08 America/New_York (EDT, UTC-4) → Z
    glucose_at = to_iso_z("2026-07-26T05:08:00-04:00")

    meal_notes = (
        "Meal notes (non-fasting / post-meal): bread, jollof rice, fried eggs, oatmeal cookies."
    )

    bp_body = {
        "document_type": "blood_pressure_screenshot",
        "source_system": "Samsung Health Monitor",
        "measured_at": bp_at,
        "systolic": 127,
        "diastolic": 84,
        "pulse": 65,
        "units": "mmHg",
    }
    glucose_body = {
        "document_type": "glucose_meter_screenshot",
        "source_system": "Contour Next GEN",
        "measured_at": glucose_at,
        "glucose": 183,
        "units": "mg/dL",
        "meal_context": "non_fasting",
        "context": "post_meal",
        "meal_notes": meal_notes,
        "interpretation": (
            f"Non-fasting Contour Next GEN fingerstick glucose after meal. {meal_notes} "
            "Provenance: meter photograph / manual confirmation."
        ),
    }

    record_bp = (
        "Record 1 (BP 127/84, pulse 65)",
        {
            "patient_id": DEFAULT_PATIENT_ID,
            "content": json.dumps(bp_body).encode("utf-8"),
            "filename": "samsung_bp_2026-07-25.json",
            "mime_type": "application/json",
            "document_type": "blood_pressure_screenshot",
            "source_system": "Samsung Health Monitor",
            "acquisition_method": "wearable_screenshot",
            "provenance": "wearable_screenshot",
            "measured_at": bp_at,
            "tags": ["device:samsung_health_monitor", "hc301"],
            "extracted_measurements": [
                {"metric": "systolic_bp", "value": 127, "units": "mmHg", "measured_at": bp_at},
                {"metric": "diastolic_bp", "value": 84, "units": "mmHg", "measured_at": bp_at},
                {"metric": "resting_hr", "value": 65, "units": "bpm", "measured_at": bp_at},
            ],
            "interpretation": (
                "Samsung Health Monitor blood pressure 127/84 mmHg, pulse 65 bpm "
                "(2026-07-25 23:48 America/New_York -> UTC Z). User-initiated reading."
            ),
            "confidence": 0.9,
        },
    )

    record_glucose = (
        "Record 2 (glucose 183 mg/dL, Contour Next GEN)",
        {
            "patient_id": DEFAULT_PATIENT_ID,
            "content": json.dumps(glucose_body).encode("utf-8"),
            "filename": "contour_glucose_2026-07-26.json",
            "mime_type": "application/json",
            "document_type": "glucose_meter_screenshot",
            "source_system": "Contour Next GEN",
            "acquisition_method": "meter_photograph_manual_confirmation",
            "provenance": "meter photograph / manual confirmation",
            "measured_at": glucose_at,
            "tags": [
                "device:contour_next_gen",
                "meal:non_fasting",
                "context:post_meal",
                "hc301",
            ],
            "extracted_measurements": [
                {
                    "metric": "glucose",
                    "value": 183,
                    "units": "mg/dL",
                    "measured_at": glucose_at,
                    "context": "post_meal",
                    "meal_context": "non_fasting",
                    "meal_notes": meal_notes,
                }
            ],
            "interpretation": glucose_body["interpretation"],
            "confidence": 0.9,
        },
    )
    return [record_bp, record_glucose]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "HC-301 manual import of two recent confirmed health records. "
            "Never runs on app startup — invoke this script explicitly."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print exactly what would be imported without writing to the vault.",
    )
    args = parser.parse_args(argv)

    store = VaultStore()
    # ImportService wraps ImportPipeline (canonical path); both share the same VaultStore.
    service = ImportService(store=store)
    assert isinstance(service.pipeline, ImportPipeline)

    mode = "DRY-RUN (no writes)" if args.dry_run else "LIVE import"
    print(f"HC-301 recent records import - {mode} (manual script - not startup)")
    print(f"Vault: {store.root}")
    print(f"BP measured_at (Z): {to_iso_z('2026-07-25T23:48:00-04:00')}")
    print(f"Glucose measured_at (Z): {to_iso_z('2026-07-26T05:08:00-04:00')}")

    results = []
    for label, req in build_records():
        results.append(
            import_record(service, store, label=label, request=req, dry_run=args.dry_run)
        )

    imported = results.count("imported")
    skipped = results.count("skipped")
    failed = results.count("failed")
    dry = results.count("dry-run")
    print(f"Done. imported={imported} skipped={skipped} failed={failed} dry-run={dry}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
