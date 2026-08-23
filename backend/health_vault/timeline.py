"""Chronological health timeline builder (HC-201H measured-date priority + HC-301 unified events)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from backend.health_vault.date_extraction import timeline_sort_key
from backend.health_vault.health_snapshot import metric_filter_aliases, snapshot_metric_id
from backend.health_vault.metric_normalization import canonicalize_metric
from backend.health_vault.vault_store import VaultStore


CONSUMER_TIMELINE_LIMIT = 400

_CONSUMER_ENTRY_KEYS = (
    "date",
    "measured_at",
    "report_date",
    "imported_at",
    "primary_category",
    "category_label",
    "category",
    "group_id",
    "group_title",
    "sequence_number",
    "page_number",
    "trend_impact",
    "original_link",
    "entry_kind",
    "provenance",
    "source",
    "severity",
    "summary",
    "event_type",
    "dedupe_key",
)

_CONSUMER_DOCUMENT_KEYS = (
    "id",
    "patient_id",
    "document_type",
    "source_system",
    "original_filename",
    "measured_at",
    "report_date",
    "imported_at",
    "primary_category",
    "secondary_categories",
    "group_id",
    "group_title",
    "sequence_number",
    "page_number",
    "provenance",
    "storage_uri",
    "status",
)

_CONSUMER_MEASUREMENT_KEYS = (
    "metric",
    "metric_type",
    "value",
    "units",
    "unit",
    "measured_at",
    "source",
    "document_id",
)

_CONSUMER_PAYLOAD_KEYS = (
    "metric",
    "metric_type",
    "value",
    "unit",
    "units",
    "source",
    "g",
    "sys",
    "dia",
    "ts",
)


def _measurements_by_document(measurements: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for measurement in measurements:
        document_id = str(measurement.get("document_id") or "")
        if document_id:
            grouped[document_id].append(measurement)
    return dict(grouped)


def _pick(source: dict[str, Any] | None, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    return {key: source[key] for key in keys if key in source}


def project_consumer_timeline_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Display/transport projection — does not delete stored clinical documents."""
    out = _pick(entry, _CONSUMER_ENTRY_KEYS)
    document = entry.get("document") if isinstance(entry.get("document"), dict) else None
    slim_document = _pick(document, _CONSUMER_DOCUMENT_KEYS)
    out["document"] = slim_document or None
    measurements = entry.get("measurements") or []
    out["measurements"] = [
        _pick(row, _CONSUMER_MEASUREMENT_KEYS) for row in measurements if isinstance(row, dict)
    ]
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else None
    if payload:
        out["payload"] = _pick(payload, _CONSUMER_PAYLOAD_KEYS)
    return out


def build_consumer_timeline_response(
    entries: list[dict[str, Any]],
    *,
    unified: bool,
    metric: str | None = None,
    metrics: list[str] | str | None = None,
    limit: int = CONSUMER_TIMELINE_LIMIT,
) -> dict[str, Any]:
    filtered = filter_timeline_entries(entries, metric=metric, metrics=metrics)
    cap = max(1, int(limit or CONSUMER_TIMELINE_LIMIT))
    truncated = len(filtered) > cap
    projected = [project_consumer_timeline_entry(entry) for entry in filtered[:cap]]
    metric_list = [part.strip() for part in str(metrics or "").split(",") if part.strip()] if not isinstance(metrics, list) else [str(part).strip() for part in metrics if str(part).strip()]
    return {
        "entries": projected,
        "unified": bool(unified),
        "filter_metric": metric,
        "filter_metrics": metric_list,
        "truncated": truncated,
        "returned_count": len(projected),
        "matched_count": len(filtered),
    }


