"""
HC-201G — Batch import orchestrator.

Delegates every file to the canonical ImportPipeline. Does not duplicate
parser, validation, storage, trend, or audit logic.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.health_vault.batch_config import BatchImportConfig, get_batch_config
from backend.health_vault.event_bus import EventBus
from backend.health_vault.import_pipeline import ImportPipeline
from backend.health_vault.models import classify_document_type, utc_now
from backend.health_vault.parser_registry import ParserRegistry
from backend.health_vault.parsers import register_builtin_parsers
from backend.health_vault.vault_store import VaultStore

_UNSAFE_NAME_RE = re.compile(r"[^\w.\- ()\[\]]+", re.UNICODE)
_PAGE_RE = re.compile(r"(?:page|pg|p)[_\-\s]?(\d+)", re.IGNORECASE)
_SEQ_RE = re.compile(r"[_\-](\d{1,3})(?:\.[^.]+)?$")


def sanitize_filename(name: str | None) -> str:
    """Basename only; strip path segments and unsafe characters."""
    raw = Path(str(name or "upload.bin")).name.strip() or "upload.bin"
    cleaned = _UNSAFE_NAME_RE.sub("_", raw).strip(" ._")
    return cleaned or "upload.bin"


def _extension(filename: str) -> str:
    return Path(filename).suffix.lower()


def _mime_allowed(mime: str, filename: str, config: BatchImportConfig) -> bool:
    ext = _extension(filename)
    if ext not in config.allowed_extensions:
        return False
    m = (mime or "").lower().split(";")[0].strip()
    if not m or m == "application/octet-stream":
        return True
    return any(m == p or m.startswith(p.rstrip("*")) for p in config.allowed_mime_prefixes)


def suggest_groups(items: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """
    Heuristic grouping for related screenshots/pages.

    Returns map: item_index -> {group_id, sequence_number, page_number, group_title}
    Uncertain / unrelated files get unique group_ids (separate documents, same batch).
    """
    assignments: dict[int, dict[str, Any]] = {}
    buckets: dict[str, list[int]] = {}

    for i, item in enumerate(items):
        filename = sanitize_filename(item.get("filename"))
        doc_type = item.get("document_type") or classify_document_type(
            filename, item.get("mime_type"), item.get("document_type")
        )
        stem = Path(filename).stem.lower()
        # Normalize trailing page/seq tokens for bucket key
        stem_key = _PAGE_RE.sub("", stem)
        stem_key = _SEQ_RE.sub("", stem_key)
        stem_key = re.sub(r"[_\-\s]+$", "", stem_key)
        source = str(item.get("source_system") or "unknown").lower()
        key = f"{doc_type}|{source}|{stem_key}"
        buckets.setdefault(key, []).append(i)

    for key, idxs in buckets.items():
        if len(idxs) < 2:
            # Singleton — own group (do not silently combine with others)
            for i in idxs:
                filename = sanitize_filename(items[i].get("filename"))
                page = _extract_page(filename)
                assignments[i] = {
                    "group_id": str(uuid4()),
                    "sequence_number": 1,
                    "page_number": page,
                    "group_title": None,
                }
            continue

        group_id = str(uuid4())
        # Order by page/seq then filename
        def sort_key(i: int) -> tuple:
            fn = sanitize_filename(items[i].get("filename"))
            page = _extract_page(fn) or 10_000
            seq = _extract_seq(fn) or page
            return (seq, page, fn.lower())

        ordered = sorted(idxs, key=sort_key)
        title_stem = Path(sanitize_filename(items[ordered[0]].get("filename"))).stem
        title = f"Grouped report · {title_stem}"
        for seq, i in enumerate(ordered, start=1):
            fn = sanitize_filename(items[i].get("filename"))
            assignments[i] = {
                "group_id": group_id,
                "sequence_number": seq,
                "page_number": _extract_page(fn) or seq,
                "group_title": title,
            }
    return assignments


def _extract_page(filename: str) -> int | None:
    m = _PAGE_RE.search(filename)
    return int(m.group(1)) if m else None


def _extract_seq(filename: str) -> int | None:
    m = _SEQ_RE.search(Path(filename).stem)
    return int(m.group(1)) if m else None


class BatchImportService:
    """
    Batch Received → validate → batch_id → queue → ImportPipeline per file →
    preserve results → progress → consolidated report.
    """

    def __init__(
        self,
        store: VaultStore | None = None,
        config: BatchImportConfig | None = None,
        pipeline: ImportPipeline | None = None,
    ) -> None:
        self.store = store or VaultStore()
        self.config = config or get_batch_config()
        if pipeline is not None:
            self.pipeline = pipeline
        else:
            reg = ParserRegistry()
            register_builtin_parsers(reg)
            self.pipeline = ImportPipeline(
                store=self.store, registry=reg, bus=EventBus()
            )

    def validate_batch(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Validate count/size/types without importing. Returns structured errors."""
        errors: list[dict[str, Any]] = []
        warnings: list[str] = []
        if not isinstance(items, list) or not items:
            return {
                "ok": False,
                "errors": [{"code": "empty_batch", "message": "Batch contains no files"}],
                "total_bytes": 0,
                "file_count": 0,
            }

        if len(items) > self.config.max_files_per_batch:
            errors.append(
                {
                    "code": "max_files_exceeded",
                    "message": (
                        f"Maximum {self.config.max_files_per_batch} files per batch "
                        f"(received {len(items)})"
                    ),
                }
            )

        total_bytes = 0
        for i, item in enumerate(items):
            filename = sanitize_filename(item.get("filename"))
            content = item.get("content")
            size = (
                len(content)
                if isinstance(content, (bytes, bytearray))
                else int(item.get("size_bytes") or 0)
            )
            total_bytes += size
            mime = str(item.get("mime_type") or "application/octet-stream")

            if size <= 0 and content is None and not item.get("extracted_measurements"):
                errors.append(
                    {
                        "code": "empty_file",
                        "index": i,
                        "filename": filename,
                        "message": "File is empty",
                    }
                )
            if size > self.config.max_file_bytes:
                errors.append(
                    {
                        "code": "max_file_bytes_exceeded",
                        "index": i,
                        "filename": filename,
                        "message": (
                            f"File exceeds {self.config.max_file_bytes} bytes "
                            f"(got {size})"
                        ),
                    }
                )
            if not _mime_allowed(mime, filename, self.config) and not item.get(
                "extracted_measurements"
            ):
                # Allow AI/JSON measurement payloads without binary extension
                if not (
                    item.get("document")
                    or (mime.endswith("json") and item.get("extracted_measurements") is not None)
                ):
                    errors.append(
                        {
                            "code": "unsupported_type",
                            "index": i,
                            "filename": filename,
                            "message": f"Unsupported type for {filename} ({mime})",
                        }
                    )

        if total_bytes > self.config.max_batch_bytes:
            errors.append(
                {
                    "code": "max_batch_bytes_exceeded",
                    "message": (
                        f"Batch exceeds {self.config.max_batch_bytes} bytes "
                        f"(got {total_bytes})"
                    ),
                }
            )

        return {
            "ok": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "total_bytes": total_bytes,
            "file_count": len(items),
            "limits": self.config.to_dict(),
        }

    def import_batch(
        self,
        items: list[dict[str, Any]],
        *,
        batch_id: str | None = None,
        auto_group: bool = True,
        skip_validation: bool = False,
        confirmed_by_user: bool = False,
        confirmation_timestamp: str | None = None,
    ) -> dict[str, Any]:
        batch_id = batch_id or str(uuid4())
        started = utc_now()
        kwargs = {
            "confirmed_by_user": confirmed_by_user,
            "confirmation_timestamp": confirmation_timestamp,
        }
        prepared: list[dict[str, Any]] = []
        for item in items or []:
            prepared.append(
                {
                    **item,
                    "filename": sanitize_filename(item.get("filename")),
                }
            )

        if not skip_validation:
            validation = self.validate_batch(prepared)
            if not validation["ok"]:
                return {
                    "ok": False,
                    "partial_success": False,
                    "status": "rejected",
                    "batch_id": batch_id,
                    "total": len(prepared),
                    "imported": 0,
                    "duplicates": 0,
                    "failed": len(prepared),
                    "requires_review": 0,
                    "results": [],
                    "validation": validation,
                    "started_at": started,
                    "finished_at": utc_now(),
                    "limits": self.config.to_dict(),
                }

        groups = suggest_groups(prepared) if auto_group else {}
        results: list[dict[str, Any]] = []
        imported = duplicates = failed = requires_review = 0

        for i, item in enumerate(prepared):
            g = groups.get(i) or {
                "group_id": str(uuid4()),
                "sequence_number": i + 1,
                "page_number": None,
                "group_title": None,
            }
            # Explicit overrides from caller win
            group_id = item.get("group_id") or g["group_id"]
            sequence_number = item.get("sequence_number") or g["sequence_number"]
            page_number = item.get("page_number") if item.get("page_number") is not None else g["page_number"]
            group_title = item.get("group_title") if item.get("group_title") is not None else g["group_title"]

            req = {
                "content": item.get("content"),
                "document": item.get("document"),
                "text": item.get("text"),
                "json": item.get("json"),
                "filename": item["filename"],
                "mime_type": item.get("mime_type") or "application/octet-stream",
                "document_type": item.get("document_type"),
                "source_system": item.get("source_system"),
                "acquisition_method": item.get("acquisition_method"),
                "measured_at": item.get("measured_at"),
                "extracted_measurements": item.get("extracted_measurements"),
                "interpretation": item.get("interpretation"),
                "confidence": item.get("confidence"),
                "provenance": item.get("provenance"),
                "tags": list(item.get("tags") or []),
                "patient_id": item.get("patient_id"),
                "batch_id": batch_id,
                "group_id": group_id,
                "sequence_number": sequence_number,
                "page_number": page_number,
                "group_title": group_title,
            }
            # Drop Nones so pipeline defaults apply
            req = {k: v for k, v in req.items() if v is not None}

            try:
                result = self.pipeline.run(req)
            except Exception as exc:
                result = {
                    "ok": False,
                    "duplicate": False,
                    "status": "failed",
                    "document": None,
                    "measurements": [],
                    "errors": [f"pipeline_exception:{type(exc).__name__}"],
                    "warnings": [],
                }

            status = self._map_status(result)
            doc = result.get("document") or {}
            entry = {
                "index": i,
                "filename": item["filename"],
                "ok": bool(result.get("ok")),
                "duplicate": bool(result.get("duplicate")),
                "status": status,
                "document_id": doc.get("id"),
                "duplicate_of": result.get("original_document_id") or doc.get("duplicate_of"),
                "original_document_id": result.get("original_document_id"),
                "sha256": result.get("sha256"),
                "parser": result.get("parser"),
                "confidence": result.get("confidence"),
                "provenance": doc.get("provenance") or item.get("provenance"),
                "category": doc.get("primary_category"),
                "primary_category": doc.get("primary_category"),
                "secondary_categories": doc.get("secondary_categories") or [],
                "measured_at": doc.get("measured_at"),
                "date_confidence": doc.get("date_confidence"),
                "date_source": doc.get("date_source"),
                "batch_id": batch_id,
                "group_id": group_id,
                "sequence_number": sequence_number,
                "page_number": page_number,
                "group_title": group_title,
                "measurement_count": len(result.get("measurements") or []),
                "warnings": result.get("warnings") or [],
                "errors": result.get("errors") or [],
            }
            results.append(entry)

            if status == "duplicate":
                duplicates += 1
            elif status == "imported":
                imported += 1
            elif status == "requires_review":
                requires_review += 1
            else:
                failed += 1

        total = len(prepared)
        partial = imported > 0 and (failed > 0 or requires_review > 0)
        ok = failed == 0 and requires_review == 0 and (
            imported + duplicates == total
        )

        category_counts: dict[str, int] = {}
        measured_dates = []
        group_ids = set()
        for r in results:
            cat = r.get("primary_category") or r.get("category") or "other"
            if r.get("status") in {"imported", "requires_review", "duplicate"}:
                category_counts[cat] = category_counts.get(cat, 0) + 1
            if r.get("measured_at"):
                measured_dates.append(str(r["measured_at"]))
            if r.get("group_id"):
                group_ids.add(r["group_id"])

        finished = utc_now()
        report = {
            "ok": ok,
            "partial_success": partial or (imported > 0 and failed > 0),
            "status": (
                "complete"
                if ok
                else ("partial_success" if imported or duplicates else "failed")
            ),
            "batch_id": batch_id,
            "selected": total,
            "total": total,
            "imported": imported,
            "duplicates": duplicates,
            "failed": failed,
            "requires_review": requires_review,
            "category_counts": category_counts,
            "grouped_reports": len(group_ids),
            "earliest_measured_at": min(measured_dates) if measured_dates else None,
            "latest_measured_at": max(measured_dates) if measured_dates else None,
            "results": results,
            "started_at": started,
            "finished_at": finished,
            "completed_at": finished,
            "limits": self.config.to_dict(),
            "confirmed_by_user": bool(kwargs.get("confirmed_by_user")),
            "confirmation_timestamp": kwargs.get("confirmation_timestamp"),
        }

        try:
            self.store.record_batch_audit(
                {
                    "batch_id": batch_id,
                    "selected_count": total,
                    "confirmed_by_user": report["confirmed_by_user"],
                    "confirmation_timestamp": report.get("confirmation_timestamp"),
                    "imported_count": imported,
                    "duplicate_count": duplicates,
                    "failed_count": failed,
                    "category_counts": category_counts,
                    "earliest_measured_at": report["earliest_measured_at"],
                    "latest_measured_at": report["latest_measured_at"],
                    "completed_at": finished,
                }
            )
        except Exception:
            pass

        return report

    @staticmethod
    def _map_status(result: dict[str, Any]) -> str:
        if result.get("duplicate"):
            return "duplicate"
        if not result.get("ok"):
            return "failed"
        errors = result.get("errors") or []
        # Hard validation errors → requires_review; classification flags stay on the document
        if errors:
            return "requires_review"
        status = str(result.get("status") or "").lower()
        if status in {"failed"}:
            return "failed"
        return "imported"
