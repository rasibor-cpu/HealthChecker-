"""OCR-text parsers for glucose-meter, CGM and wearable screenshots (HC-359).

Only explicitly labelled numeric values are extracted. Ambiguous or conflicting
readings yield no measurement and a review-required note. Trend arrows, rates
and diagnoses (e.g. AFib/ECG interpretation) are never inferred.
"""
from __future__ import annotations

import re
from typing import Any

from backend.health_vault.models import create_measurement
from backend.health_vault.parsers import _Base, _blob

_NUM = r"(\d{1,3}(?:[.,]\d{1,2})?)"
_GLUCOSE_RE = re.compile(
    r"(?:glucose|blood\s*sugar|\bbg\b)\D{0,12}?" + _NUM + r"\s*(mg\s*/\s*dl|mmol\s*/\s*l)", re.I
)
_CGM_HINTS = ("cgm", "libre", "dexcom", "sensor glucose", "continuous glucose")
_HR_RE = re.compile(r"(?:heart\s*rate|pulse|\bhr\b)\D{0,12}?(\d{2,3})\s*(?:bpm)?", re.I)
_SPO2_RE = re.compile(r"(?:spo\s*2|oxygen\s*saturation)\D{0,12}?(\d{2,3})\s*%", re.I)
_TS_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})\b")


def _text(ctx: dict[str, Any]) -> str:
    return str(ctx.get("text") or "")


def _is_json(ctx: dict[str, Any]) -> bool:
    return bool(ctx.get("json")) or _text(ctx).lstrip().startswith(("{", "["))


def _glucose_readings(text: str) -> list[tuple[float, str]]:
    out = []
    for m in _GLUCOSE_RE.finditer(text):
        unit = "mmol/L" if "mmol" in m.group(2).lower() else "mg/dL"
        out.append((float(m.group(1).replace(",", ".")), unit))
    return out


def _review(note: str, why: str) -> dict[str, Any]:
    return {"measurements": [], "confidence": 0.1, "notes": [note, why], "requires_review": True}


def _glucose_result(ctx, readings, note, timestamp=None) -> dict[str, Any]:
    distinct = set(readings)
    if not distinct:
        return _review(note, "No explicit glucose reading found")
    if len(distinct) != 1:
        return _review(note, "Conflicting glucose readings; review required")
    value, unit = readings[0]
    lo, hi = (1.0, 40.0) if unit == "mmol/L" else (20.0, 800.0)
    if not lo <= value <= hi:
        return _review(note, "Glucose value out of plausible range; review required")
    m = create_measurement(
        document_id=ctx.get("document_id"), metric="glucose", value=value, units=unit,
        confidence=0.6, measured_at=timestamp,
    )
    return {"measurements": [m], "confidence": 0.6, "notes": [note]}


class GlucoseMeterScreenshotParser(_Base):
    id = "glucose_meter_screenshot_parser"
    name = "GlucoseMeterScreenshotParser"
    priority = 20
    supported_types = ["glucose_meter_screenshot"]

    def can_parse(self, ctx: dict[str, Any]) -> bool:
        if _is_json(ctx):
            return False
        return bool(_glucose_readings(_text(ctx))) and not any(h in _blob(ctx) for h in _CGM_HINTS)

    def parse(self, ctx: dict[str, Any]) -> dict[str, Any]:
        return _glucose_result(ctx, _glucose_readings(_text(ctx)), "Glucose meter screenshot parser")


class CgmScreenshotParser(_Base):
    id = "cgm_screenshot_parser"
    name = "CgmScreenshotParser"
    priority = 24
    supported_types = ["cgm_screenshot"]

    def can_parse(self, ctx: dict[str, Any]) -> bool:
        if _is_json(ctx):
            return False
        return any(h in _blob(ctx) for h in _CGM_HINTS) and bool(_glucose_readings(_text(ctx)))

    def parse(self, ctx: dict[str, Any]) -> dict[str, Any]:
        text = _text(ctx)
        stamps = set(_TS_RE.findall(text))
        ts = None
        if len(stamps) == 1:
            d, t = next(iter(stamps))
            ts = f"{d}T{t}:00"
        return _glucose_result(ctx, _glucose_readings(text), "CGM screenshot parser", ts)


class WearableScreenshotParser(_Base):
    id = "wearable_screenshot_parser"
    name = "WearableScreenshotParser"
    # Above SamsungHealthParser (20) so explicitly labelled readings beat its
    # contentless "ecg_result" placeholder; below BloodPressureParser (21).
    priority = 20.5
    supported_types = ["wearable_screenshot"]

    def can_parse(self, ctx: dict[str, Any]) -> bool:
        if _is_json(ctx):
            return False
        t = _text(ctx)
        return bool(_HR_RE.search(t) or _SPO2_RE.search(t))

    def parse(self, ctx: dict[str, Any]) -> dict[str, Any]:
        text = _text(ctx)
        note = "Wearable screenshot parser"
        measurements = []
        for rx, metric, units, lo, hi in (
            (_HR_RE, "heart_rate", "bpm", 25, 250),
            (_SPO2_RE, "spo2", "%", 50, 100),
        ):
            vals = {int(v) for v in rx.findall(text)}
            if not vals:
                continue
            if len(vals) != 1 or not lo <= next(iter(vals)) <= hi:
                # Fail closed for the whole document: a partial extract would
                # silently omit the disputed reading from the preview.
                return _review(note, f"Ambiguous or implausible {metric} reading; review required")
            measurements.append(create_measurement(
                document_id=ctx.get("document_id"), metric=metric, value=vals.pop(), units=units, confidence=0.55,
            ))
        return {"measurements": measurements, "confidence": 0.55 if measurements else 0.1, "notes": [note]}
