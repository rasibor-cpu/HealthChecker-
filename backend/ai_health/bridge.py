"""
AI Health Bridge — preview / confirm / history for AI-extracted records (HC-202).

Connectors never write to storage. Confirmed imports always go through ImportPipeline:
Validation → Classification → Duplicate Detection → Timeline → Trends →
Executive Dashboard consumers → Doctor Visit → Audit.
"""

from __future__ import annotations

import base64
import json
from typing import Any
from uuid import uuid4

from backend.ai_health.connectors.base import list_connectors, resolve_connector
from backend.health_vault.category_classifier import classify_health_record
from backend.health_vault.doctor_visit import DoctorVisitMode
from backend.health_vault.import_pipeline import ImportPipeline
from backend.health_vault.models import utc_now
from backend.health_vault.timeline import build_timeline
from backend.health_vault.trend_engine import TrendEngine
from backend.health_vault.vault_store import VaultStore

DISCLAIMER = (
    "AI Health Bridge is observational decision support only. "
    "It does not diagnose or prescribe. All imports require explicit user confirmation "
    "and pass through the canonical Health Vault ImportPipeline."
)


def _pipeline_content_bytes(rec: dict[str, Any]) -> bytes:
    """Stable import content bytes for hashing — excludes ephemeral record/linkage IDs."""
    material = {
        "filename": rec.get("filename") or rec.get("original_filename"),
        "document_type": rec.get("document_type"),
        "measured_at": rec.get("measured_at"),
        "measurements": rec.get("extracted_measurements"),
        "interpretation": rec.get("interpretation"),
        "source_system": rec.get("source_system"),
    }
    return json.dumps(material, sort_keys=True, default=str).encode("utf-8")


def _pipeline_content_sha(rec: dict[str, Any]) -> str:
    return VaultStore.sha256_bytes(_pipeline_content_bytes(rec))


