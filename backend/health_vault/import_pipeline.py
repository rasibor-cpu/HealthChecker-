"""
Autonomous Import Pipeline — single orchestration path for all health record imports.

Document Received → Parser → OCR → Extract → Validate → Duplicate Detection →
Store Document → Store Measurements → Timeline → Trends → Doctor Visit →
Audit → Notify UI
"""

from __future__ import annotations

import json
import time
from typing import Any

from backend.health_vault import parsers as _parsers  # noqa: F401
from backend.health_vault.clinical_rules import ClinicalRulesEngine
from backend.health_vault.confidence_engine import ConfidenceEngine
from backend.health_vault.doctor_visit import DoctorVisitMode
from backend.health_vault.event_bus import (
    DOCUMENT_RECEIVED,
    DOCUMENT_STORED,
    DOCTOR_REPORT_UPDATED,
    DUPLICATE_DETECTED,
    IMPORT_COMPLETED,
    IMPORT_FAILED,
    MEASUREMENTS_EXTRACTED,
    MEASUREMENT_STORED,
    OCR_COMPLETED,
    PARSER_FAILED,
    PARSER_SELECTED,
    TIMELINE_UPDATED,
    TREND_UPDATED,
    VALIDATION_COMPLETED,
    EventBus,
    get_event_bus,
)
from backend.health_vault.health_intelligence import HealthIntelligenceEngine
from backend.health_vault.models import MedicalDocument, classify_document_type, utc_now, create_measurement
from backend.health_vault.ocr import get_ocr_provider
from backend.health_vault.parser_registry import get_default_registry
from backend.health_vault.timeline import build_timeline
from backend.health_vault.trend_engine import TrendEngine
from backend.health_vault.validation_engine import ValidationEngine
from backend.health_vault.vault_store import VaultStore
from backend.health_vault.category_classifier import classify_health_record
from backend.health_vault.date_extraction import extract_measured_date
from backend.health_vault.metric_normalization import normalize_measurement


