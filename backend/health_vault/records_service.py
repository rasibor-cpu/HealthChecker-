from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any
from backend.health_vault.batch_import import BatchImportService, sanitize_filename
from backend.health_vault.health_intelligence import HealthIntelligenceEngine
from backend.health_vault.metric_normalization import canonicalize_metric
from backend.health_vault.vault_store import VaultStore
from backend.health_vault.models import (
    HealthRecord,
    RecordStatus,
    RecordCategory,
    RecordProcessingEvent,
    RecordLinkage,
    utc_now,
)

SURFACE_CLINICAL = "clinical_document"
SURFACE_IMPORTED = "imported_report"
SURFACE_DEVICE = "device_data"

_CLINICAL_CATEGORIES = {
    RecordCategory.LABS,
    RecordCategory.GLUCOSE,
    RecordCategory.KIDNEY,
    RecordCategory.BLOOD_PRESSURE,
    RecordCategory.ECG,
    RecordCategory.MEDICATION,
}
_CLINICAL_TYPE_TOKENS = (
    "laboratory",
    "lab_report",
    "imaging",
    "radiology",
    "ecg",
    "ekg",
    "prescription",
    "medication",
    "consult",
    "discharge",
    "referral",
    "hospital",
    "visit_note",
    "clinical_report",
)
_DEVICE_METRIC_LABELS = {
    "heart_rate": "Heart Rate",
    "resting_hr": "Resting Heart Rate",
    "steps": "Steps",
    "sleep_duration": "Sleep",
    "sleep_score": "Sleep score",
    "oxygen_saturation": "Oxygen Saturation",
    "exercise_minutes": "Exercise",
    "activity_minutes": "Activity",
    "weight": "Weight",
}
_SEARCH_METRIC_ALIASES = {
    "creatinine": ("creatinine", "creatinine_serum", "creatinine_urine"),
    "blood pressure": ("systolic_bp", "diastolic_bp", "blood_pressure"),
    "blood_pressure": ("systolic_bp", "diastolic_bp", "blood_pressure"),
    "bp": ("systolic_bp", "diastolic_bp"),
    "medication": ("medication",),
    "ecg": ("ecg",),
}

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


def _record_blob(record: HealthRecord) -> str:
    meta = record.metadata or {}
    prov = record.source_provenance or {}
    category = (
        record.primary_category.value
        if isinstance(record.primary_category, RecordCategory)
        else str(record.primary_category or "")
    )
    status = (
        record.status.value
        if isinstance(record.status, RecordStatus)
        else str(record.status or "")
    )
    tags = prov.get("tags") or []
    return " ".join(
        str(part or "")
        for part in (
            record.original_filename,
            record.source_system if hasattr(record, "source_system") else None,
            prov.get("source_system"),
            prov.get("provenance"),
            prov.get("acquisition_method"),
            meta.get("document_type"),
            category,
            status,
            " ".join(str(tag) for tag in tags),
        )
    ).lower()


def is_device_telemetry_record(record: HealthRecord) -> bool:
    """True for Health Connect / continuous-monitoring JSON telemetry documents."""
    meta = record.metadata or {}
    if meta.get("document_type") == "continuous_monitoring_observation":
        return True
    blob = _record_blob(record)
    filename = str(record.original_filename or "").lower()
    if filename.startswith("health_connect_"):
        return True
    return "health_connect" in blob or "continuous_monitoring" in blob or "hc302" in blob


def classify_record_surface(record: HealthRecord) -> str:
    """Presentation class only — does not rewrite stored documents."""
    if is_device_telemetry_record(record):
        return SURFACE_DEVICE
    blob = _record_blob(record)
    if record.primary_category in _CLINICAL_CATEGORIES:
        return SURFACE_CLINICAL
    if any(token in blob for token in _CLINICAL_TYPE_TOKENS):
        return SURFACE_CLINICAL
    if "manual_upload" in blob:
        return SURFACE_CLINICAL
    return SURFACE_IMPORTED


