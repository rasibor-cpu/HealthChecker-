"""
HC-201H — Measured-date extraction with explicit priority and confidence.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from backend.health_vault.models import utc_now

_ISO_RE = re.compile(
    r"\b(20\d{2}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?)\b"
)
_FILENAME_DATE_RE = re.compile(
    r"(20\d{2})[_\-]?(\d{2})[_\-]?(\d{2})|(?:^|[_\-])(\d{2})(\d{2})(\d{2})(?:[_\-]|$)"
)


def _parse_candidate(value: str | None) -> str | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    # Normalize space to T for fromisoformat comfort
    try:
        cleaned = text.replace("Z", "+00:00")
        if " " in cleaned and "T" not in cleaned:
            cleaned = cleaned.replace(" ", "T", 1)
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except Exception:
        pass
    m = _ISO_RE.search(text)
    if m:
        return _parse_candidate(m.group(1))
    return None


def extract_measured_date(
    *,
    explicit_measured_at: str | None = None,
    report_date: str | None = None,
    parser_date: str | None = None,
    source_metadata_date: str | None = None,
    exif_capture_date: str | None = None,
    filename: str | None = None,
    imported_at: str | None = None,
    measurement_dates: list[str | None] | None = None,
) -> dict[str, Any]:
    """
    Priority:
    1 explicit measurement/report date
    2 parser-extracted date
    3 trusted source metadata
    4 EXIF capture date
    5 filename date
    6 upload/import time fallback
    """
    original_extracted_text: str | None = None

    # Prefer earliest explicit measurement date when multiple present
    meas_dates = [_parse_candidate(d) for d in (measurement_dates or []) if d]
    meas_dates = [d for d in meas_dates if d]

    candidates: list[tuple[str, str, float]] = []
    for label, raw, conf in (
        ("explicit_measured_at", explicit_measured_at, 0.95),
        ("measurement_value", meas_dates[0] if meas_dates else None, 0.92),
        ("report_date", report_date, 0.9),
        ("parser_date", parser_date, 0.85),
        ("source_metadata", source_metadata_date, 0.8),
        ("exif_capture_date", exif_capture_date, 0.7),
    ):
        parsed = _parse_candidate(raw) if isinstance(raw, str) else raw
        if parsed:
            candidates.append((label, parsed, conf))
            if original_extracted_text is None and isinstance(raw, str):
                original_extracted_text = raw

    if not candidates and filename:
        original_extracted_text = filename
        fd = _filename_date(filename)
        if fd:
            candidates.append(("filename_date", fd, 0.55))

    if candidates:
        # Highest confidence first; ties keep first in priority list
        source, measured_at, confidence = max(candidates, key=lambda c: c[2])
        requires_review = confidence < 0.7
        return {
            "measured_at": measured_at,
            "report_date": _parse_candidate(report_date) or (
                measured_at if source == "report_date" else None
            ),
            "imported_at": imported_at or utc_now(),
            "file_capture_date": _parse_candidate(exif_capture_date),
            "date_confidence": confidence,
            "date_source": source,
            "requires_review": requires_review,
            "original_date_text": original_extracted_text,
        }

    fallback = imported_at or utc_now()
    return {
        "measured_at": fallback,
        "report_date": _parse_candidate(report_date),
        "imported_at": fallback,
        "file_capture_date": _parse_candidate(exif_capture_date),
        "date_confidence": 0.25,
        "date_source": "imported_at_fallback",
        "requires_review": True,
        "original_date_text": original_extracted_text,
    }


def _filename_date(filename: str) -> str | None:
    m = _FILENAME_DATE_RE.search(filename)
    if not m:
        return None
    if m.group(1):
        y, mo, d = m.group(1), m.group(2), m.group(3)
    else:
        # MMDDYY ambiguous — treat as YYMMDD only when year-like 20xx not present;
        # use 20YY-MM-DD from groups 4,5,6 as MM DD YY → prefer YY as year if > 50 else 20YY
        a, b, c = m.group(4), m.group(5), m.group(6)
        # Prefer YYYY from adjacent ISO if any; else assume YYMMDD when a is 20-29
        if a and int(a) >= 20 and int(a) <= 29:
            y, mo, d = f"20{a}", b, c
        else:
            # MM/DD/YY
            y, mo, d = f"20{c}", a, b
    try:
        dt = datetime(int(y), int(mo), int(d), tzinfo=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def timeline_sort_key(doc: dict[str, Any]) -> str:
    """measured_at > report_date > imported_at."""
    return str(
        doc.get("measured_at")
        or doc.get("report_date")
        or doc.get("imported_at")
        or ""
    )
