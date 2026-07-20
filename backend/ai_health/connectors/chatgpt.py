"""ChatGPT Connector V1 — normalizes ChatGPT-extracted health records (no vault writes)."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import uuid4

from backend.ai_health.connectors.base import AIConnector, register_connector
from backend.health_vault.models import utc_now


PARSER_VERSION = "chatgpt_connector_v1"


def _strip_conversation_text(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Keep conversation metadata only — never persist chat body unless explicitly imported."""
    src = dict(meta or {})
    out: dict[str, Any] = {}
    for key in (
        "conversation_id",
        "message_id",
        "message_timestamp",
        "ai_provider",
        "parser_version",
        "model",
        "thread_id",
    ):
        if src.get(key) is not None:
            out[key] = src[key]
    # Explicit opt-in only
    if src.get("store_conversation_text") is True and src.get("conversation_text"):
        out["conversation_text"] = src["conversation_text"]
        out["conversation_text_imported"] = True
    return out


def _fingerprint_record(record: dict[str, Any]) -> str:
    """Stable content fingerprint when no attachment bytes are present."""
    material = {
        "filename": record.get("original_filename") or record.get("filename"),
        "document_type": record.get("document_type"),
        "measured_at": record.get("measured_at"),
        "measurements": record.get("extracted_measurements") or record.get("measurements") or [],
        "interpretation": record.get("interpretation"),
        "source_system": record.get("source_system"),
    }
    blob = json.dumps(material, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class ChatGPTConnector(AIConnector):
    """Connector V1 for ChatGPT structured health-record payloads."""

    provider_id = "chatgpt"
    display_name = "ChatGPT"
    version = "1.0.0"

    def supports(self, payload: dict[str, Any]) -> bool:
        pid = str(
            (payload or {}).get("provider_id")
            or (payload or {}).get("provider")
            or (payload or {}).get("ai_provider")
            or "chatgpt"
        ).lower()
        return pid in {"chatgpt", "openai", "gpt", "chatgpt_connector"}

    def normalize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw = dict(payload or {})
        conversation = _strip_conversation_text(
            raw.get("conversation") or raw.get("chat_metadata") or {}
        )
        conversation.setdefault("ai_provider", self.provider_id)
        conversation.setdefault("parser_version", PARSER_VERSION)
        if not conversation.get("message_timestamp"):
            conversation["message_timestamp"] = raw.get("message_timestamp") or utc_now()

        records_in = raw.get("records") or raw.get("health_records") or raw.get("items") or []
        if not records_in and (
            raw.get("extracted_measurements") or raw.get("measurements") or raw.get("document")
        ):
            records_in = [raw]

        if not isinstance(records_in, list) or not records_in:
            raise ValueError("chatgpt_payload_requires_records")

        records: list[dict[str, Any]] = []
        for idx, item in enumerate(records_in):
            if not isinstance(item, dict):
                raise ValueError(f"record_{idx}_must_be_object")
            records.append(self._normalize_record(item, idx, conversation))

        return {
            "provider_id": self.provider_id,
            "provider_version": self.version,
            "parser_version": PARSER_VERSION,
            "conversation": conversation,
            "records": records,
            "record_count": len(records),
            "normalized_at": utc_now(),
        }

    def _normalize_record(
        self, item: dict[str, Any], idx: int, conversation: dict[str, Any]
    ) -> dict[str, Any]:
        doc = dict(item.get("document") or {})
        filename = (
            item.get("original_filename")
            or item.get("filename")
            or doc.get("original_filename")
            or f"chatgpt_record_{idx + 1}.json"
        )
        measurements = (
            item.get("extracted_measurements")
            or item.get("measurements")
            or doc.get("extracted_measurements")
            or []
        )
        if not isinstance(measurements, list):
            raise ValueError(f"record_{idx}_measurements_must_be_list")

        confidence = item.get("confidence")
        if confidence is None:
            confidence = doc.get("confidence")
        try:
            confidence_f = float(confidence) if confidence is not None else 0.7
        except (TypeError, ValueError):
            confidence_f = 0.5

        attachments = item.get("attachments") or []
        if not isinstance(attachments, list):
            attachments = []

        content = item.get("content")
        content_b64 = item.get("content_base64")
        sha256 = item.get("sha256") or doc.get("sha256")
        if not sha256:
            sha256 = _fingerprint_record(
                {
                    "original_filename": filename,
                    "document_type": item.get("document_type") or doc.get("document_type"),
                    "measured_at": item.get("measured_at") or doc.get("measured_at"),
                    "extracted_measurements": measurements,
                    "interpretation": item.get("interpretation") or doc.get("interpretation"),
                    "source_system": item.get("source_system") or "chatgpt",
                }
            )

        record_id = item.get("record_id") or str(uuid4())
        linkage = {
            "ai_record_id": record_id,
            "conversation_id": conversation.get("conversation_id"),
            "message_id": conversation.get("message_id"),
            "attachment_ids": [
                a.get("id") or a.get("attachment_id")
                for a in attachments
                if isinstance(a, dict) and (a.get("id") or a.get("attachment_id"))
            ],
            "provider_id": self.provider_id,
            "parser_version": PARSER_VERSION,
        }

        review_flags: list[str] = []
        if confidence_f < 0.6:
            review_flags.append("low_confidence")
        if item.get("requires_review") or doc.get("requires_review"):
            review_flags.append("requires_review")
        if not measurements:
            review_flags.append("no_measurements")

        return {
            "record_id": record_id,
            "filename": filename,
            "original_filename": filename,
            "mime_type": item.get("mime_type") or doc.get("mime_type") or "application/json",
            "document_type": item.get("document_type") or doc.get("document_type"),
            "source_system": item.get("source_system") or doc.get("source_system") or "chatgpt",
            "acquisition_method": "external_ai",
            "ai_version": f"{self.provider_id}@{self.version}",
            "measured_at": item.get("measured_at") or doc.get("measured_at"),
            "report_date": item.get("report_date") or doc.get("report_date"),
            "interpretation": item.get("interpretation") or doc.get("interpretation"),
            "provenance": item.get("provenance") or doc.get("provenance") or "imported_json",
            "confidence": confidence_f,
            "extracted_measurements": measurements,
            "attachments": attachments,
            "tags": list(item.get("tags") or doc.get("tags") or []) + ["ai_import:chatgpt"],
            "sha256": sha256,
            "content": content,
            "content_base64": content_b64,
            "text": item.get("text"),
            "json": item.get("json") or item.get("data_json"),
            "linkage": linkage,
            "review_flags": review_flags,
            "patient_id": item.get("patient_id") or doc.get("patient_id") or "default-patient",
        }


# Auto-register on import
register_connector(ChatGPTConnector())
