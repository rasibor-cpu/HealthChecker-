from __future__ import annotations

from collections import Counter
from typing import Any
from backend.health_vault.batch_import import BatchImportService, sanitize_filename
from backend.health_vault.health_intelligence import HealthIntelligenceEngine
from backend.health_vault.vault_store import VaultStore
from backend.health_vault.models import (
    HealthRecord,
    RecordStatus,
    RecordCategory,
    RecordProcessingEvent,
    RecordLinkage,
    utc_now,
)

def map_category(cat_str: str | None) -> RecordCategory:
    if not cat_str:
        return RecordCategory.OTHER
    cat_str = cat_str.lower()
    if "pressure" in cat_str:
        return RecordCategory.BLOOD_PRESSURE
    if "sleep" in cat_str:
        return RecordCategory.SLEEP
    if "ecg" in cat_str or "cardio" in cat_str:
        return RecordCategory.ECG
    if "glucose" in cat_str or "diabetes" in cat_str:
        return RecordCategory.GLUCOSE
    if "kidney" in cat_str or "renal" in cat_str:
        return RecordCategory.KIDNEY
    if "lab" in cat_str:
        return RecordCategory.LABS
    if "weight" in cat_str or "body" in cat_str:
        return RecordCategory.WEIGHT
    if "med" in cat_str:
        return RecordCategory.MEDICATION
    return RecordCategory.OTHER

def map_status(status_str: str | None, requires_review: bool) -> RecordStatus:
    if requires_review:
        return RecordStatus.REQUIRES_REVIEW
    if not status_str:
        return RecordStatus.IMPORTED
    status_str = status_str.lower()
    if status_str == "imported":
        return RecordStatus.IMPORTED
    if status_str in {"parsed", "partial", "complete", "completed", "ok"}:
        return RecordStatus.IMPORTED
    if status_str in RecordStatus._value2member_map_:
        return RecordStatus(status_str)
    return RecordStatus.FAILED

