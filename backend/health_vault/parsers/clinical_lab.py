"""Structured clinical laboratory panel parser (hospital / ER JSON reports)."""

from __future__ import annotations

from typing import Any

from backend.health_vault.models import create_measurement


def _json_from_text(text: str | None) -> Any:
    if not text:
        return None
    try:
        import json

        return json.loads(text)
    except Exception:
        return None


class ClinicalLabPanelParser:
    id = "clinical_lab_panel_parser"
    name = "ClinicalLabPanelParser"
    version = "hc321uat12j.v1"
    priority = 40
    supported_types = ["hospital_report", "laboratory_pdf", "json_measurements"]

    def can_parse(self, ctx: dict[str, Any]) -> bool:
        data = ctx.get("json") or _json_from_text(ctx.get("text"))
        if isinstance(data, dict) and (
            data.get("analytes")
            or data.get("results")
            or data.get("lab_results")
            or str(data.get("report_kind") or "").lower() in {"laboratory", "er_labs", "hospital_lab"}
        ):
            return True
        blob = " ".join(
            str(part or "")
            for part in (ctx.get("filename"), ctx.get("document_type"), ctx.get("source_system"))
        ).lower()
        return any(token in blob for token in ("brantford", "er_lab", "lab_panel", "clinical_lab"))

    def parse(self, ctx: dict[str, Any]) -> dict[str, Any]:
        data = ctx.get("json") or _json_from_text(ctx.get("text")) or {}
        if not isinstance(data, dict):
            data = {}
        facility = (
            data.get("source_facility")
            or data.get("facility")
            or ctx.get("source_facility")
        )
        collected = (
            data.get("collected_at")
            or data.get("collection_timestamp")
            or data.get("measured_at")
            or ctx.get("measured_at")
        )
        default_specimen = data.get("specimen")
        default_context = data.get("context") or data.get("collection_context")
        rows = data.get("analytes") or data.get("results") or data.get("lab_results") or []
        measurements = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            metric = row.get("metric") or row.get("analyte_id") or row.get("code")
            name = row.get("original_analyte_name") or row.get("analyte") or row.get("name") or metric
            measurements.append(
                create_measurement(
                    document_id=ctx.get("document_id"),
                    metric=metric or name,
                    value=row.get("value"),
                    units=row.get("units") or row.get("unit"),
                    reference_range=row.get("reference_range") or row.get("ref_range"),
                    abnormal_flag=row.get("abnormal_flag") or row.get("flag"),
                    confidence=row.get("confidence", 0.86),
                    measured_at=row.get("measured_at") or row.get("collected_at") or collected,
                    original_analyte_name=name,
                    specimen=row.get("specimen") or default_specimen,
                    context=row.get("context") or default_context,
                    source_facility=row.get("source_facility") or facility,
                    provenance=row.get("provenance") or data.get("provenance") or "original_document_verified",
                )
            )
        notes = ["Clinical laboratory panel parser"]
        if facility:
            notes.append(f"facility:{facility}")
        return {
            "measurements": measurements,
            "confidence": 0.86 if measurements else 0.2,
            "notes": notes,
            "measured_at": collected,
            "report_date": data.get("report_date") or collected,
        }
