"""
HC-201I — Executive Health Briefing Engine.

Observational decision-support only. Does not diagnose or prescribe.
Independent of UI so Doctor Visit, print, notifications, and future APIs can reuse it.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from backend.health_vault.clinical_rules import ClinicalRulesEngine
from backend.health_vault.date_extraction import timeline_sort_key
from backend.health_vault.metric_normalization import canonicalize_metric
from backend.health_vault.models import utc_now
from backend.health_vault.trend_engine import TrendEngine
from backend.health_vault.vault_store import VaultStore

_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "executive_dashboard.json"

DISCLAIMER = (
    "Observational decision-support only. Not a diagnosis or prescription. "
    "Does not replace professional medical assessment."
)

STATUS_LABELS = (
    "Stable",
    "Improving",
    "Worsening",
    "Needs attention",
    "Insufficient data",
    "Awaiting verification",
)


def _parse_iso(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        text = value.strip().replace("Z", "+00:00")
        if " " in text and "T" not in text:
            text = text.replace(" ", "T", 1)
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _days_ago(dt: datetime | None, *, as_of: datetime) -> float | None:
    if dt is None:
        return None
    return max(0.0, (as_of - dt).total_seconds() / 86400.0)


def load_executive_dashboard_config(path: Path | None = None) -> dict[str, Any]:
    p = path or _CONFIG_PATH
    if not p.exists():
        return {"schema_version": "hc.executive_dashboard.v1", "domains": [], "disclaimer": DISCLAIMER}
    return json.loads(p.read_text(encoding="utf-8"))


class ExecutiveHealthBriefingEngine:
    """Compose vault documents, measurements, trends, meds, imports into an executive briefing."""

    def __init__(
        self,
        store: VaultStore,
        *,
        config: dict[str, Any] | None = None,
        trend_engine: TrendEngine | None = None,
        clinical_rules: ClinicalRulesEngine | None = None,
    ) -> None:
        self.store = store
        self.config = config or load_executive_dashboard_config()
        self.trends = trend_engine or TrendEngine(store)
        self.rules = clinical_rules or ClinicalRulesEngine()

    def generate(
        self,
        *,
        patient_id: str = "default-patient",
        as_of: str | None = None,
        trend_window: str = "30d",
        category: str | None = None,
    ) -> dict[str, Any]:
        as_of_dt = _parse_iso(as_of) or datetime.now(timezone.utc)
        docs = [
            d
            for d in self.store.list_documents()
            if str(d.get("patient_id") or "default-patient") == patient_id
        ]
        if category:
            docs = [
                d
                for d in docs
                if d.get("primary_category") == category
                or category in (d.get("secondary_categories") or [])
            ]
        measurements = self.store.list_measurements()
        docs_by_id = {d["id"]: d for d in docs if d.get("id")}
        profile = self.store.get_profile() or {}
        batch_audits = list(reversed(self.store.list_batch_audits() or []))[:10]
        import_log = list(reversed(self.store.import_log() or []))[:20]

        # Ensure trends are current for windowed classification
        try:
            self.trends.recompute()
        except Exception:
            pass
        trend_snap = self.store.get_trends() or {}

        domain_summaries = self._domain_summaries(
            docs=docs,
            measurements=measurements,
            docs_by_id=docs_by_id,
            trend_snap=trend_snap,
            as_of=as_of_dt,
            trend_window=trend_window,
        )
        attention = self._attention_items(
            docs=docs,
            measurements=measurements,
            docs_by_id=docs_by_id,
            profile=profile,
            as_of=as_of_dt,
            domain_summaries=domain_summaries,
        )
        monitoring = self._monitoring_actions(attention=attention, domain_summaries=domain_summaries, docs=docs)
        recent_imports = self._recent_imports(batch_audits)
        records_requiring_sources = [
            {
                "document_id": d.get("id"),
                "title": d.get("original_filename") or d.get("document_type"),
                "category": d.get("primary_category"),
                "provenance": d.get("provenance"),
                "measured_at": d.get("measured_at"),
            }
            for d in docs
            if str(d.get("provenance") or "")
            in {"historical_summary", "user_reported", "source_document_required"}
            or d.get("status") == "awaiting_source"
        ]

        latest_health_date = None
        dated = sorted(docs, key=timeline_sort_key, reverse=True)
        if dated:
            latest_health_date = (
                dated[0].get("measured_at") or dated[0].get("report_date") or dated[0].get("imported_at")
            )

        recent_cutoff = as_of_dt - timedelta(days=7)
        new_recent = sum(
            1
            for d in docs
            if (_parse_iso(d.get("imported_at")) or datetime.min.replace(tzinfo=timezone.utc))
            >= recent_cutoff
        )
        review_count = sum(1 for d in docs if d.get("requires_review"))

        data_status = self._overall_data_status(
            docs=docs,
            domain_summaries=domain_summaries,
            review_count=review_count,
            records_requiring_sources=records_requiring_sources,
            as_of=as_of_dt,
        )

        return {
            "schema_version": "hc.executive_briefing.v1",
            "generated_at": utc_now(),
            "as_of": as_of_dt.isoformat().replace("+00:00", "Z"),
            "patient_id": patient_id,
            "trend_window": trend_window,
            "data_status": data_status,
            "last_updated": utc_now(),
            "latest_health_record_date": latest_health_date,
            "new_records_imported_recently": new_recent,
            "records_requiring_review": review_count,
            "records_requiring_sources_count": len(records_requiring_sources),
            "domain_summaries": domain_summaries,
            "attention_items": attention,
            "monitoring_actions": monitoring,
            "recent_imports": recent_imports,
            "records_requiring_sources": records_requiring_sources,
            "medications_summary": self._medications_summary(profile, docs),
            "import_log_tail": [
                {
                    "timestamp": e.get("timestamp"),
                    "result": e.get("result"),
                    "document_id": e.get("document_id"),
                    "warnings": e.get("warnings") or [],
                }
                for e in import_log[:8]
            ],
            "disclaimer": self.config.get("disclaimer") or DISCLAIMER,
            "observational_only": True,
            "diagnostic": False,
            "prescriptive": False,
        }

    def printable_summary(self, briefing: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        brief = briefing or self.generate(**kwargs)
        return {
            "title": "HealthChecker+ Executive Health Summary",
            "generated_at": brief.get("generated_at"),
            "patient_id": brief.get("patient_id"),
            "data_status": brief.get("data_status"),
            "domain_summaries": brief.get("domain_summaries"),
            "attention_items": brief.get("attention_items"),
            "monitoring_actions": brief.get("monitoring_actions"),
            "records_requiring_sources": brief.get("records_requiring_sources"),
            "medications_summary": brief.get("medications_summary"),
            "recent_imports": brief.get("recent_imports"),
            "disclaimer": brief.get("disclaimer") or DISCLAIMER,
            "observational_only": True,
        }

    # --- internals ---

    def _overall_data_status(
        self,
        *,
        docs: list[dict[str, Any]],
        domain_summaries: dict[str, Any],
        review_count: int,
        records_requiring_sources: list[dict[str, Any]],
        as_of: datetime,
    ) -> str:
        if not docs:
            return "Limited data"
        intervals = self.config.get("monitoring_intervals_days") or {}
        overdue = 0
        for domain in domain_summaries.values():
            cat = (domain.get("primary_category") or domain.get("id") or "")
            # map domain id to interval key loosely
            key = None
            for k in intervals:
                if k in str(domain.get("categories") or []) or k == domain.get("id"):
                    key = k
                    break
            if domain.get("id") == "blood_pressure":
                key = "blood_pressure"
            elif domain.get("id") == "diabetes":
                key = "glucose_diabetes"
            elif domain.get("id") == "kidney":
                key = "kidney_renal"
            elif domain.get("id") == "heart":
                key = "ecg_cardiology"
            days = intervals.get(key) if key else None
            latest = _parse_iso(domain.get("latest_date"))
            age = _days_ago(latest, as_of=as_of)
            if days is not None and (age is None or age > float(days)):
                overdue += 1
        if review_count or records_requiring_sources or overdue >= 2:
            return "Needs record updates"
        if overdue == 1 or any(
            d.get("status_label") == "Insufficient data" for d in domain_summaries.values()
        ):
            return "Partially current"
        return "Current"

    def _domain_summaries(
        self,
        *,
        docs: list[dict[str, Any]],
        measurements: list[dict[str, Any]],
        docs_by_id: dict[str, dict[str, Any]],
        trend_snap: dict[str, Any],
        as_of: datetime,
        trend_window: str,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for domain in self.config.get("domains") or []:
            did = str(domain.get("id") or "other")
            cats = set(domain.get("categories") or [])
            metrics = [canonicalize_metric(m) for m in (domain.get("metrics") or [])]
            domain_docs = [
                d
                for d in docs
                if d.get("primary_category") in cats
                or any(c in (d.get("secondary_categories") or []) for c in cats)
            ]
            domain_docs.sort(key=timeline_sort_key, reverse=True)
            latest_doc = domain_docs[0] if domain_docs else None

            latest_values: dict[str, Any] = {}
            window_days = (self.config.get("trend_windows_days") or {}).get(trend_window)
            for metric in metrics:
                series = self._windowed_series(
                    metric=metric,
                    measurements=measurements,
                    docs_by_id=docs_by_id,
                    as_of=as_of,
                    window_days=window_days,
                )
                if not series:
                    continue
                latest_values[metric] = {
                    "value": series[-1]["value"],
                    "units": series[-1].get("units"),
                    "measured_at": series[-1].get("measured_at"),
                    "document_id": series[-1].get("document_id"),
                    "confidence": series[-1].get("confidence"),
                    "count_in_window": len(series),
                }

            sleep_context = None
            if did == "sleep" and latest_doc:
                notes = str(latest_doc.get("interpretation") or "")
                tags = " ".join(str(t).lower() for t in (latest_doc.get("tags") or []))
                late = any(
                    tok in notes.lower() or tok in tags
                    for tok in ("late bedtime", "awake until", "4:00", "4am", "short night")
                )
                sleep_context = {
                    "single_night_result": True,
                    "seven_day_trend": self._trend_label_for_metric(
                        "sleep_duration", trend_snap, min_points=3
                    ),
                    "longer_term_trend": self._trend_label_for_metric(
                        "sleep_score", trend_snap, min_points=5
                    ),
                    "contextual_note": "Single-night result shown separately from multi-day trends.",
                }
                dur = latest_values.get("sleep_duration") or {}
                try:
                    minutes = float(dur.get("value"))
                except (TypeError, ValueError):
                    minutes = None
                if late or (minutes is not None and minutes < 300):
                    sleep_context["contextual_note"] = (
                        "This appears to be a single-night / short-sleep result. "
                        "A late bedtime can explain a short night and must not automatically "
                        "be treated as chronic deterioration."
                    )

            # Heart ECG fields from latest cardiology doc measurements / interpretation
            heart_extra = None
            if did == "heart" and latest_doc:
                heart_extra = self._heart_fields(latest_doc, measurements)

            # BP pair
            bp_display = None
            if did == "blood_pressure":
                sys_v = latest_values.get("systolic_bp")
                dia_v = latest_values.get("diastolic_bp")
                if sys_v and dia_v:
                    bp_display = f"{sys_v['value']}/{dia_v['value']} {sys_v.get('units') or 'mmHg'}"

            primary_metric = metrics[0] if metrics else None
            trend_label = (
                self._trend_label_for_metric(primary_metric, trend_snap)
                if primary_metric
                else "Insufficient data"
            )
            status = self._status_label(
                latest_doc=latest_doc,
                trend_label=trend_label,
                latest_values=latest_values,
            )

            out[did] = {
                "id": did,
                "title": domain.get("title"),
                "categories": list(cats),
                "status_label": status,
                "latest_date": (
                    (latest_doc or {}).get("measured_at")
                    or (latest_doc or {}).get("report_date")
                    or (latest_doc or {}).get("imported_at")
                ),
                "latest_values": latest_values,
                "bp_display": bp_display,
                "trend_direction": trend_label,
                "data_confidence": (latest_doc or {}).get("classification_confidence")
                or (latest_doc or {}).get("parser_confidence"),
                "provenance": (latest_doc or {}).get("provenance"),
                "source_system": (latest_doc or {}).get("source_system"),
                "recent_record_count": len(domain_docs),
                "requires_review": bool((latest_doc or {}).get("requires_review")),
                "verification_status": self._verification_status(latest_doc),
                "sleep_context": sleep_context,
                "heart_detail": heart_extra,
                "expandable_provenance": {
                    "document_id": (latest_doc or {}).get("id"),
                    "filename": (latest_doc or {}).get("original_filename"),
                    "date_source": (latest_doc or {}).get("date_source"),
                    "date_confidence": (latest_doc or {}).get("date_confidence"),
                    "classification_method": (latest_doc or {}).get("classification_method"),
                },
            }
        return out

    def _heart_fields(self, doc: dict[str, Any], measurements: list[dict[str, Any]]) -> dict[str, Any]:
        related = [m for m in measurements if m.get("document_id") == doc.get("id")]
        by_metric = {canonicalize_metric(m.get("metric")): m for m in related}
        rhythm = None
        for m in related:
            if canonicalize_metric(m.get("metric")) in {"rhythm", "ecg_rhythm"}:
                rhythm = m.get("value")
        if rhythm is None and doc.get("interpretation"):
            # Soft extract common phrase without diagnosing
            text = str(doc.get("interpretation"))
            if "sinus" in text.lower():
                rhythm = "Sinus rhythm"
        avg_hr = (by_metric.get("average_hr") or by_metric.get("heart_rate") or {}).get("value")
        symptoms = None
        for m in related:
            if "symptom" in str(m.get("metric") or "").lower():
                symptoms = m.get("value")
        if symptoms is None and "symptom" in str(doc.get("interpretation") or "").lower():
            symptoms = "see interpretation"
        return {
            "ecg_classification": rhythm or doc.get("interpretation"),
            "rhythm": rhythm,
            "average_heart_rate": avg_hr,
            "resting_or_sleeping_hr": (by_metric.get("resting_hr") or {}).get("value"),
            "hrv": (by_metric.get("hrv_rmssd") or {}).get("value"),
            "symptoms": symptoms if symptoms is not None else "none reported",
            "ecg_date": doc.get("measured_at") or doc.get("report_date"),
            "source_device": doc.get("source_system"),
            "wearable_note": (
                "Wearable ECG findings are observational and do not exclude all heart conditions."
            ),
        }

    def _verification_status(self, doc: dict[str, Any] | None) -> str:
        if not doc:
            return "Insufficient data"
        prov = str(doc.get("provenance") or "")
        if prov in {"original_document_verified", "laboratory_source", "wearable_pdf"}:
            return "original-document verified"
        if prov == "user_reported":
            return "user reported"
        if prov == "historical_summary":
            return "historical summary"
        if prov in {"source_document_required", ""} and doc.get("requires_review"):
            return "source document required"
        if prov == "wearable_screenshot":
            return "wearable screenshot"
        return prov or "unknown"

    def _windowed_series(
        self,
        *,
        metric: str,
        measurements: list[dict[str, Any]],
        docs_by_id: dict[str, dict[str, Any]],
        as_of: datetime,
        window_days: int | None,
    ) -> list[dict[str, Any]]:
        canonical = canonicalize_metric(metric)
        items = []
        for m in measurements:
            if canonicalize_metric(m.get("metric")) != canonical:
                continue
            if not self.trends._eligible(m, docs_by_id):  # noqa: SLF001 — shared eligibility
                continue
            doc = docs_by_id.get(str(m.get("document_id") or "")) or {}
            measured = _parse_iso(m.get("measured_at") or doc.get("measured_at") or doc.get("report_date"))
            if measured is None:
                continue
            if window_days is not None and window_days > 0:
                if measured < as_of - timedelta(days=int(window_days)):
                    continue
            if measured > as_of:
                continue
            try:
                val = float(m["value"])
            except (TypeError, ValueError, KeyError):
                continue
            items.append(
                {
                    "value": val,
                    "units": m.get("units"),
                    "measured_at": measured.isoformat().replace("+00:00", "Z"),
                    "document_id": m.get("document_id"),
                    "confidence": m.get("confidence") or doc.get("classification_confidence"),
                }
            )
        items.sort(key=lambda x: x["measured_at"])
        return items

    def _trend_label_for_metric(
        self, metric: str | None, trend_snap: dict[str, Any], *, min_points: int | None = None
    ) -> str:
        if not metric:
            return "Insufficient data"
        min_pts = min_points or int(self.config.get("trend_min_points") or 3)
        canonical = canonicalize_metric(metric)
        entry = trend_snap.get(canonical) or trend_snap.get(metric) or {}
        values = entry.get("values") or entry.get("series")
        if isinstance(values, list) and len(values) < min_pts:
            return "Insufficient data"
        label = entry.get("label")
        if label in {"Improving", "Stable", "Worsening"}:
            return label
        # Fall back to engine classify on stored series
        try:
            series = self.trends.series(canonical)
            if len(series) < min_pts:
                return "Insufficient data"
            result = TrendEngine.classify(canonical, series)
            return str(result.get("label") or "Insufficient data")
        except Exception:
            return "Insufficient data"

    def _status_label(
        self,
        *,
        latest_doc: dict[str, Any] | None,
        trend_label: str,
        latest_values: dict[str, Any],
    ) -> str:
        if not latest_doc and not latest_values:
            return "Insufficient data"
        if latest_doc and latest_doc.get("requires_review"):
            return "Awaiting verification"
        if trend_label == "Worsening":
            return "Needs attention"
        if trend_label == "Improving":
            return "Improving"
        if trend_label == "Stable":
            return "Stable"
        if latest_values:
            return "Stable"
        return "Insufficient data"

    def _attention_items(
        self,
        *,
        docs: list[dict[str, Any]],
        measurements: list[dict[str, Any]],
        docs_by_id: dict[str, dict[str, Any]],
        profile: dict[str, Any],
        as_of: datetime,
        domain_summaries: dict[str, Any],
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []

        def add(kind: str, code: str, message: str, *, priority: int = 50) -> None:
            items.append(
                {
                    "kind": kind,  # data_quality | monitoring | clinical_flag
                    "code": code,
                    "message": message,
                    "priority": priority,
                    "advisory_only": True,
                    "diagnostic": False,
                }
            )

        for d in docs:
            if d.get("status") == "failed":
                add("data_quality", "failed_import", f"Failed import: {d.get('original_filename') or d.get('id')}", priority=10)
            if d.get("requires_review"):
                add(
                    "data_quality",
                    "requires_review",
                    f"Record requires review ({d.get('primary_category') or 'uncategorized'}).",
                    priority=20,
                )
            conf = d.get("classification_confidence")
            if conf is not None and float(conf) < float(self.config.get("confidence_threshold") or 0.45):
                add(
                    "data_quality",
                    "low_confidence_classification",
                    f"Low-confidence classification for {d.get('original_filename') or d.get('id')}.",
                    priority=25,
                )
            if d.get("date_confidence") is not None and float(d.get("date_confidence") or 0) < 0.4:
                add(
                    "data_quality",
                    "unreliable_date",
                    f"Unreliable measured date for {d.get('original_filename') or d.get('id')}.",
                    priority=22,
                )
            if str(d.get("provenance") or "") in {"historical_summary", "user_reported", "source_document_required"}:
                add(
                    "data_quality",
                    "source_document_required",
                    f"Original source document still useful for {d.get('original_filename') or d.get('document_type')}.",
                    priority=18,
                )
            if d.get("duplicate_of"):
                add(
                    "data_quality",
                    "conflicting_duplicate",
                    f"Duplicate candidate linked to {d.get('duplicate_of')}.",
                    priority=28,
                )

        for m in measurements:
            if m.get("units") in (None, "", "unknown") and canonicalize_metric(m.get("metric")) in {
                "glucose",
                "creatinine",
                "weight",
            }:
                add(
                    "data_quality",
                    "missing_units",
                    f"Missing units for metric {m.get('metric')}.",
                    priority=30,
                )
            flag = self.rules.classify(m)
            if flag == "Critical":
                add(
                    "clinical_flag",
                    "clinical_flag",
                    f"Configured clinical rule flagged {canonicalize_metric(m.get('metric'))} as Critical (observational).",
                    priority=15,
                )

        intervals = self.config.get("monitoring_intervals_days") or {}
        for domain_id, domain in domain_summaries.items():
            key_map = {
                "blood_pressure": "blood_pressure",
                "diabetes": "glucose_diabetes",
                "kidney": "kidney_renal",
                "heart": "ecg_cardiology",
                "sleep": "sleep",
                "weight": "weight_body_metrics",
                "labs": "laboratory_report",
                "medications": "medication",
            }
            key = key_map.get(domain_id)
            if not key or key not in intervals:
                continue
            age = _days_ago(_parse_iso(domain.get("latest_date")), as_of=as_of)
            if age is None or age > float(intervals[key]):
                add(
                    "monitoring",
                    "overdue_measurement",
                    f"{domain.get('title') or domain_id} monitoring interval exceeded or missing recent data.",
                    priority=35,
                )

        meds = profile.get("medications") or []
        for med in meds:
            text = med if isinstance(med, str) else str(med.get("name") or med)
            if any(tok in text.lower() for tok in ("uncertain", "unknown dose", "?", "tbd")):
                add(
                    "data_quality",
                    "uncertain_medication",
                    f"Uncertain medication status: {text}.",
                    priority=24,
                )

        # Dedup by code+message
        seen: set[str] = set()
        unique = []
        for item in sorted(items, key=lambda x: (int(x["priority"]), x["code"], x["message"])):
            key = f"{item['code']}|{item['message']}"
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        return unique[:40]

    def _monitoring_actions(
        self,
        *,
        attention: list[dict[str, Any]],
        domain_summaries: dict[str, Any],
        docs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        codes = {a["code"] for a in attention}

        def add(code: str, prompt: str) -> None:
            actions.append(
                {
                    "code": code,
                    "prompt": prompt,
                    "advisory_only": True,
                    "prescriptive": False,
                    "note": "Record-completion / monitoring prompt — not a medical prescription.",
                }
            )

        if "source_document_required" in codes:
            add("upload_original_lab", "Upload the original laboratory report")
        if "uncertain_medication" in codes:
            add("confirm_med_dose", "Confirm medication dose")
        if domain_summaries.get("blood_pressure", {}).get("status_label") in {
            "Insufficient data",
            "Needs attention",
        } or "overdue_measurement" in codes:
            add("record_bp", "Record a current blood-pressure reading")
        diabetes = domain_summaries.get("diabetes") or {}
        if not (diabetes.get("latest_values") or {}).get("hba1c"):
            add("add_hba1c", "Add the latest HbA1c result")
        if "requires_review" in codes or "low_confidence_classification" in codes:
            add("review_low_confidence", "Review low-confidence imported records")
        if "overdue_measurement" in codes:
            add(
                "repeat_per_rule",
                "Repeat a measurement only where an existing configured monitoring rule supports it",
            )
        if not docs:
            add("upload_records", "Upload health records to populate the Executive Dashboard")

        # Dedup
        seen = set()
        out = []
        for a in actions:
            if a["code"] in seen:
                continue
            seen.add(a["code"])
            out.append(a)
        return out

    def _recent_imports(self, batch_audits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for b in batch_audits[:8]:
            out.append(
                {
                    "batch_id": b.get("batch_id"),
                    "batch_date": b.get("completed_at") or b.get("confirmation_timestamp"),
                    "selected": b.get("selected_count") or b.get("selected"),
                    "imported": b.get("imported_count") or b.get("imported"),
                    "duplicates": b.get("duplicate_count") or b.get("duplicates"),
                    "failed": b.get("failed_count") or b.get("failed"),
                    "category_counts": b.get("category_counts") or {},
                    "earliest_measured_at": b.get("earliest_measured_at"),
                    "latest_measured_at": b.get("latest_measured_at"),
                    "grouped_report_count": b.get("grouped_report_count")
                    or b.get("groups_created")
                    or 0,
                    "confirmed_by_user": b.get("confirmed_by_user"),
                }
            )
        return out

    def _medications_summary(self, profile: dict[str, Any], docs: list[dict[str, Any]]) -> dict[str, Any]:
        meds = profile.get("medications") or []
        current = []
        uncertain = []
        for med in meds:
            text = med if isinstance(med, str) else str(med.get("name") or med)
            entry = {"name": text, "status": "confirmed"}
            if any(tok in text.lower() for tok in ("uncertain", "unknown", "?", "tbd")):
                entry["status"] = "uncertain"
                uncertain.append(entry)
            else:
                current.append(entry)
        med_docs = [d for d in docs if d.get("primary_category") == "medication"]
        return {
            "current_medications": current,
            "uncertain_medication_statuses": uncertain,
            "recently_started": [],
            "recently_stopped": [],
            "missing_dose_information": [u["name"] for u in uncertain],
            "missing_start_stop_dates": [],
            "medication_document_count": len(med_docs),
            "timeline_link": "#vault",
            "note": "Does not infer drug interactions or recommend medication changes.",
        }