def build_timeline(
    store: VaultStore,
    *,
    category: str | None = None,
    newest_first: bool = True,
    date_from: str | None = None,
    date_to: str | None = None,
    severity: str | None = None,
    include_guardian_events: bool = False,
    include_hc_v6: bool = False,
    patient_id: str = "default-patient",
    measurements_by_document: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """
    Document timeline (HC-201 compatible by default).

    Pass include_guardian_events=True / include_hc_v6=True or use
    build_unified_timeline() for HC-301 merged views.
    """
    trends = store.get_trends(patient_id=patient_id)
    entries: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    # HC321-UAT12F: encrypted production indexes decrypt on every _read_index.
    # list_measurements(document_id=...) used to re-read the whole vault once per
    # document, which stalled the S24 Filtered Timeline fetch until the browser
    # reported TypeError: Failed to fetch. Batch the measurement load instead.
    if measurements_by_document is None:
        measurements_by_document = _measurements_by_document(store.list_measurements())
    for doc in store.list_documents():
        if doc.get("patient_id", "default-patient") != patient_id:
            continue
        if category and category != "all":
            primary = doc.get("primary_category")
            secondary = doc.get("secondary_categories") or []
            if primary != category and category not in secondary:
                continue
        measurements = list(measurements_by_document.get(str(doc["id"]), []))
        related = {}
        for m in measurements:
            metric = str(m.get("metric") or "").lower()
            if metric in trends:
                related[metric] = trends[metric]
        impact = "; ".join(f"{k}: {v.get('label')}" for k, v in related.items()) or (
            "No trend impact yet"
        )
        sort_date = timeline_sort_key(doc)
        if date_from and sort_date < date_from:
            continue
        if date_to and sort_date > date_to:
            continue
        dedupe = f"doc|{doc.get('id')}"
        if dedupe in seen_keys:
            continue
        seen_keys.add(dedupe)
        entries.append(
            {
                "date": sort_date,
                "measured_at": doc.get("measured_at"),
                "report_date": doc.get("report_date"),
                "imported_at": doc.get("imported_at"),
                "primary_category": doc.get("primary_category"),
                "category_label": doc.get("primary_category"),
                "group_id": doc.get("group_id"),
                "group_title": doc.get("group_title"),
                "sequence_number": doc.get("sequence_number"),
                "page_number": doc.get("page_number"),
                "document": doc,
                "measurements": measurements,
                "trend_impact": impact,
                "original_link": doc.get("storage_uri"),
                "entry_kind": "document",
                "provenance": doc.get("provenance") or doc.get("source_system"),
                "severity": None,
                "fhir_resources": {
                    "document": "DocumentReference",
                    "observations": "Observation",
                },
            }
        )

    if include_guardian_events:
        for ev in store.list_timeline_events():
            if ev.get("patient_id", "default-patient") != patient_id:
                continue
            if category and category != "all":
                if ev.get("category") != category and ev.get("kind") != category:
                    continue
            if severity and ev.get("severity") != severity:
                continue
            sort_date = str(ev.get("measured_at") or ev.get("imported_at") or "")
            if date_from and sort_date < date_from:
                continue
            if date_to and sort_date > date_to:
                continue
            dedupe = str(ev.get("dedupe_key") or ev.get("event_id") or "")
            if dedupe and dedupe in seen_keys:
                continue
            if dedupe:
                seen_keys.add(dedupe)
            entries.append(
                {
                    "date": sort_date,
                    "measured_at": ev.get("measured_at"),
                    "imported_at": ev.get("imported_at"),
                    "primary_category": ev.get("category"),
                    "category_label": ev.get("category"),
                    "entry_kind": ev.get("kind") or "guardian_event",
                    "provenance": ev.get("provenance"),
                    "severity": ev.get("severity"),
                    "summary": ev.get("summary"),
                    "payload": ev.get("payload") or {},
                    "document": None,
                    "measurements": [],
                    "trend_impact": ev.get("summary") or "",
                    "original_link": None,
                    "fhir_resources": {},
                }
            )

    if include_hc_v6:
        for row in _load_hc_v6_entries():
            if category and category not in ("all", None, row.get("primary_category"), "hc_v6"):
                continue
            sort_date = str(row.get("date") or "")
            if date_from and sort_date < date_from:
                continue
            if date_to and sort_date > date_to:
                continue
            dedupe = str(row.get("dedupe_key") or "")
            if dedupe and dedupe in seen_keys:
                continue
            if dedupe:
                seen_keys.add(dedupe)
            entries.append(row)

    # Group-aware sort: by group measured date, then sequence/page within group
    def _entry_key(e: dict[str, Any]) -> tuple:
        doc = e.get("document") if isinstance(e.get("document"), dict) else {}
        gid = doc.get("group_id") or doc.get("id") or e.get("entry_kind") or ""
        seq = doc.get("sequence_number") or doc.get("page_number") or 0
        return (str(e.get("date") or ""), str(gid), int(seq) if seq is not None else 0)

    entries.sort(key=_entry_key, reverse=newest_first)
    if newest_first:
        # Within same date+group, keep ascending page order
        entries.sort(
            key=lambda e: (
                str(e.get("date") or ""),
                str((e.get("document") or {}).get("group_id") if isinstance(e.get("document"), dict) else ""),
            ),
            reverse=True,
        )
        # stable secondary: sequence ascending within group
        buckets: dict[str, list[dict[str, Any]]] = {}
        order: list[str] = []
        for e in entries:
            doc = e.get("document") if isinstance(e.get("document"), dict) else {}
            gid = str(doc.get("group_id") or doc.get("id") or e.get("entry_kind") or e.get("date") or "x")
            if gid not in buckets:
                buckets[gid] = []
                order.append(gid)
            buckets[gid].append(e)
        rebuilt: list[dict[str, Any]] = []
        for gid in order:
            group = buckets[gid]
            group.sort(
                key=lambda e: int(
                    (
                        (e.get("document") or {}).get("sequence_number")
                        if isinstance(e.get("document"), dict)
                        else None
                    )
                    or (
                        (e.get("document") or {}).get("page_number")
                        if isinstance(e.get("document"), dict)
                        else None
                    )
                    or 0
                )
            )
            rebuilt.extend(group)
        entries = rebuilt
    return entries


def _load_hc_v6_entries() -> list[dict[str, Any]]:
    """
    Optional HC_V6 merge for server-side timeline.

    Browser localStorage HC_V6 is authoritative in the PWA. Server cannot read
    browser storage; returns empty unless a local sidecar file is present for tests.
    """
    try:
        import json
        from pathlib import Path

        side = Path(__file__).resolve().parents[2] / "vault_storage" / "hc_v6_sidecar.json"
        if not side.exists():
            return []
        raw = json.loads(side.read_text(encoding="utf-8"))
        logs = raw.get("logs") or []
        out: list[dict[str, Any]] = []
        for i, row in enumerate(logs):
            ts = str(row.get("ts") or row.get("measured_at") or "")
            dedupe = f"hc_v6|{ts}|{row.get('g')}|{row.get('sys')}|{row.get('dia')}"
            out.append(
                {
                    "date": ts,
                    "measured_at": ts,
                    "imported_at": ts,
                    "primary_category": "hc_v6",
                    "category_label": "hc_v6",
                    "entry_kind": "hc_v6_log",
                    "provenance": row.get("source") or "HC_V6",
                    "severity": None,
                    "summary": "HC_V6 reading",
                    "payload": row,
                    "document": None,
                    "measurements": [],
                    "trend_impact": "",
                    "original_link": None,
                    "fhir_resources": {},
                    "dedupe_key": dedupe,
                }
            )
        return out
    except Exception:
        return []


def _entry_metric_tokens(entry: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for measurement in entry.get("measurements") or []:
        mid = canonicalize_metric(measurement.get("metric") or measurement.get("metric_type") or "")
        if mid:
            tokens.add(mid)
            tokens.add(snapshot_metric_id(mid))
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    for raw in (
        entry.get("summary"),
        entry.get("trend_impact"),
        entry.get("primary_category"),
        payload.get("metric"),
        payload.get("metric_type"),
    ):
        text = str(raw or "").strip()
        if not text:
            continue
        tokens.add(canonicalize_metric(text))
        tokens.add(snapshot_metric_id(text))
    return {item for item in tokens if item}


def timeline_entry_matches_metric(
    entry: dict[str, Any],
    metric: str | None = None,
    metrics: list[str] | str | None = None,
) -> bool:
    want = metric_filter_aliases(metric, metrics)
    if not want:
        return True
    tokens = _entry_metric_tokens(entry)
    if tokens & want:
        return True
    related = entry.get("trend_impact") or ""
    blob = " ".join(
        [
            str(entry.get("summary") or ""),
            str(related),
            str(entry.get("primary_category") or ""),
        ]
    ).lower()
    for token in want:
        if token and token.replace("_", " ") in blob:
            return True
    return False


def filter_timeline_entries(
    entries: list[dict[str, Any]],
    metric: str | None = None,
    metrics: list[str] | str | None = None,
) -> list[dict[str, Any]]:
    want = metric_filter_aliases(metric, metrics)
    if not want:
        return list(entries or [])
    return [entry for entry in (entries or []) if timeline_entry_matches_metric(entry, metric, metrics)]


def build_unified_timeline(store: VaultStore, **kwargs: Any) -> list[dict[str, Any]]:
    """HC-301 merged timeline: vault docs + guardian events + optional HC_V6 sidecar."""
    kwargs.setdefault("include_guardian_events", True)
    kwargs.setdefault("include_hc_v6", True)
    return build_timeline(store, **kwargs)
