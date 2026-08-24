"""Built-in Health Vault parsers — auto-register on import."""

from __future__ import annotations

import json
import re
from typing import Any

from backend.health_vault.models import create_measurement
from backend.health_vault.parser_registry import DEFAULT_REGISTRY


def _json_from_text(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _blob(ctx: dict[str, Any]) -> str:
    return " ".join(
        [
            str(ctx.get("filename") or ""),
            str(ctx.get("document_type") or ""),
            str(ctx.get("text") or "")[:2000],
            str(ctx.get("source_system") or ""),
        ]
    ).lower()


def _from_mapping(obj: Any, document_id: str | None, confidence: float) -> list:
    if obj is None:
        return []
    if isinstance(obj, dict) and "measurements" in obj:
        obj = obj["measurements"]
    if isinstance(obj, dict) and "extracted_measurements" in obj:
        obj = obj["extracted_measurements"]
    out = []
    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict):
                out.append(
                    create_measurement(
                        document_id=document_id,
                        confidence=item.get("confidence", confidence),
                        **{k: v for k, v in item.items() if k != "confidence"},
                    )
                )
        return out
    if isinstance(obj, dict):
        skip = {"document", "interpretation", "confidence", "parser", "filename", "measured_at", "date"}
        measured_at = obj.get("measured_at") or obj.get("date")
        for key, val in obj.items():
            if key in skip or isinstance(val, (dict, list)):
                continue
            out.append(
                create_measurement(
                    document_id=document_id,
                    metric=key,
                    value=val,
                    confidence=confidence,
                    measured_at=measured_at,
                )
            )
    return out


class _Base:
    id = "base"
    name = "Base"
    version = "1.0.0"
    priority = 0
    supported_types: list[str] = []

    def can_parse(self, ctx: dict[str, Any]) -> bool:
        return False

    def parse(self, ctx: dict[str, Any]) -> dict[str, Any]:
        return {"measurements": [], "confidence": 0.0, "notes": []}


class SamsungHealthParser(_Base):
    id = "samsung_health_parser"
    name = "SamsungHealthParser"
    priority = 20
    supported_types = [
        "samsung_health_ecg",
        "samsung_health_sleep",
        "samsung_health_energy_score",
    ]

    def can_parse(self, ctx: dict[str, Any]) -> bool:
        b = _blob(ctx)
        return "samsung" in b or ctx.get("document_type") in self.supported_types

    def parse(self, ctx: dict[str, Any]) -> dict[str, Any]:
        data = ctx.get("json") or _json_from_text(ctx.get("text"))
        measurements = _from_mapping(data, ctx.get("document_id"), 0.65)
        dtype = ctx.get("document_type")
        if not measurements and dtype == "samsung_health_ecg":
            measurements = [
                create_measurement(
                    document_id=ctx.get("document_id"),
                    metric="ecg_result",
                    value="imported",
                    confidence=0.4,
                )
            ]
        return {
            "measurements": measurements,
            "confidence": 0.65 if measurements else 0.3,
            "notes": ["Samsung Health parser"],
        }


class GalaxyWatchParser(_Base):
    id = "galaxy_watch_parser"
    name = "GalaxyWatchParser"
    priority = 18
    supported_types = ["galaxy_watch_report"]

    def can_parse(self, ctx: dict[str, Any]) -> bool:
        b = _blob(ctx)
        return "galaxy" in b or "watch" in b or ctx.get("document_type") == "galaxy_watch_report"

    def parse(self, ctx: dict[str, Any]) -> dict[str, Any]:
        data = ctx.get("json") or _json_from_text(ctx.get("text"))
        measurements = _from_mapping(data, ctx.get("document_id"), 0.6)
        return {
            "measurements": measurements,
            "confidence": 0.6 if measurements else 0.3,
            "notes": ["Galaxy Watch parser"],
        }


class LifeLabsParser(_Base):
    id = "lifelabs_parser"
    name = "LifeLabsParser"
    priority = 25
    supported_types = ["laboratory_pdf"]

    def can_parse(self, ctx: dict[str, Any]) -> bool:
        b = _blob(ctx)
        return any(x in b for x in ("lifelabs", "lab", "laboratory")) or ctx.get(
            "document_type"
        ) == "laboratory_pdf"

    def parse(self, ctx: dict[str, Any]) -> dict[str, Any]:
        data = ctx.get("json") or _json_from_text(ctx.get("text"))
        measurements = _from_mapping(data, ctx.get("document_id"), 0.75)
        text = str(ctx.get("text") or "")
        for pattern, metric in (
            (r"eGFR[^0-9]*([0-9]+(?:\.[0-9]+)?)", "egfr"),
            (r"Creatinine[^0-9]*([0-9]+(?:\.[0-9]+)?)", "creatinine"),
            (r"HbA1c[^0-9]*([0-9]+(?:\.[0-9]+)?)", "hba1c"),
        ):
            m = re.search(pattern, text, re.I)
            if m:
                measurements.append(
                    create_measurement(
                        document_id=ctx.get("document_id"),
                        metric=metric,
                        value=float(m.group(1)),
                        confidence=0.55,
                    )
                )
        return {
            "measurements": measurements,
            "confidence": 0.7 if measurements else 0.25,
            "notes": ["LifeLabs parser"],
        }


