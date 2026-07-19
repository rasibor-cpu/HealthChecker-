"""Chronological health timeline builder (HC-201H measured-date priority)."""

from __future__ import annotations

from typing import Any

from backend.health_vault.date_extraction import timeline_sort_key
from backend.health_vault.vault_store import VaultStore


def build_timeline(
    store: VaultStore,
    *,
    category: str | None = None,
    newest_first: bool = True,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    trends = store.get_trends()
    entries: list[dict[str, Any]] = []
    for doc in store.list_documents():
        if category and category != "all":
            primary = doc.get("primary_category")
            secondary = doc.get("secondary_categories") or []
            if primary != category and category not in secondary:
                continue
        measurements = store.list_measurements(document_id=doc["id"])
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
                "fhir_resources": {
                    "document": "DocumentReference",
                    "observations": "Observation",
                },
            }
        )

    # Group-aware sort: by group measured date, then sequence/page within group
    def _entry_key(e: dict[str, Any]) -> tuple:
        doc = e.get("document") or {}
        gid = doc.get("group_id") or doc.get("id") or ""
        seq = doc.get("sequence_number") or doc.get("page_number") or 0
        return (str(e.get("date") or ""), str(gid), int(seq) if seq is not None else 0)

    entries.sort(key=_entry_key, reverse=newest_first)
    if newest_first:
        # Within same date+group, keep ascending page order
        entries.sort(
            key=lambda e: (
                str(e.get("date") or ""),
                str((e.get("document") or {}).get("group_id") or ""),
            ),
            reverse=True,
        )
        # stable secondary: sequence ascending within group
        buckets: dict[str, list[dict[str, Any]]] = {}
        order: list[str] = []
        for e in entries:
            gid = str((e.get("document") or {}).get("group_id") or e.get("document", {}).get("id"))
            if gid not in buckets:
                buckets[gid] = []
                order.append(gid)
            buckets[gid].append(e)
        rebuilt: list[dict[str, Any]] = []
        for gid in order:
            group = buckets[gid]
            group.sort(
                key=lambda e: int(
                    (e.get("document") or {}).get("sequence_number")
                    or (e.get("document") or {}).get("page_number")
                    or 0
                )
            )
            rebuilt.extend(group)
        entries = rebuilt
    return entries
