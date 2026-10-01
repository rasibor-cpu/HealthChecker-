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
NON_MEASUREMENT_DATE_SOURCES = frozenset(
    {
        "report_date",
        "source_metadata",
        "exif_capture_date",
        "filename_date",
        "source_document_date_only",
        "imported_at_fallback",
    }
)


def clinical_observation_timestamp(
    measurement: dict[str, Any],
    document: dict[str, Any] | None = None,
) -> str | None:
    """Return a trustworthy observation timestamp, excluding legacy fallbacks."""
    document = document or {}
    measured_at = measurement.get("measured_at")
    document_measured_at = document.get("measured_at")
    date_source = str(document.get("date_source") or "")
    if (
        date_source in NON_MEASUREMENT_DATE_SOURCES
        and measured_at == document_measured_at
    ):
        return None
    if measured_at:
        return str(measured_at)
    if date_source in NON_MEASUREMENT_DATE_SOURCES:
        return None
    return str(document_measured_at) if document_measured_at else None


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
    Only observation dates can populate ``measured_at``. Document dates and
    receipt time remain separate so neither can make an old observation current.
    """
    original_extracted_text: str | None = None

    # A document containing one measurement can inherit that measurement date.
    # A multi-date bundle is a longitudinal package, so do not mislabel the
    # whole document with whichever historical row happens to appear first.
    raw_meas_dates = [d for d in (measurement_dates or []) if d]
    meas_dates = [_parse_candidate(d) for d in raw_meas_dates]
    meas_dates = [d for d in meas_dates if d]
    unique_meas_dates = sorted(set(meas_dates))
    all_measurements_dated = bool(raw_meas_dates) and len(meas_dates) == len(raw_meas_dates)

    candidates: list[tuple[str, str, float]] = []
    for label, raw, conf in (
        ("explicit_measured_at", explicit_measured_at, 0.95),
        ("measurement_value", unique_meas_dates[0] if len(unique_meas_dates) == 1 else None, 0.92),
        ("parser_date", parser_date, 0.85),
    ):
        parsed = _parse_candidate(raw) if isinstance(raw, str) else raw
        if parsed:
            candidates.append((label, parsed, conf))
            if original_extracted_text is None and isinstance(raw, str):
                original_extracted_text = raw

    source_document_candidates: list[tuple[str, str]] = []
    for label, raw in (
        ("report_date", report_date),
        ("source_metadata", source_metadata_date),
        ("exif_capture_date", exif_capture_date),
    ):
        parsed = _parse_candidate(raw) if isinstance(raw, str) else raw
        if parsed:
            source_document_candidates.append((label, parsed))
            if original_extracted_text is None and isinstance(raw, str):
                original_extracted_text = raw
    if filename:
        filename_date = _filename_date(filename)
        if filename_date:
            source_document_candidates.append(("filename_date", filename_date))
            if original_extracted_text is None:
                original_extracted_text = filename

    source_document_date_source, source_document_date = (
        source_document_candidates[0]
        if source_document_candidates
        else (None, None)
    )
    if candidates:
        # Highest confidence first; ties keep first in priority list
        source, measured_at, confidence = max(candidates, key=lambda c: c[2])
        requires_review = confidence < 0.7
        return {
            "measured_at": measured_at,
            "report_date": _parse_candidate(report_date),
            "source_document_date": source_document_date,
            "source_document_date_source": source_document_date_source,
            "imported_at": imported_at or utc_now(),
            "file_capture_date": _parse_candidate(exif_capture_date),
            "date_confidence": confidence,
            "date_source": source,
            "requires_review": requires_review,
            "original_date_text": original_extracted_text,
        }

    if len(unique_meas_dates) > 1:
        return {
            "measured_at": None,
            "report_date": _parse_candidate(report_date),
            "source_document_date": source_document_date,
            "source_document_date_source": source_document_date_source,
            "imported_at": imported_at or utc_now(),
            "file_capture_date": _parse_candidate(exif_capture_date),
            "date_confidence": 0.92,
            "date_source": "measurement_values_multiple_dates",
            "requires_review": not all_measurements_dated,
            "original_date_text": original_extracted_text,
        }

    receipt_time = imported_at or utc_now()
    return {
        "measured_at": None,
        "report_date": _parse_candidate(report_date),
        "source_document_date": source_document_date,
        "source_document_date_source": source_document_date_source,
        "imported_at": receipt_time,
        "file_capture_date": _parse_candidate(exif_capture_date),
        "date_confidence": 0.25,
        "date_source": (
            "source_document_date_only"
            if source_document_date
            else "imported_at_fallback"
        ),
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


def timeline_sort_key(doc: dict[str, Any] | None) -> str:
    """Sort by observation, document, then receipt date."""
    if not isinstance(doc, dict):
        return ""
    return str(
        doc.get("measured_at")
        or doc.get("report_date")
        or doc.get("source_document_date")
        or doc.get("imported_at")
        or ""
    )