class LibreParser(_Base):
    id = "libre_parser"
    name = "LibreParser"
    priority = 22
    supported_types = ["libre_cgm_report", "blood_glucose"]

    def can_parse(self, ctx: dict[str, Any]) -> bool:
        b = _blob(ctx)
        return any(x in b for x in ("libre", "cgm", "freestyle")) or ctx.get(
            "document_type"
        ) in self.supported_types

    def parse(self, ctx: dict[str, Any]) -> dict[str, Any]:
        data = ctx.get("json") or _json_from_text(ctx.get("text"))
        measurements = _from_mapping(data, ctx.get("document_id"), 0.7)
        return {
            "measurements": measurements,
            "confidence": 0.65 if measurements else 0.3,
            "notes": ["Libre parser"],
        }


class BloodPressureParser(_Base):
    id = "blood_pressure_parser"
    name = "BloodPressureParser"
    priority = 21
    supported_types = ["blood_pressure_screenshot"]

    def can_parse(self, ctx: dict[str, Any]) -> bool:
        b = _blob(ctx)
        return any(x in b for x in ("blood_pressure", "systolic", "diastolic")) or ctx.get(
            "document_type"
        ) == "blood_pressure_screenshot" or re.search(r"\bbp\b", b) is not None

    def parse(self, ctx: dict[str, Any]) -> dict[str, Any]:
        data = ctx.get("json") or _json_from_text(ctx.get("text"))
        measurements = _from_mapping(data, ctx.get("document_id"), 0.7)
        text = str(ctx.get("text") or ctx.get("filename") or "")
        m = re.search(r"(\d{2,3})\s*/\s*(\d{2,3})", text)
        if m:
            measurements.extend(
                [
                    create_measurement(
                        document_id=ctx.get("document_id"),
                        metric="systolic",
                        value=int(m.group(1)),
                        confidence=0.55,
                    ),
                    create_measurement(
                        document_id=ctx.get("document_id"),
                        metric="diastolic",
                        value=int(m.group(2)),
                        confidence=0.55,
                    ),
                ]
            )
        return {
            "measurements": measurements,
            "confidence": 0.6 if measurements else 0.25,
            "notes": ["Blood pressure parser"],
        }


class HospitalReportParser(_Base):
    id = "hospital_report_parser"
    name = "HospitalReportParser"
    priority = 10
    supported_types = ["hospital_report", "medication_report", "imaging_report", "unknown"]

    def can_parse(self, ctx: dict[str, Any]) -> bool:
        b = _blob(ctx)
        return any(x in b for x in ("hospital", "clinic", "discharge", "imaging", "medication")) or ctx.get(
            "document_type"
        ) in self.supported_types

    def parse(self, ctx: dict[str, Any]) -> dict[str, Any]:
        data = ctx.get("json") or _json_from_text(ctx.get("text"))
        measurements = _from_mapping(data, ctx.get("document_id"), 0.5)
        return {
            "measurements": measurements,
            "confidence": 0.5 if measurements else 0.2,
            "notes": ["Hospital report parser"],
        }


class AIAssistedParser(_Base):
    id = "ai_assisted_parser"
    name = "AIAssistedParser"
    priority = 100
    supported_types = ["ai_assisted_import"]

    def can_parse(self, ctx: dict[str, Any]) -> bool:
        return (
            ctx.get("acquisition_method") == "external_ai"
            or ctx.get("document_type") == "ai_assisted_import"
            or bool(ctx.get("extracted_measurements"))
        )

    def parse(self, ctx: dict[str, Any]) -> dict[str, Any]:
        confidence = float(ctx.get("confidence") if ctx.get("confidence") is not None else 0.85)
        measurements = []
        for item in ctx.get("extracted_measurements") or []:
            if isinstance(item, dict):
                measurements.append(
                    create_measurement(
                        document_id=ctx.get("document_id"),
                        confidence=item.get("confidence", confidence),
                        **{k: v for k, v in item.items() if k != "confidence"},
                    )
                )
        return {
            "measurements": measurements,
            "confidence": confidence,
            "notes": ["External AI extraction accepted as-is"],
        }


class GenericJsonParser(_Base):
    """Fallback for application/json / json_measurements when no specialty parser matches."""

    id = "generic_json_parser"
    name = "GenericJsonParser"
    priority = 5
    supported_types = ["json_measurements"]

    def can_parse(self, ctx: dict[str, Any]) -> bool:
        mime = str(ctx.get("mime_type") or "").lower()
        name = str(ctx.get("filename") or "").lower()
        return (
            ctx.get("document_type") == "json_measurements"
            or "json" in mime
            or name.endswith(".json")
            or ctx.get("json") is not None
        )

    def parse(self, ctx: dict[str, Any]) -> dict[str, Any]:
        data = ctx.get("json") or _json_from_text(ctx.get("text"))
        measurements = _from_mapping(data, ctx.get("document_id"), 0.7)
        return {
            "measurements": measurements,
            "confidence": 0.7 if measurements else 0.2,
            "notes": ["Generic JSON measurement parser"],
        }


def register_builtin_parsers(registry=None) -> None:
    from backend.health_vault.parsers.clinical_lab import ClinicalLabPanelParser

    reg = registry or DEFAULT_REGISTRY
    for cls in (
        ClinicalLabPanelParser,
        SamsungHealthParser,
        GalaxyWatchParser,
        LifeLabsParser,
        LibreParser,
        BloodPressureParser,
        HospitalReportParser,
        AIAssistedParser,
        GenericJsonParser,
    ):
        reg.register(cls())


# Auto-register on module import
register_builtin_parsers()