class ImportPipeline:
    """Canonical autonomous import orchestration."""

    def __init__(
        self,
        store: VaultStore | None = None,
        registry=None,
        bus: EventBus | None = None,
    ) -> None:
        self.store = store or VaultStore()
        self.registry = registry or get_default_registry()
        self.bus = bus or get_event_bus()
        self.rules = ClinicalRulesEngine()
        self.validator = ValidationEngine(self.rules)
        self.confidence = ConfidenceEngine()
        self.trends = TrendEngine(self.store)
        self.doctor = DoctorVisitMode(self.store)
        self.intelligence = HealthIntelligenceEngine(self.store, self.trends)
        self.ocr = get_ocr_provider()
        self.last_perf: dict[str, float] = {}

    def run(self, request: dict[str, Any]) -> dict[str, Any]:
        t0 = time.perf_counter()
        timings: dict[str, float] = {}
        req = dict(request or {})
        warnings: list[str] = []
        errors: list[str] = []

        try:
            content, filename, mime, text, data_json = self._normalize_input(req)
            # Promote measured_at from JSON body when not provided on the request
            if not req.get("measured_at") and isinstance(data_json, dict):
                if data_json.get("measured_at"):
                    req["measured_at"] = data_json.get("measured_at")
                elif data_json.get("report_date"):
                    req["report_date"] = data_json.get("report_date")
            self.bus.publish(
                DOCUMENT_RECEIVED,
                {"filename": filename, "mime_type": mime, "size": len(content or b"")},
            )

            sha256 = (
                VaultStore.sha256_bytes(content)
                if content is not None
                else req.get("sha256")
            )
            document_type = classify_document_type(filename, mime, req.get("document_type"))

            # --- Duplicate detection (before store) ---
            dup = self._find_duplicate(sha256, req.get("measured_at"), filename, document_type)
            if dup is not None:
                self.bus.publish(
                    DUPLICATE_DETECTED,
                    {"original_id": dup.get("id"), "sha256": sha256},
                )
                result = {
                    "ok": True,
                    "duplicate": True,
                    "status": "Duplicate",
                    "document": dup,
                    "original_document_id": dup.get("id"),
                    "measurements": [],
                    "confidence": None,
                    "validation": None,
                    "warnings": ["Duplicate content — import skipped; referencing original"],
                    "errors": [],
                    "imported_at": utc_now(),
                    "sha256": sha256,
                    "perf_ms": {},
                }
                self._append_import_log(result)
                self.bus.publish(IMPORT_COMPLETED, {"duplicate": True, "document_id": dup.get("id")})
                return result

            provenance = req.get("provenance")
            tags = list(req.get("tags") or [])
            if provenance:
                prov_tag = f"provenance:{provenance}"
                if prov_tag not in tags:
                    tags.append(prov_tag)
            batch_id = req.get("batch_id")
            group_id = req.get("group_id")
            if batch_id:
                bt = f"batch_id:{batch_id}"
                if bt not in tags:
                    tags.append(bt)
            if group_id:
                gt = f"group_id:{group_id}"
                if gt not in tags:
                    tags.append(gt)
            document = MedicalDocument(
                patient_id=req.get("patient_id") or "default-patient",
                document_type=document_type,
                source_system=req.get("source_system") or "healthchecker_plus",
                acquisition_method=req.get("acquisition_method")
                or ("external_ai" if req.get("extracted_measurements") else "manual_upload"),
                original_filename=filename,
                sha256=sha256,
                mime_type=mime,
                tags=tags,
                interpretation=req.get("interpretation"),
                measured_at=req.get("measured_at"),
                status="imported",
                provenance=provenance,
                batch_id=batch_id,
                group_id=group_id,
                sequence_number=req.get("sequence_number"),
                page_number=req.get("page_number"),
                group_title=req.get("group_title"),
            )
            # Digital signature metadata
            document_meta = {
                "hash": sha256,
                "import_timestamp": document.imported_at,
                "parser_version": None,
                "ai_version": req.get("ai_version"),
            }

            # --- OCR ---
            t_ocr = time.perf_counter()
            ocr_result = self.ocr.extract(content, mime_type=mime, filename=filename)
            if ocr_result.text and not text:
                text = ocr_result.text
                try:
                    data_json = json.loads(text)
                except Exception:
                    pass
            timings["ocr_ms"] = (time.perf_counter() - t_ocr) * 1000
            self.bus.publish(OCR_COMPLETED, ocr_result.to_dict())

            # --- Determine parser + extract ---
            parse_ctx = {
                "document_id": document.id,
                "document_type": document.document_type,
                "filename": filename,
                "mime_type": mime,
                "text": text,
                "json": data_json,
                "source_system": document.source_system,
                "acquisition_method": document.acquisition_method,
                "extracted_measurements": req.get("extracted_measurements"),
                "confidence": req.get("confidence"),
                "measured_at": req.get("measured_at"),
            }
            parser = self.registry.resolve(parse_ctx)
            if parser is None:
                self.bus.publish(PARSER_FAILED, {"reason": "no_parser"})
                warnings.append("No parser matched; storing document with zero measurements")
                parsed = {
                    "parser": None,
                    "measurements": [],
                    "confidence": 0.0,
                    "notes": ["No registered parser matched"],
                }
            else:
                self.bus.publish(
                    PARSER_SELECTED,
                    {"parser_id": parser.id, "parser_name": parser.name, "version": parser.version},
                )
                t_parse = time.perf_counter()
                try:
                    parsed = self.registry.parse(parse_ctx)
                except Exception as exc:
                    self.bus.publish(PARSER_FAILED, {"error": type(exc).__name__})
                    errors.append(f"Parser failed: {type(exc).__name__}")
                    parsed = {
                        "parser": {"id": parser.id, "name": parser.name, "version": parser.version},
                        "measurements": [],
                        "confidence": 0.0,
                        "notes": [f"parser_exception:{type(exc).__name__}"],
                    }
                timings["parser_ms"] = (time.perf_counter() - t_parse) * 1000

            measurements = list(parsed.get("measurements") or [])
            document.parser_version = (
                f"{parsed['parser']['id']}@{parsed['parser']['version']}"
                if parsed.get("parser")
                else None
            )
            document_meta["parser_version"] = document.parser_version
            document.parser_confidence = (
                float(req["confidence"])
                if req.get("confidence") is not None
                else float(parsed.get("confidence") or 0.0)
            )
            self.bus.publish(
                MEASUREMENTS_EXTRACTED,
                {"count": len(measurements), "parser": parsed.get("parser")},
            )

            # Clinical flags on parser metrics (legacy names), then normalize for trends
            measurements = self.rules.apply(measurements)

            normalized_meas = []
            for m in measurements:
                raw = m.to_dict() if hasattr(m, "to_dict") else dict(m)
                norm = normalize_measurement(raw)
                obj = create_measurement(
                    **{
                        k: norm[k]
                        for k in (
                            "measurement_id",
                            "document_id",
                            "category",
                            "metric",
                            "value",
                            "units",
                            "reference_range",
                            "abnormal_flag",
                            "confidence",
                            "measured_at",
                            "fhir_resource",
                        )
                        if k in norm
                    }
                )
                obj.original_metric = norm.get("original_metric")
                obj.original_value = norm.get("original_value")
                obj.original_units = norm.get("original_units")
                obj.unit_compatible = bool(norm.get("unit_compatible", True))
                obj.normalization_version = norm.get("normalization_version")
                normalized_meas.append(obj)
            measurements = normalized_meas

            date_info = extract_measured_date(
                explicit_measured_at=req.get("measured_at") or document.measured_at,
                report_date=req.get("report_date"),
                parser_date=parsed.get("measured_at") or parsed.get("report_date"),
                source_metadata_date=req.get("source_metadata_date"),
                exif_capture_date=req.get("exif_capture_date") or req.get("file_capture_date"),
                filename=filename,
                imported_at=document.imported_at,
                measurement_dates=[
                    (m.measured_at if hasattr(m, "measured_at") else None) for m in measurements
                ],
            )
            document.measured_at = date_info["measured_at"]
            document.report_date = date_info.get("report_date")
            document.file_capture_date = date_info.get("file_capture_date")
            document.date_confidence = date_info.get("date_confidence")
            document.date_source = date_info.get("date_source")
            for m in measurements:
                if not getattr(m, "measured_at", None):
                    m.measured_at = document.measured_at

            classification = classify_health_record(
                document_type=document.document_type,
                filename=filename,
                source_system=document.source_system,
                measurements=measurements,
                ocr_text=(ocr_result.text if ocr_result else None) or req.get("text"),
                group_title=document.group_title,
            )
            document.primary_category = classification["primary_category"]
            document.secondary_categories = list(classification["secondary_categories"])
            document.classification_confidence = classification["classification_confidence"]
            document.classification_method = classification["classification_method"]
            document.classification_version = classification["classification_version"]
            document.requires_review = bool(
                classification["requires_review"] or date_info.get("requires_review")
            )
            if document.requires_review:
                tag = "requires_review"
                if tag not in document.tags:
                    document.tags.append(tag)
            cat_tag = f"category:{document.primary_category}"
            if cat_tag not in document.tags:
                document.tags.append(cat_tag)

            # --- Validation ---
            existing_fps = self._measurement_fingerprints()
            validation = self.validator.validate(
                measurements,
                document_measured_at=document.measured_at or document.imported_at,
                existing_fingerprints=existing_fps,
            )
            self.bus.publish(VALIDATION_COMPLETED, validation.to_dict())
            for issue in validation.issues:
                if issue.severity == "error":
                    errors.append(issue.message)
                elif issue.severity == "warning":
                    warnings.append(issue.message)

            flags = [
                (m.abnormal_flag if hasattr(m, "abnormal_flag") else m.get("abnormal_flag"))
                for m in measurements
            ]
            clinical_conf = self.confidence.clinical_from_flags(flags)

            # --- Store (immutable append) ---
            t_store = time.perf_counter()
            document.status = "parsed" if measurements else "partial"
            stored = self.store.store(
                document=document,
                measurements=measurements,
                content=content,
                interpretation=req.get("interpretation"),
                parser=parsed.get("parser"),
                import_meta={
                    "notes": parsed.get("notes"),
                    "mime_type": mime,
                    "ocr": ocr_result.to_dict(),
                    "validation": validation.to_dict(),
                    "digital_signature": document_meta,
                    "pipeline": "hc201h_classified_import_pipeline",
                    "classification": classification,
                    "date_extraction": date_info,
                },
            )
            timings["store_ms"] = (time.perf_counter() - t_store) * 1000
            storage_conf = 1.0 if stored.get("document", {}).get("storage_uri") or content is None else 0.85
            self.bus.publish(DOCUMENT_STORED, {"document_id": document.id, "sha256": sha256})
            self.bus.publish(
                MEASUREMENT_STORED,
                {"document_id": document.id, "count": len(measurements)},
            )

            # --- Timeline / trends / doctor / intelligence ---
            t_tl = time.perf_counter()
            timeline = build_timeline(self.store)
            timings["timeline_ms"] = (time.perf_counter() - t_tl) * 1000
            self.bus.publish(TIMELINE_UPDATED, {"entries": len(timeline)})

            t_tr = time.perf_counter()
            trends = self.trends.recompute()
            timings["trends_ms"] = (time.perf_counter() - t_tr) * 1000
            self.bus.publish(TREND_UPDATED, {"metrics": list(trends.keys())})

            t_doc = time.perf_counter()
            doctor_report = self.doctor.generate()
            observations = self.intelligence.generate_observations()
            timings["doctor_ms"] = (time.perf_counter() - t_doc) * 1000
            self.bus.publish(DOCTOR_REPORT_UPDATED, {"title": doctor_report.get("title")})

            conf = self.confidence.compute(
                extraction=float(parsed.get("confidence") or 0.0),
                validation=validation.confidence,
                clinical=clinical_conf,
                storage=storage_conf,
            )
            # Persist confidence on index import record
            self._attach_confidence(document.id, conf.to_dict())

            timings["total_ms"] = (time.perf_counter() - t0) * 1000
            self.last_perf = timings

            result = {
                "ok": True,
                "duplicate": False,
                "status": document.status,
                "document": stored["document"],
                "measurements": [
                    m.to_dict() if hasattr(m, "to_dict") else m for m in measurements
                ],
                "parser": parsed.get("parser"),
                "confidence": conf.to_dict(),
                "validation": validation.to_dict(),
                "ocr": ocr_result.to_dict(),
                "digital_signature": document_meta,
                "trends": trends,
                "timeline_preview": timeline[:5],
                "doctor_visit_preview": {
                    "kidney_trend": doctor_report.get("kidney_trend"),
                    "diabetes_trend": doctor_report.get("diabetes_trend"),
                },
                "observations": observations[:8],
                "warnings": warnings,
                "errors": errors,
                "import_record": stored["import_record"],
                "sha256": sha256,
                "imported_at": document.imported_at,
                "perf_ms": {k: round(v, 3) for k, v in timings.items()},
                "ui_notify": True,
            }
            self._append_import_log(result)
            self.bus.publish(
                IMPORT_COMPLETED,
                {"document_id": document.id, "overall_confidence": conf.overall_confidence},
            )
            return result

        except Exception as exc:
            self.bus.publish(IMPORT_FAILED, {"error": type(exc).__name__})
            fail = {
                "ok": False,
                "duplicate": False,
                "status": "failed",
                "document": None,
                "measurements": [],
                "confidence": None,
                "validation": None,
                "warnings": warnings,
                "errors": errors + [f"pipeline_exception:{type(exc).__name__}"],
                "imported_at": utc_now(),
                "perf_ms": {"total_ms": round((time.perf_counter() - t0) * 1000, 3)},
            }
            self._append_import_log(fail)
            return fail

    def _normalize_input(
        self, req: dict[str, Any]
    ) -> tuple[bytes | None, str, str, str, Any]:
        content: bytes | None = req.get("content")
        filename = req.get("filename") or "upload.bin"
        mime = req.get("mime_type") or "application/octet-stream"
        text = req.get("text") or ""
        data_json = req.get("json")

        if content is None and isinstance(req.get("document"), str):
            text = req["document"]
            content = text.encode("utf-8")
            try:
                data_json = json.loads(text)
            except Exception:
                pass
        elif content is not None and (
            "json" in mime or str(filename).lower().endswith(".json") or "text" in mime
        ):
            try:
                text = content.decode("utf-8", errors="replace")
                data_json = json.loads(text)
            except Exception:
                text = content.decode("utf-8", errors="replace")
        return content, filename, mime, text, data_json

    def _find_duplicate(
        self,
        sha256: str | None,
        measured_at: str | None,
        filename: str,
        document_type: str,
    ) -> dict[str, Any] | None:
        if not sha256:
            return None
        for doc in self.store.list_documents():
            if doc.get("sha256") == sha256:
                return doc
            # Soft metadata match when hash missing on older records
            if (
                measured_at
                and doc.get("measured_at") == measured_at
                and doc.get("original_filename") == filename
                and doc.get("document_type") == document_type
            ):
                return doc
        return None

    def _measurement_fingerprints(self) -> set[str]:
        fps: set[str] = set()
        for m in self.store.list_measurements():
            fps.add(
                f"{m.get('metric')}|{m.get('value')}|{m.get('measured_at') or ''}"
            )
        return fps

    def _attach_confidence(self, document_id: str, confidence: dict[str, Any]) -> None:
        data = self.store._read_index()
        for imp in data.get("imports") or []:
            if imp.get("document_id") == document_id:
                imp["confidence_breakdown"] = confidence
                break
        for doc in data.get("documents") or []:
            if doc.get("id") == document_id:
                doc["confidence_breakdown"] = confidence
                break
        self.store._write_index(data)

    def _append_import_log(self, result: dict[str, Any]) -> None:
        data = self.store._read_index()
        log = data.setdefault("import_log", [])
        log.append(
            {
                "timestamp": utc_now(),
                "parser": (result.get("parser") or {}).get("name")
                if isinstance(result.get("parser"), dict)
                else result.get("parser"),
                "result": "duplicate"
                if result.get("duplicate")
                else ("ok" if result.get("ok") else "failed"),
                "warnings": result.get("warnings") or [],
                "duplicates": bool(result.get("duplicate")),
                "errors": result.get("errors") or [],
                "document_id": (result.get("document") or {}).get("id")
                if isinstance(result.get("document"), dict)
                else None,
                "sha256": result.get("sha256"),
                "overall_confidence": (result.get("confidence") or {}).get("overall_confidence")
                if isinstance(result.get("confidence"), dict)
                else None,
            }
        )
        self.store._audit(data, "import_log_appended", {"result": log[-1]["result"]})
        self.store._write_index(data)