def existing_client_search_matches(record: HealthRecord, term: str) -> bool:
    """Reproduce the pre-UAT12I client filter: filename/category/source/status only."""
    needle = str(term or "").strip().lower()
    if not needle:
        return True
    category = (
        record.primary_category.value
        if isinstance(record.primary_category, RecordCategory)
        else str(record.primary_category or "")
    )
    status = (
        record.status.value
        if isinstance(record.status, RecordStatus)
        else str(record.status or "")
    )
    source = (record.source_provenance or {}).get("source_system") or ""
    return any(
        needle in str(value or "").lower()
        for value in (record.original_filename, category, source, status)
    )


def _metric_from_filename(filename: str) -> str:
    name = str(filename or "").lower()
    if name.startswith("health_connect_"):
        parts = name.split("_")
        # health_connect_<metric...>_<uuid>.json
        if len(parts) >= 4:
            metric = "_".join(parts[2:-1])
            return canonicalize_metric(metric.replace(".json", ""))
    return ""


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
                extracted_measurements=self._display_measurements(patient_id, measurements),
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

    def _metrics_by_document(self, measurements: list[dict[str, Any]] | None = None) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = defaultdict(list)
        for measurement in measurements if measurements is not None else self.store.list_measurements():
            document_id = str(measurement.get("document_id") or "")
            metric = canonicalize_metric(
                measurement.get("metric") or measurement.get("metric_type") or ""
            )
            if document_id and metric:
                grouped[document_id].append(metric)
        return dict(grouped)

    def _search_records(
        self,
        records: list[HealthRecord],
        term: str,
        metrics_by_document: dict[str, list[str]],
    ) -> list[HealthRecord]:
        needle = str(term or "").strip().lower()
        if not needle:
            return list(records)
        wanted_metrics = set()
        for key, aliases in _SEARCH_METRIC_ALIASES.items():
            if key in needle or needle in key:
                wanted_metrics.update(aliases)
        wanted_metrics.add(canonicalize_metric(needle.replace(" ", "_")))
        wanted_metrics.discard("")
        wanted_metrics.discard("unknown")
        matched: list[HealthRecord] = []
        for record in records:
            if existing_client_search_matches(record, needle):
                matched.append(record)
                continue
            blob = _record_blob(record)
            if needle in blob:
                matched.append(record)
                continue
            doc_metrics = metrics_by_document.get(record.document_id) or []
            if wanted_metrics and any(metric in wanted_metrics for metric in doc_metrics):
                matched.append(record)
        return matched

    def _device_summaries(
        self,
        device_records: list[HealthRecord],
        metrics_by_document: dict[str, list[str]],
        measurements: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        latest_value: dict[str, tuple[str, Any, str]] = {}
        for measurement in measurements:
            document_id = str(measurement.get("document_id") or "")
            metric = canonicalize_metric(
                measurement.get("metric") or measurement.get("metric_type") or ""
            )
            measured_at = str(measurement.get("measured_at") or "")
            if not metric:
                continue
            previous = latest_value.get(metric)
            if previous is None or measured_at > previous[0]:
                latest_value[metric] = (
                    measured_at,
                    measurement.get("value"),
                    str(measurement.get("units") or ""),
                )
        buckets: dict[str, dict[str, Any]] = {}
        for record in device_records:
            metrics = metrics_by_document.get(record.document_id) or []
            metric = metrics[0] if metrics else _metric_from_filename(record.original_filename)
            metric = canonicalize_metric(metric) if metric else "other"
            bucket = buckets.setdefault(
                metric,
                {
                    "metric": metric,
                    "label": _DEVICE_METRIC_LABELS.get(
                        metric, metric.replace("_", " ").title()
                    ),
                    "record_count": 0,
                    "latest_value": None,
                    "latest_units": "",
                    "latest_at": None,
                    "source": "health_connect_companion",
                },
            )
            bucket["record_count"] += 1
            stamp = record.measured_at or record.imported_at
            if stamp and (not bucket["latest_at"] or str(stamp) > str(bucket["latest_at"])):
                bucket["latest_at"] = stamp
            live = latest_value.get(metric)
            if live:
                bucket["latest_at"] = live[0] or bucket["latest_at"]
                bucket["latest_value"] = live[1]
                bucket["latest_units"] = live[2]
        return sorted(buckets.values(), key=lambda row: row["label"])

    def _summary_dict(self, record: HealthRecord) -> dict[str, Any]:
        payload = record.to_summary_dict()
        payload["surface"] = classify_record_surface(record)
        return payload

    def consumer_records_payload(
        self,
        patient_id: str,
        *,
        category: str | None = None,
        status: str | None = None,
        metric: str | None = None,
        metrics: list[str] | str | None = None,
        q: str | None = None,
        surface: str | None = None,
    ) -> dict[str, Any]:
        """Group Health Records for the consumer UI without mutating stored documents."""
        measurements = list(self.store.list_measurements() or [])
        measurement_counts = self._measurement_counts_by_document(measurements)
        metrics_by_document = self._metrics_by_document(measurements)
        all_records = self.list_records(
            patient_id,
            measurement_counts=measurement_counts,
        )
        classified = [(record, classify_record_surface(record)) for record in all_records]
        surface_counts = {
            SURFACE_CLINICAL: 0,
            SURFACE_IMPORTED: 0,
            SURFACE_DEVICE: 0,
        }
        for _record, class_name in classified:
            surface_counts[class_name] = surface_counts.get(class_name, 0) + 1
        device_records = [record for record, class_name in classified if class_name == SURFACE_DEVICE]
        summaries = self._device_summaries(device_records, metrics_by_document, measurements)

        filtered = list(all_records)
        if category:
            expected_category = map_category(category)
            filtered = [record for record in filtered if record.primary_category == expected_category]
        if status:
            expected_status = map_status(status, False)
            filtered = [record for record in filtered if record.status == expected_status]
        if metric or metrics:
            from backend.health_vault.health_snapshot import metric_filter_aliases, snapshot_metric_id

            want = metric_filter_aliases(metric, metrics)
            matching_docs = {
                document_id
                for document_id, names in metrics_by_document.items()
                if any(name in want or snapshot_metric_id(name) in want for name in names)
            }
            filtered = [record for record in filtered if record.document_id in matching_docs]

        query = str(q or "").strip()
        surface_key = str(surface or "").strip().lower()
        if query:
            visible = self._search_records(filtered, query, metrics_by_document)
            mode = "search"
        elif metric or metrics:
            visible = filtered
            mode = "metric_drilldown"
        elif surface_key in {SURFACE_DEVICE, "device"}:
            visible = []
            mode = SURFACE_DEVICE
        elif surface_key in {SURFACE_IMPORTED, "imported_reports"}:
            visible = [
                record for record in filtered if classify_record_surface(record) == SURFACE_IMPORTED
            ]
            mode = SURFACE_IMPORTED
        elif surface_key in {SURFACE_CLINICAL, "clinical_documents"}:
            visible = [
                record for record in filtered if classify_record_surface(record) == SURFACE_CLINICAL
            ]
            mode = SURFACE_CLINICAL
        else:
            # Omitted surface keeps the historical full listing for API callers
            # and dashboard-adjacent tests. The consumer UI requests a surface.
            visible = filtered
            mode = "all"

        visible.sort(
            key=lambda record: (record.measured_at or record.imported_at, record.document_id),
            reverse=True,
        )
        return {
            "records": [self._summary_dict(record) for record in visible],
            "vault_record_count": len(all_records),
            "mode": mode,
            "surface_counts": {
                "clinical_documents": surface_counts.get(SURFACE_CLINICAL, 0),
                "imported_reports": surface_counts.get(SURFACE_IMPORTED, 0),
                "device_data": surface_counts.get(SURFACE_DEVICE, 0),
            },
            "device_data": {
                "summaries": summaries,
                "record_count": surface_counts.get(SURFACE_DEVICE, 0),
                "preserved": True,
            },
            "search": {
                "term": query,
                "match_count": len(visible) if query else 0,
            },
        }

    def _display_measurements(self, patient_id: str, measurements: list[dict[str, Any]]) -> list[dict[str, Any]]:
        from backend.health_vault.models import UserDashboardPreferences
        from backend.health_vault.unit_conversion import apply_display_units

        profile = self.store.get_profile(patient_id=patient_id) or {}
        prefs = UserDashboardPreferences.from_dict(profile.get("dashboard_preferences") or {})
        return [
            apply_display_units(
                dict(row),
                region=prefs.reporting_region,
                unit_overrides=prefs.unit_overrides,
            )
            for row in measurements
        ]

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
