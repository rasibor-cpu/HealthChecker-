"""
Seed browser Health Vault from a private backfill JSON via a local static page.

Prints a one-shot JS snippet (or writes a temporary HTML helper) that calls
HCImportEngine for each record. Does not commit private data.

Usage:
  python scripts/seed_browser_vault_from_backfill.py private_imports/robert_health_backfill.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.health_vault.backfill import (  # noqa: E402
    apply_profile,
    record_to_pipeline_request,
    validate_backfill_payload,
)


def to_browser_payloads(payload: dict) -> list[dict]:
    patient_id = str((payload.get("patient") or {}).get("patient_id") or "default-patient")
    out: list[dict] = []
    for record in payload.get("records") or []:
        req = record_to_pipeline_request(record, patient_id)
        # Browser engine expects text/document, not raw bytes.
        content = req.pop("content", b"")
        if isinstance(content, bytes):
            document = content.decode("utf-8")
        else:
            document = str(content)
        out.append(
            {
                "document": document,
                "filename": req["filename"],
                "mime_type": req["mime_type"],
                "document_type": req["document_type"],
                "acquisition_method": req["acquisition_method"],
                "source_system": req.get("source_system"),
                "measured_at": req.get("measured_at"),
                "extracted_measurements": req.get("extracted_measurements") or [],
                "interpretation": req.get("interpretation"),
                "confidence": req.get("confidence"),
                "tags": req.get("tags") or [],
                "provenance": req.get("provenance"),
            }
        )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit browser seed payloads from backfill JSON")
    parser.add_argument("input", help="Private backfill JSON path")
    parser.add_argument(
        "--write-json",
        default=None,
        help="Write browser payloads JSON (keep under private_imports/)",
    )
    args = parser.parse_args(argv)

    path = Path(args.input)
    payload = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_backfill_payload(payload)
    if errors:
        print("INVALID: " + "; ".join(errors), file=sys.stderr)
        return 2

    browser = {
        "profile": payload.get("profile") or {},
        "patient": payload.get("patient") or {},
        "imports": to_browser_payloads(payload),
    }
    text = json.dumps(browser, indent=2)
    if args.write_json:
        out = Path(args.write_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"Wrote {out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
