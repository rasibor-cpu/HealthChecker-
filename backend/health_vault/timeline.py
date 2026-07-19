"""Chronological health timeline builder."""

from __future__ import annotations

from typing import Any

from backend.health_vault.vault_store import VaultStore


def build_timeline(store: VaultStore) -> list[dict[str, Any]]:
    trends = store.get_trends()
    entries: list[dict[str, Any]] = []
    for doc in store.list_documents():
        measurements = store.list_measurements(document_id=doc["id"])
        related = {}
        for m in measurements:
            metric = str(m.get("metric") or "").lower()
            if metric in trends:
                related[metric] = trends[metric]
        impact = "; ".join(
            f"{k}: {v.get('label')}" for k, v in related.items()
        ) or "No trend impact yet"
        entries.append(
            {
                "date": doc.get("measured_at") or doc.get("imported_at"),
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
    entries.sort(key=lambda e: str(e.get("date") or ""), reverse=True)
    return entries