class AIHealthBridge:
    """Orchestrates AI connector normalization and confirmed ImportPipeline runs."""

    def __init__(self, store: VaultStore | None = None, pipeline: ImportPipeline | None = None) -> None:
        self.store = store or VaultStore()
        self.pipeline = pipeline or ImportPipeline(store=self.store)

    def available_providers(self) -> list[dict[str, str]]:
        return list_connectors()

    def preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Normalize + summarize without writing health records.

        Requires no confirmation yet. Persists a short-lived preview ticket only
        (metadata + normalized records) so confirm can require explicit consent.
        """
        connector = resolve_connector(payload or {})
        normalized = connector.normalize_payload(payload or {})
        records = list(normalized.get("records") or [])

        categories: dict[str, int] = {}
        dates: list[str] = []
        duplicate_estimate = 0
        preview_records: list[dict[str, Any]] = []

        for rec in records:
            meas = rec.get("extracted_measurements") or []
            classification = classify_health_record(
                document_type=rec.get("document_type"),
                filename=rec.get("filename"),
                source_system=rec.get("source_system"),
                measurements=meas,
                ocr_text=rec.get("text"),
            )
            cat = classification.get("primary_category") or "other"
            categories[cat] = categories.get(cat, 0) + 1
            measured = rec.get("measured_at") or rec.get("report_date")
            if measured:
                dates.append(str(measured)[:10])

            sha = _pipeline_content_sha(rec)
            is_dup = False
            if sha:
                existing = [
                    d
                    for d in self.store.list_documents()
                    if d.get("sha256") == sha and not d.get("duplicate_of")
                ]
                if existing:
                    is_dup = True
                    duplicate_estimate += 1

            preview_records.append(
                {
                    "record_id": rec.get("record_id"),
                    "filename": rec.get("filename"),
                    "category": cat,
                    "classification_confidence": classification.get("classification_confidence"),
                    "measured_at": rec.get("measured_at"),
                    "measurement_count": len(meas),
                    "measurements_preview": [
                        {
                            "metric": (m.get("metric") if isinstance(m, dict) else getattr(m, "metric", None)),
                            "value": (m.get("value") if isinstance(m, dict) else getattr(m, "value", None)),
                            "units": (m.get("units") if isinstance(m, dict) else getattr(m, "units", None)),
                        }
                        for m in meas[:12]
                        if isinstance(m, dict) or hasattr(m, "metric")
                    ],
                    "confidence": rec.get("confidence"),
                    "provenance": rec.get("provenance"),
                    "review_flags": list(rec.get("review_flags") or []),
                    "possible_duplicate": is_dup,
                    "linkage": rec.get("linkage"),
                }
            )

        date_range = None
        if dates:
            ordered = sorted(dates)
            date_range = {"earliest": ordered[0], "latest": ordered[-1]}

        preview_id = str(uuid4())
        ticket = {
            "preview_id": preview_id,
            "created_at": utc_now(),
            "provider_id": normalized.get("provider_id"),
            "provider_version": normalized.get("provider_version"),
            "parser_version": normalized.get("parser_version"),
            "conversation": normalized.get("conversation") or {},
            "records": records,
            "status": "awaiting_confirmation",
        }
        self.store.save_ai_import_preview(ticket)

        n = len(records)
        provider_label = connector.display_name
        message = f"{provider_label} has prepared {n} health record{'s' if n != 1 else ''}.\n\nImport into HealthChecker+?"

        return {
            "ok": True,
            "preview_id": preview_id,
            "provider_id": normalized.get("provider_id"),
            "provider_version": normalized.get("provider_version"),
            "message": message,
            "record_count": n,
            "categories": categories,
            "date_range": date_range,
            "duplicate_estimate": duplicate_estimate,
            "records": preview_records,
            "conversation": normalized.get("conversation") or {},
            "requires_confirmation": True,
            "disclaimer": DISCLAIMER,
        }

    def confirm(self, body: dict[str, Any]) -> dict[str, Any]:
        """
        Import only after explicit user confirmation.

        Rejects missing/false confirmation. Never silent-writes.
        """
        req = dict(body or {})
        confirmed = req.get("confirmed") or req.get("user_confirmed") or req.get("confirm")
        if confirmed is not True and str(confirmed).lower() not in {"true", "1", "yes"}:
            return {
                "ok": False,
                "status": "rejected",
                "errors": ["explicit_user_confirmation_required"],
                "imported": 0,
                "duplicates": 0,
                "failed": 0,
                "disclaimer": DISCLAIMER,
            }

        preview_id = req.get("preview_id")
        ticket = self.store.get_ai_import_preview(preview_id) if preview_id else None

        if ticket is None:
            # Allow one-shot confirm with full payload only when confirmed=true
            if not (req.get("records") or req.get("health_records") or req.get("items")):
                return {
                    "ok": False,
                    "status": "rejected",
                    "errors": ["preview_id_or_payload_required"],
                    "imported": 0,
                    "duplicates": 0,
                    "failed": 0,
                    "disclaimer": DISCLAIMER,
                }
            connector = resolve_connector(req)
            normalized = connector.normalize_payload(req)
            records = list(normalized.get("records") or [])
            conversation = normalized.get("conversation") or {}
            provider_id = normalized.get("provider_id")
            provider_version = normalized.get("provider_version")
            parser_version = normalized.get("parser_version")
        else:
            if ticket.get("status") not in {None, "awaiting_confirmation"}:
                return {
                    "ok": False,
                    "status": "rejected",
                    "errors": ["preview_already_consumed"],
                    "imported": 0,
                    "duplicates": 0,
                    "failed": 0,
                    "disclaimer": DISCLAIMER,
                }
            records = list(ticket.get("records") or [])
            conversation = dict(ticket.get("conversation") or {})
            provider_id = ticket.get("provider_id")
            provider_version = ticket.get("provider_version")
            parser_version = ticket.get("parser_version")

        if not records:
            return {
                "ok": False,
                "status": "rejected",
                "errors": ["no_records_to_import"],
                "imported": 0,
                "duplicates": 0,
                "failed": 0,
                "disclaimer": DISCLAIMER,
            }

        confirmation_timestamp = utc_now()
        batch_id = req.get("batch_id") or f"ai-{provider_id}-{uuid4().hex[:10]}"
        results: list[dict[str, Any]] = []
        imported = duplicates = failed = grouped = 0
        warnings: list[str] = []
        confidences: list[float] = []
        document_ids: list[str] = []

        for rec in records:
            pipeline_req = self._to_pipeline_request(rec, batch_id=batch_id, conversation=conversation)
            try:
                result = self.pipeline.run(pipeline_req)
            except Exception as exc:
                failed += 1
                results.append(
                    {
                        "ok": False,
                        "status": "failed",
                        "record_id": rec.get("record_id"),
                        "errors": [type(exc).__name__],
                    }
                )
                continue

            if result.get("duplicate"):
                duplicates += 1
                status = "duplicate"
            elif result.get("ok"):
                imported += 1
                status = "imported"
                doc = result.get("document") or {}
                if doc.get("id"):
                    document_ids.append(doc["id"])
                if doc.get("group_id"):
                    grouped += 1
            else:
                failed += 1
                status = "failed"

            if result.get("confidence") is not None:
                try:
                    conf_val = result["confidence"]
                    if isinstance(conf_val, dict):
                        conf_val = conf_val.get("overall_confidence") or conf_val.get("overall")
                    confidences.append(float(conf_val))
                except (TypeError, ValueError):
                    pass
            elif rec.get("confidence") is not None:
                try:
                    confidences.append(float(rec["confidence"]))
                except (TypeError, ValueError):
                    pass

            for w in result.get("warnings") or []:
                warnings.append(str(w))

            # Linkage: stamp AI metadata onto import result (no private paths)
            linkage = dict(rec.get("linkage") or {})
            linkage["document_id"] = (result.get("document") or {}).get("id")
            linkage["timeline"] = True
            linkage["trends"] = True
            linkage["executive_dashboard"] = True
            linkage["doctor_visit"] = True

            results.append(
                {
                    "ok": bool(result.get("ok")),
                    "status": status,
                    "record_id": rec.get("record_id"),
                    "document_id": (result.get("document") or {}).get("id"),
                    "duplicate": bool(result.get("duplicate")),
                    "warnings": list(result.get("warnings") or []),
                    "errors": list(result.get("errors") or []),
                    "linkage": linkage,
                }
            )

        # Refresh downstream views (same post-pipeline consumers)
        timeline = build_timeline(self.store)
        trends = TrendEngine(self.store).recompute()
        doctor = DoctorVisitMode(self.store).generate()

        avg_confidence = None
        if confidences:
            avg_confidence = round(sum(confidences) / len(confidences), 4)

        audit = {
            "kind": "ai_health_import",
            "batch_id": batch_id,
            "preview_id": preview_id,
            "ai_provider": provider_id,
            "provider_version": provider_version,
            "parser_version": parser_version,
            "import_time": confirmation_timestamp,
            "user_confirmation": True,
            "confirmation_timestamp": confirmation_timestamp,
            "imported_count": imported,
            "duplicates": duplicates,
            "failed": failed,
            "warnings": warnings[:50],
            "confidence": avg_confidence,
            "conversation": {
                k: conversation.get(k)
                for k in (
                    "conversation_id",
                    "message_id",
                    "message_timestamp",
                    "ai_provider",
                    "parser_version",
                    "model",
                )
                if conversation.get(k) is not None
            },
            "document_ids": document_ids,
            "record_count": len(records),
        }
        self.store.record_ai_import_audit(audit)
        if preview_id:
            self.store.consume_ai_import_preview(preview_id)

        ok = failed == 0 and (imported > 0 or duplicates > 0)
        partial = imported > 0 and failed > 0
        return {
            "ok": ok or partial,
            "partial_success": partial,
            "status": "imported" if ok else ("partial" if partial else "failed"),
            "batch_id": batch_id,
            "provider_id": provider_id,
            "imported": imported,
            "duplicates": duplicates,
            "failed": failed,
            "grouped_reports": grouped,
            "updated_trends": True,
            "dashboard_refreshed": True,
            "doctor_visit_updated": True,
            "timeline_entries": len(timeline),
            "trend_metrics": len(trends or {}),
            "doctor_visit_title": doctor.get("title"),
            "results": results,
            "warnings": warnings[:50],
            "confidence": avg_confidence,
            "confirmation_timestamp": confirmation_timestamp,
            "audit": {
                "ai_provider": provider_id,
                "import_time": confirmation_timestamp,
                "user_confirmation": True,
                "imported_count": imported,
                "duplicates": duplicates,
                "warnings": warnings[:20],
                "confidence": avg_confidence,
            },
            "disclaimer": DISCLAIMER,
        }

    def import_history(self, limit: int = 50) -> dict[str, Any]:
        entries = self.store.list_ai_import_audits()
        entries = list(reversed(entries))[: max(1, min(int(limit or 50), 200))]
        return {
            "ok": True,
            "entries": entries,
            "providers": self.available_providers(),
            "disclaimer": DISCLAIMER,
        }

    def _to_pipeline_request(
        self,
        rec: dict[str, Any],
        *,
        batch_id: str,
        conversation: dict[str, Any],
    ) -> dict[str, Any]:
        content = None
        if rec.get("content") is not None:
            content = rec.get("content")
        elif rec.get("content_base64"):
            try:
                content = base64.b64decode(rec["content_base64"])
            except Exception:
                content = None
        if content is None:
            content = _pipeline_content_bytes(rec)

        tags = list(rec.get("tags") or [])
        if conversation.get("conversation_id"):
            ct = f"conversation_id:{conversation['conversation_id']}"
            if ct not in tags:
                tags.append(ct)

        return {
            "content": content,
            "filename": rec.get("filename") or rec.get("original_filename") or "ai_record.json",
            "mime_type": rec.get("mime_type") or "application/json",
            "document_type": rec.get("document_type"),
            "source_system": rec.get("source_system") or "chatgpt",
            "acquisition_method": "external_ai",
            "ai_version": rec.get("ai_version"),
            "measured_at": rec.get("measured_at"),
            "report_date": rec.get("report_date"),
            "interpretation": rec.get("interpretation"),
            "provenance": rec.get("provenance") or "imported_json",
            "confidence": rec.get("confidence"),
            "extracted_measurements": rec.get("extracted_measurements") or [],
            "tags": tags,
            "sha256": rec.get("sha256"),
            "text": rec.get("text"),
            "json": rec.get("json"),
            "patient_id": rec.get("patient_id") or "default-patient",
            "batch_id": batch_id,
            "group_id": rec.get("group_id"),
            "group_title": rec.get("group_title"),
            "sequence_number": rec.get("sequence_number"),
            "page_number": rec.get("page_number"),
        }