class RecordsService:
    def __init__(self, store: VaultStore):
        self.store = store
        self.batch_service = BatchImportService(store=store)
        self.intelligence = HealthIntelligenceEngine(store)

    @staticmethod
    def _patient_id(row: dict[str, Any]) -> str:
        return str(row.get("patient_id") or "default-patient")

    def _lifecycle(self, doc: dict[str, Any]) -> list[RecordProcessingEvent]:
        """Return only events backed by persisted VaultStore records."""
        doc_id = str(doc["id"])
        final_status = map_status(doc.get("status"), bool(doc.get("requires_review")))
        events: list[RecordProcessingEvent] = []
        for audit in self.store.audit():
            detail = audit.get("detail") or {}
            if detail.get("document_id") != doc_id:
                continue
            events.append(RecordProcessingEvent(
                event_id=str(audit.get("id") or f"audit-{doc_id}"),
                document_id=doc_id,
                status=RecordStatus.PROCESSING,
                timestamp=str(audit.get("at") or doc.get("imported_at") or ""),
                event_type=str(audit.get("action") or "vault_event"),
                source="vault_audit",
                details=dict(detail),
            ))
        for imported in self.store.imports():
            if imported.get("document_id") != doc_id:
                continue
            events.append(RecordProcessingEvent(
                event_id=str(imported.get("import_id") or f"import-{doc_id}"),
                document_id=doc_id,
                status=final_status,
                timestamp=str(imported.get("imported_at") or doc.get("imported_at") or ""),
                event_type="import_recorded",
                source="intake_import",
                details={
                    "measurement_count": imported.get("measurement_count", 0),
                    "duplicate_content": bool(imported.get("duplicate_content")),
                    "parser": imported.get("parser"),
                },
            ))
        for index, log in enumerate(self.store.import_log()):
            if log.get("document_id") != doc_id:
                continue
            events.append(RecordProcessingEvent(
                event_id=f"import-log-{doc_id}-{index}",
                document_id=doc_id,
                status=RecordStatus.DUPLICATE if log.get("duplicates") else final_status,
                timestamp=str(log.get("timestamp") or doc.get("imported_at") or ""),
                event_type="import_completed",
                source="intake_import_log",
                details={
                    "result": log.get("result"),
                    "warnings": list(log.get("warnings") or []),
                    "errors": list(log.get("errors") or []),
                },
            ))
        events.sort(key=lambda event: (event.timestamp, event.event_id))
        return events

    @staticmethod
    def _source_provenance(doc: dict[str, Any]) -> dict[str, Any]:
        tags = list(doc.get("tags") or [])
        tagged = {}
        for tag in tags:
            if isinstance(tag, str) and ":" in tag:
                key, value = tag.split(":", 1)
                tagged[key] = value
        source_system = doc.get("source_system")
        provenance = doc.get("provenance")
        is_gmail = "gmail" in str(source_system or provenance or "").lower()
        return {
            "source_system": source_system,
            "acquisition_method": doc.get("acquisition_method"),
            "provenance": provenance,
            "gmail": {
                "source": "gmail",
                "message_id": doc.get("gmail_message_id") or tagged.get("gmail_message_id"),
                "attachment_id": doc.get("gmail_attachment_id") or tagged.get("gmail_attachment_id"),
            } if is_gmail else None,
            "original_filename": doc.get("original_filename"),
            "sha256": doc.get("sha256"),
            "batch_id": doc.get("batch_id"),
            "group_id": doc.get("group_id"),
            "tags": tags,
        }

    @staticmethod
    def _measurement_counts_by_document(measurements: list[dict[str, Any]]) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for measurement in measurements:
            document_id = str(measurement.get("document_id") or "")
            if document_id:
                counts[document_id] += 1
        return dict(counts)

    def _build_record(
        self,
        doc: dict[str, Any],
        include_linkage: bool = False,
        metrics_count: int | None = None,
    ) -> HealthRecord:
        doc_id = doc["id"]
        patient_id = doc.get("patient_id", "default-patient")

        category = map_category(doc.get("primary_category"))
        status = map_status(doc.get("status"), doc.get("requires_review", False))

        linkage = None
        measurements: list[dict[str, Any]] = []
        if include_linkage:
            measurements = self.store.list_measurements(document_id=doc_id)

            from backend.health_vault.timeline import build_timeline
            t_events = build_timeline(self.store, patient_id=patient_id)
            linked_t_events = [
                e for e in t_events
                if e.get("entry_kind") == "document" and e.get("document", {}).get("id") == doc_id
            ]

            linked_obs = []
            evidence_refs = []
            observation_rows = self.intelligence.get_patient_observations(patient_id)
            observation_rows.extend(self.store.list_observations())
            seen_observations: set[str] = set()
            for observation in observation_rows:
                if self._patient_id(observation) != patient_id:
                    continue
                observation_id = str(observation.get("observation_id") or observation.get("id") or "")
                if observation_id and observation_id in seen_observations:
                    continue
                evidence = observation.get("evidence") or observation.get("evidence_references") or []
                matching = [ref for ref in evidence if isinstance(ref, dict) and ref.get("document_id") == doc_id]
                if matching:
                    linked_obs.append(observation)
                    evidence_refs.extend(matching)
                    if observation_id:
                        seen_observations.add(observation_id)

            trends_dict = self.store.get_trends(patient_id=patient_id)
            linked_trends = []
            for m in measurements:
                metric = m.get("metric")
                if metric and metric in trends_dict:
                    linked_trends.append({"metric": metric, "trend": trends_dict[metric], "document_id": doc_id})

            linkage = RecordLinkage(
                document_id=doc_id,
                extracted_measurements=measurements,
                timeline_events=linked_t_events,
                ai_observations=linked_obs,
                trend_references=linked_trends,
                evidence_references=evidence_refs,
            )

        if metrics_count is None:
            metrics_count = (
                len(measurements)
                if include_linkage
                else len(self.store.list_measurements(document_id=doc_id))
            )

        return HealthRecord(
            document_id=doc_id,
            patient_id=patient_id,
            original_filename=doc.get("original_filename") or doc_id,
            primary_category=category,
            status=status,
            imported_at=doc.get("imported_at") or utc_now(),
            measured_at=doc.get("measured_at"),
            size_bytes=doc.get("size_bytes"),
            metrics_count=metrics_count,
            metadata={key: doc.get(key) for key in (
                "document_type", "mime_type", "interpretation", "parser_version",
                "parser_confidence", "classification_confidence", "classification_method",
                "date_confidence", "date_source", "secondary_categories",
            )},
            source_provenance=self._source_provenance(doc),
            linkage=linkage,
            lifecycle=self._lifecycle(doc) if include_linkage else [],
        )

    def list_records(
        self,
        patient_id: str,
        *,
        category: str | None = None,
        status: str | None = None,
        metric: str | None = None,
        metrics: list[str] | str | None = None,
        measurement_counts: dict[str, int] | None = None,
    ) -> list[HealthRecord]:
        patient_docs = [
            doc for doc in self.store.list_documents() if self._patient_id(doc) == patient_id
        ]
        if measurement_counts is None:
            measurement_counts = self._measurement_counts_by_document(self.store.list_measurements())
        records = [
            self._build_record(
                doc,
                include_linkage=False,
                metrics_count=measurement_counts.get(str(doc["id"]), 0),
            )
            for doc in patient_docs
        ]
        if category:
            expected_category = map_category(category)
            records = [record for record in records if record.primary_category == expected_category]
        if status:
            expected_status = map_status(status, False)
            records = [record for record in records if record.status == expected_status]
        if metric or metrics:
            from backend.health_vault.health_snapshot import metric_filter_aliases, snapshot_metric_id
            from backend.health_vault.metric_normalization import canonicalize_metric

            want = metric_filter_aliases(metric, metrics)
            matching_docs: set[str] = set()
            for measurement in self.store.list_measurements() or []:
                mid = canonicalize_metric(measurement.get("metric") or measurement.get("metric_type") or "")
                if mid in want or snapshot_metric_id(mid) in want:
                    document_id = str(measurement.get("document_id") or "")
                    if document_id:
                        matching_docs.add(document_id)
            records = [record for record in records if record.document_id in matching_docs]
        records.sort(key=lambda record: (record.measured_at or record.imported_at, record.document_id), reverse=True)
        return records

    def get_record_details(self, patient_id: str, document_id: str) -> HealthRecord | None:
        docs = self.store.list_documents()
        for doc in docs:
            if doc.get("id") == document_id:
                if self._patient_id(doc) == patient_id:
                    return self._build_record(doc, include_linkage=True)
                return None
        return None

    def upload_record(
        self,
        patient_id: str,
        content: bytes,
        filename: str,
        mime_type: str
    ) -> dict[str, Any]:
        item = {
            "patient_id": patient_id,
            "content": content,
            "filename": sanitize_filename(filename),
            "mime_type": mime_type,
            "size_bytes": len(content),
            "source_system": "healthchecker_plus",
            "acquisition_method": "manual_upload",
            "provenance": "manual_upload",
        }
        report = self.batch_service.import_batch([item])
        first = (report.get("results") or [{}])[0]
        document_id = first.get("document_id") or first.get("original_document_id") or first.get("duplicate_of")
        state = first.get("status") or ("quarantined" if report.get("status") == "rejected" else "failed")
        return {
            "ok": bool(report.get("ok") or report.get("partial_success")),
            "batch_id": report.get("batch_id"),
            "document_id": document_id,
            "imported_count": int(report.get("imported") or 0),
            "duplicate_count": int(report.get("duplicates") or 0),
            "requires_review_count": int(report.get("requires_review") or 0),
            "status": state,
            "validation": report.get("validation"),
            "errors": first.get("errors") or (report.get("validation") or {}).get("errors") or [],
            "warnings": first.get("warnings") or [],
        }
