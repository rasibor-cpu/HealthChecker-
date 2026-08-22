"""
HC-321 Health Snapshot — latest-valid observations and consumer statuses.

Observational decision-support only. Does not diagnose or prescribe.
UI components must consume the normalized statuses produced here; they must not
embed medical thresholds.

Thresholds:
- Scalar clinical bands: ``ClinicalRulesEngine`` / ``config/clinical_rules.json``
- Context-aware evaluators (glucose fasting vs post-meal, activity heart rate,
  adult sleep duration, informational metrics): this module, documented in
  ``config/health_snapshot.json``
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.health_vault.clinical_rules import (
    FLAG_ABNORMAL,
    FLAG_BORDERLINE,
    FLAG_CRITICAL,
    FLAG_NORMAL,
    FLAG_UNKNOWN,
    ClinicalRulesEngine,
)
from backend.health_vault.metric_normalization import canonicalize_metric
from backend.health_vault.models import utc_now
from backend.health_vault.trend_engine import HIGHER_BETTER, LOWER_BETTER, TrendEngine

_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "health_snapshot.json"

STATUS_NORMAL = "NORMAL"
STATUS_CAUTION = "CAUTION"
STATUS_ATTENTION = "ATTENTION"
STATUS_UNKNOWN = "UNKNOWN"

COLOR_GREEN = "GREEN"
COLOR_AMBER = "AMBER"
COLOR_RED = "RED"
COLOR_GREY = "GREY"

CONSUMER_STATUSES = (STATUS_NORMAL, STATUS_CAUTION, STATUS_ATTENTION, STATUS_UNKNOWN)

STATUS_TO_COLOR = {
    STATUS_NORMAL: COLOR_GREEN,
    STATUS_CAUTION: COLOR_AMBER,
    STATUS_ATTENTION: COLOR_RED,
    STATUS_UNKNOWN: COLOR_GREY,
}

STATUS_TEXT = {
    STATUS_NORMAL: "Normal",
    STATUS_CAUTION: "Caution",
    STATUS_ATTENTION: "Attention",
    STATUS_UNKNOWN: "Unknown",
}

FLAG_TO_STATUS = {
    FLAG_NORMAL: STATUS_NORMAL,
    FLAG_BORDERLINE: STATUS_CAUTION,
    FLAG_ABNORMAL: STATUS_ATTENTION,
    FLAG_CRITICAL: STATUS_ATTENTION,
    FLAG_UNKNOWN: STATUS_UNKNOWN,
}

CURRENTNESS_CURRENT = "current"
CURRENTNESS_STALE = "stale"
CURRENTNESS_MISSING = "missing"
CURRENTNESS_INVALID = "invalid"

FRESHNESS_FRESH = "fresh"
FRESHNESS_AGING = "aging"
FRESHNESS_STALE = "stale"
FRESHNESS_MISSING = "missing"

POST_MEAL_TOKENS = ("post_meal", "postprandial", "after_meal", "after meal", "ppg", "nonfasting")
FASTING_TOKENS = ("fasting", "fasted", "pre-meal", "premeal")
ACTIVITY_TOKENS = ("exercise", "workout", "activity", "walking", "running", "training")

DISCLAIMER = (
    "Observational decision-support only. Not a diagnosis or prescription. "
    "Status colours summarise the latest valid HealthChecker data and do not "
    "replace professional medical assessment."
)

# Snapshot consumer cards: map semantic duplicates onto one preferred metric_id.
# Underlying observations remain stored under their original canonical names.
SNAPSHOT_METRIC_DEDUPE = {
    "exercise_minutes": "activity_minutes",
}


def load_health_snapshot_config(path: Path | None = None) -> dict[str, Any]:
    p = path or _CONFIG_PATH
    if not p.exists():
        return {
            "schema_version": "hc.health_snapshot.v1",
            "metrics": {},
            "default_card_order": [],
            "freshness_windows_minutes": {"default": 10080},
            "stale_escalation_multiplier": 3,
            "disclaimer": DISCLAIMER,
        }
    return json.loads(p.read_text(encoding="utf-8"))


def parse_iso(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        if " " in text and "T" not in text:
            text = text.replace(" ", "T", 1)
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def as_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num or num in (float("inf"), float("-inf")):
        return None
    return num


def observation_metric(row: dict[str, Any]) -> str:
    return canonicalize_metric(row.get("metric_type") or row.get("metric") or "")


def snapshot_metric_id(metric: str | None) -> str:
    """Canonical metric identity for Snapshot cards (includes semantic dedupe)."""
    canonical = canonicalize_metric(metric)
    return SNAPSHOT_METRIC_DEDUPE.get(canonical, canonical)


def metric_filter_aliases(
    metric: str | None = None,
    metrics: list[str] | str | None = None,
) -> set[str]:
    """Expand a Snapshot/consumer metric filter into canonical + alias tokens."""
    raw: list[str] = []
    if metric:
        raw.append(str(metric))
    if isinstance(metrics, str):
        raw.append(metrics)
    elif metrics:
        raw.extend(str(item) for item in metrics)
    want: set[str] = set()
    for item in raw:
        for part in str(item or "").split(","):
            token = part.strip().lower()
            if not token:
                continue
            canon = canonicalize_metric(token)
            snap = snapshot_metric_id(token)
            want.update({token, canon, snap, token.replace(" ", "_"), token.replace("_", " ")})
            if snap == "activity_minutes" or canon in {"activity_minutes", "exercise_minutes"}:
                want.update({"activity_minutes", "exercise_minutes"})
            if snap == "blood_pressure" or canon in {"systolic_bp", "diastolic_bp"}:
                want.update({"blood_pressure", "systolic_bp", "diastolic_bp", "systolic", "diastolic"})
            if canon == "heart_rate":
                want.update({"heart_rate", "pulse", "hr"})
    return {item for item in want if item}


def _normalize_identity_timestamp(value: Any) -> str:
    measured = parse_iso(value)
    if measured is None:
        text = str(value or "").strip()
        return text[:19] if text else ""
    return measured.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fallback_observation_identity(row: dict[str, Any] | None) -> str:
    """Conservative display identity when fingerprint / observation_id are absent."""
    row = row or {}
    metric = observation_metric(row)
    num = as_number(row.get("value"))
    value_key = str(num if num is not None else (row.get("value") if row.get("value") is not None else ""))
    units = str(row.get("units") or row.get("unit") or "").strip().lower()
    ts = _normalize_identity_timestamp(row.get("measured_at"))
    return f"fallback:{metric}|{value_key}|{ts}|{units}"


def observation_display_identity(row: dict[str, Any] | None) -> str:
    """Canonical observation identity for Snapshot history display dedupe.

    Prefers stored fingerprint, then observation_id. Falls back to a conservative
    metric|value|timestamp|units fingerprint only when stronger identifiers are
    missing. Never mutates stored clinical observations.
    """
    row = row or {}
    fingerprint = str(row.get("fingerprint") or "").strip()
    if fingerprint:
        return f"fp:{fingerprint}"
    observation_id = str(row.get("observation_id") or row.get("id") or "").strip()
    if observation_id:
        return f"id:{observation_id}"
    return fallback_observation_identity(row)


def dedupe_observation_history(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Collapse duplicate display rows without deleting stored observations."""
    seen_fp: set[str] = set()
    seen_id: set[str] = set()
    seen_fallback: set[str] = set()
    unique: list[dict[str, Any]] = []
    for row in rows or []:
        fingerprint = str(row.get("fingerprint") or "").strip()
        observation_id = str(row.get("observation_id") or row.get("id") or "").strip()
        fallback = fallback_observation_identity(row)
        if fingerprint and fingerprint in seen_fp:
            continue
        if observation_id and observation_id in seen_id:
            continue
        if fallback in seen_fallback:
            continue
        if fingerprint:
            seen_fp.add(fingerprint)
        if observation_id:
            seen_id.add(observation_id)
        seen_fallback.add(fallback)
        unique.append(row)
    return unique


def observation_context(row: dict[str, Any]) -> str:
    parts = [
        str(row.get("context") or ""),
        str(row.get("tag") or ""),
        str(row.get("notes") or ""),
        " ".join(str(t) for t in (row.get("tags") or [])),
    ]
    return " ".join(parts).lower()


def _spec_for(rules: ClinicalRulesEngine, metric: str) -> dict[str, Any] | None:
    metrics = (rules.rules.get("metrics") or {}) if rules else {}
    return metrics.get(metric) or metrics.get(canonicalize_metric(metric))


def is_impossible_value(rules: ClinicalRulesEngine, metric: str, value: float) -> bool:
    spec = _spec_for(rules, metric)
    if not spec:
        return False
    if spec.get("impossible_below") is not None and value < float(spec["impossible_below"]):
        return True
    if spec.get("impossible_above") is not None and value > float(spec["impossible_above"]):
        return True
    return False


def is_valid_observation(
    row: dict[str, Any] | None,
    *,
    rules: ClinicalRulesEngine | None = None,
    require_numeric: bool = True,
) -> bool:
    """Return True when a row may represent the latest valid observation."""
    if not row:
        return False
    if str(row.get("acquisition_mode") or "").upper() == "SIMULATED_TEST_ONLY":
        return False
    if row.get("invalid") or row.get("is_invalid"):
        return False
    quality = row.get("quality") or {}
    if quality.get("invalid") is True:
        return False
    if quality.get("unit_compatible") is False or row.get("unit_compatible") is False:
        return False
    if not parse_iso(row.get("measured_at")):
        return False
    value = row.get("value")
    if value is None or value == "":
        return False
    if require_numeric:
        num = as_number(value)
        if num is None:
            return False
        metric = observation_metric(row)
        if rules and is_impossible_value(rules, metric, num):
            return False
    return True


def select_latest_valid(
    observations: list[dict[str, Any]] | None,
    *,
    metric: str | None = None,
    rules: ClinicalRulesEngine | None = None,
    require_numeric: bool = True,
) -> dict[str, Any] | None:
    """
    Choose the newest valid observation.

    A newer invalid row is ignored so an older valid row can still be selected.
    Ordering uses timezone-aware ``measured_at`` (not list order).
    """
    rows = list(observations or [])
    want = canonicalize_metric(metric) if metric else None
    eligible: list[tuple[datetime, int, dict[str, Any]]] = []
    for idx, row in enumerate(rows):
        if want and observation_metric(row) != want:
            continue
        if not is_valid_observation(row, rules=rules, require_numeric=require_numeric):
            continue
        measured = parse_iso(row.get("measured_at"))
        if measured is None:
            continue
        eligible.append((measured, idx, row))
    if not eligible:
        return None
    eligible.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return eligible[0][2]


def compute_freshness(
    *,
    metric: str,
    measured_at: str | None,
    now: datetime | None = None,
    windows: dict[str, Any] | None = None,
    stale_multiplier: float = 3.0,
) -> dict[str, Any]:
    """Age + fresh/aging/stale/missing. Does not invent a clinical status."""
    as_of = now or datetime.now(timezone.utc)
    measured = parse_iso(measured_at)
    if measured is None:
        return {
            "freshness_status": FRESHNESS_MISSING,
            "currentness": CURRENTNESS_MISSING,
            "age_seconds": None,
            "age_minutes": None,
            "label": "No observation time",
        }
    age_sec = max(0.0, (as_of - measured).total_seconds())
    age_min = age_sec / 60.0
    windows = windows or {}
    canonical = canonicalize_metric(metric)
    fresh_window = float(
        windows.get(canonical)
        or windows.get(metric)
        or windows.get("default")
        or 10080
    )
    # Beyond the configured current-data freshness window, Snapshot must not present
    # an observation as the user's CURRENT clinical picture (HC321-UAT11).
    # freshness_status still distinguishes aging vs deeply stale for operators.
    if age_min <= fresh_window:
        status = FRESHNESS_FRESH
        currentness = CURRENTNESS_CURRENT
    elif age_min <= fresh_window * float(stale_multiplier or 3):
        status = FRESHNESS_AGING
        currentness = CURRENTNESS_STALE
    else:
        status = FRESHNESS_STALE
        currentness = CURRENTNESS_STALE
    return {
        "freshness_status": status,
        "currentness": currentness,
        "age_seconds": age_sec,
        "age_minutes": age_min,
        "label": freshness_label(age_sec, currentness=currentness, measured=measured),
    }


def freshness_label(
    age_seconds: float | None,
    *,
    currentness: str = CURRENTNESS_CURRENT,
    measured: datetime | None = None,
) -> str:
    if age_seconds is None:
        return "No timestamp"
    seconds = max(0, int(age_seconds))
    if seconds < 60:
        relative = "just now"
    elif seconds < 3600:
        mins = max(1, seconds // 60)
        relative = f"{mins} minute{'s' if mins != 1 else ''} ago"
    elif seconds < 86400:
        hours = max(1, seconds // 3600)
        relative = f"{hours} hour{'s' if hours != 1 else ''} ago"
    else:
        days = max(1, seconds // 86400)
        relative = f"{days} day{'s' if days != 1 else ''} ago"
    if currentness == CURRENTNESS_STALE:
        return f"Last recorded {relative} (not current)"
    return f"Updated {relative}"


def consumer_status_from_flag(flag: str | None) -> str:
    if not flag:
        return STATUS_UNKNOWN
    return FLAG_TO_STATUS.get(str(flag), STATUS_UNKNOWN)


def status_color(status: str | None) -> str:
    return STATUS_TO_COLOR.get(str(status or STATUS_UNKNOWN), COLOR_GREY)


def status_text(status: str | None) -> str:
    return STATUS_TEXT.get(str(status or STATUS_UNKNOWN), "Unknown")


def _context_matches(text: str, tokens: tuple[str, ...]) -> bool:
    blob = (text or "").lower()
    return any(tok in blob for tok in tokens)


def evaluate_consumer_status(
    *,
    metric: str,
    value: Any,
    units: str | None = None,
    context: str | None = None,
    rules: ClinicalRulesEngine | None = None,
    informational: bool = False,
    currentness: str = CURRENTNESS_CURRENT,
    sample_count: int | None = None,
) -> dict[str, Any]:
    """
    Domain-level consumer status. Never called from presentation-only code
    with inline thresholds — this *is* the clinical/domain mapping.
    """
    if currentness == CURRENTNESS_MISSING:
        return _status_result(STATUS_UNKNOWN, reason="missing")
    if currentness == CURRENTNESS_INVALID:
        return _status_result(STATUS_UNKNOWN, reason="invalid")
    if currentness == CURRENTNESS_STALE:
        # Stale values remain visible but must not be coloured as current status.
        return _status_result(STATUS_UNKNOWN, reason="stale")
    if informational:
        return _status_result(STATUS_UNKNOWN, reason="informational_no_clinical_target")

    canonical = canonicalize_metric(metric)
    num = as_number(value)
    ctx = context or ""
    engine = rules or ClinicalRulesEngine()

    if canonical == "glucose":
        return _glucose_status(num, units, ctx, engine)
    if canonical == "heart_rate":
        return _heart_rate_status(num, units, ctx, engine)
    if canonical == "sleep_duration":
        return _sleep_duration_status(num, units, sample_count=sample_count)

    flag = engine.classify({"metric": canonical, "value": value, "units": units})
    return _status_result(consumer_status_from_flag(flag), reason=f"clinical_flag:{flag}")


def _status_result(status: str, *, reason: str) -> dict[str, Any]:
    return {
        "status": status,
        "status_text": status_text(status),
        "status_color": status_color(status),
        "reason": reason,
    }


def _glucose_status(
    num: float | None,
    units: str | None,
    context: str,
    rules: ClinicalRulesEngine,
) -> dict[str, Any]:
    if num is None:
        return _status_result(STATUS_UNKNOWN, reason="non_numeric")
    # ADA-style observational post-meal bands; not a diagnosis or treatment target.
    if _context_matches(context, POST_MEAL_TOKENS):
        if num < 70:
            return _status_result(STATUS_ATTENTION, reason="post_meal_hypoglycaemia_band")
        if num < 140:
            return _status_result(STATUS_NORMAL, reason="post_meal_target_band")
        if num < 200:
            return _status_result(STATUS_CAUTION, reason="post_meal_elevated_band")
        return _status_result(STATUS_ATTENTION, reason="post_meal_high_band")
    flag = rules.classify({"metric": "glucose", "value": num, "units": units or "mg/dL"})
    # Fasting-unknown mid-range stays CAUTION via Borderline (100–125) rather than
    # claiming NORMAL. Fasting context uses the same ClinicalRulesEngine bands.
    reason = "glucose_fasting" if _context_matches(context, FASTING_TOKENS) else "glucose_context_unknown"
    return _status_result(consumer_status_from_flag(flag), reason=f"{reason}:{flag}")


def _heart_rate_status(
    num: float | None,
    units: str | None,
    context: str,
    rules: ClinicalRulesEngine,
) -> dict[str, Any]:
    if num is None:
        return _status_result(STATUS_UNKNOWN, reason="non_numeric")
    if _context_matches(context, ACTIVITY_TOKENS):
        if num <= 40 or num >= 181:
            return _status_result(STATUS_ATTENTION, reason="activity_hr_extreme")
        return _status_result(STATUS_UNKNOWN, reason="activity_hr_no_resting_assumption")
    flag = rules.classify({"metric": "heart_rate", "value": num, "units": units or "bpm"})
    return _status_result(consumer_status_from_flag(flag), reason=f"heart_rate_unlabelled:{flag}")


def _sleep_minutes(num: float, units: str | None) -> float | None:
    unit = str(units or "").lower()
    if unit in {"h", "hr", "hrs", "hour", "hours"}:
        return num * 60.0
    if unit in {"min", "mins", "minute", "minutes", "m", ""}:
        # Catalog mixes hours and minutes; values <= 24 are treated as hours.
        if unit in {"", "m"} and 0 < num <= 24:
            return num * 60.0
        return num
    if 0 < num <= 24:
        return num * 60.0
    return num


def _sleep_duration_status(
    num: float | None,
    units: str | None,
    *,
    sample_count: int | None = None,
) -> dict[str, Any]:
    """
    National Sleep Foundation adult 7–9 hour nightly duration (observational).
    Single-night results never imply chronic deterioration by themselves.
    """
    if num is None:
        return _status_result(STATUS_UNKNOWN, reason="non_numeric")
    minutes = _sleep_minutes(num, units)
    if minutes is None:
        return _status_result(STATUS_UNKNOWN, reason="unconvertible_sleep_duration")
    hours = minutes / 60.0
    if 7.0 <= hours <= 9.0:
        status = STATUS_NORMAL
        reason = "adult_sleep_7_to_9h"
    elif 6.0 <= hours < 7.0 or 9.0 < hours <= 10.0:
        status = STATUS_CAUTION
        reason = "adult_sleep_borderline_duration"
    else:
        status = STATUS_ATTENTION
        reason = "adult_sleep_short_or_long_night"
    if sample_count is not None and sample_count < 3:
        reason += ";single_night_not_chronic"
    return _status_result(status, reason=reason)


def trend_from_values(metric: str, values: list[float]) -> dict[str, Any]:
    """Reuse TrendEngine polarity (higher/lower-better) with a 3-point rule."""
    if len(values) < 3:
        return {
            "direction": None,
            "label": None,
            "indicator": None,
            "reason": "insufficient_points",
        }
    a, b, c = values[-3], values[-2], values[-1]
    rising = c > b > a
    falling = c < b < a
    canonical = canonicalize_metric(metric)
    if canonical in HIGHER_BETTER or metric in HIGHER_BETTER:
        if rising:
            direction = "improving"
        elif falling:
            direction = "worsening"
        else:
            direction = "stable"
    elif canonical in LOWER_BETTER or metric in LOWER_BETTER:
        if falling:
            direction = "improving"
        elif rising:
            direction = "worsening"
        else:
            direction = "stable"
    else:
        if rising:
            direction = "rising"
        elif falling:
            direction = "falling"
        else:
            direction = "stable"
    labels = {
        "improving": "Improving",
        "worsening": "Worsening",
        "rising": "Rising",
        "falling": "Falling",
        "stable": "Stable",
    }
    indicators = {
        "improving": "↓" if canonical in LOWER_BETTER or metric in LOWER_BETTER else "↑",
        "worsening": "↑" if canonical in LOWER_BETTER or metric in LOWER_BETTER else "↓",
        "rising": "↑",
        "falling": "↓",
        "stable": "→",
    }
    return {
        "direction": direction,
        "label": labels[direction],
        "indicator": indicators[direction],
        "reason": "auto",
    }


def format_display_value(value: Any, *, metric: str | None = None, units: str | None = None) -> str:
    if value is None or value == "":
        return "—"
    canonical = canonicalize_metric(metric or "")
    if canonical == "sleep_duration":
        minutes = _sleep_minutes(float(as_number(value) or 0), units)
        if minutes is None:
            return str(value)
        hours = minutes / 60.0
        if abs(hours - round(hours)) < 0.05:
            return str(int(round(hours)))
        return f"{hours:.1f}"
    num = as_number(value)
    if num is None:
        return str(value)
    if abs(num - round(num)) < 0.05:
        return str(int(round(num)))
    return f"{num:.1f}"


def accessibility_label(card: dict[str, Any]) -> str:
    name = card.get("title") or card.get("metric_id") or "Metric"
    value = card.get("display_value") or "no value"
    unit = card.get("unit") or ""
    status = card.get("status_text") or "unknown"
    fresh = card.get("freshness_label") or ""
    if card.get("metric_id") == "blood_pressure" and "/" in str(value):
        parts = str(value).split("/", 1)
        spoken_value = f"{parts[0].strip()} over {parts[1].strip()}"
        spoken_unit = "millimetres of mercury" if "mmhg" in unit.lower() else unit
    else:
        spoken_value = str(value)
        spoken_unit = {
            "mg/dl": "milligrams per decilitre",
            "mmhg": "millimetres of mercury",
            "bpm": "beats per minute",
            "%": "percent",
            "kg": "kilograms",
            "kg/m2": "kilograms per square metre",
            "ml/min/1.73m2": "millilitres per minute",
            "h": "hours",
            "steps": "steps",
            "min": "minutes",
            "score": "score",
        }.get(unit.lower(), unit)
    bits = [str(name), spoken_value]
    if spoken_unit:
        bits.append(spoken_unit)
    bits.append(f"status {status}")
    if fresh:
        bits.append(fresh)
    return ", ".join(bits) + "."


def apply_layout(
    cards: list[dict[str, Any]],
    layout: dict[str, Any] | None,
    default_order: list[str],
) -> list[dict[str, Any]]:
    layout = layout or {}
    hidden = set(layout.get("hidden") or [])
    order = list(layout.get("order") or default_order or [])
    by_id = {c["metric_id"]: c for c in cards}
    visible: list[dict[str, Any]] = []
    seen: set[str] = set()
    for mid in order:
        if mid in hidden:
            continue
        card = by_id.get(mid)
        if card:
            visible.append(card)
            seen.add(mid)
    for card in cards:
        mid = card["metric_id"]
        if mid in seen or mid in hidden:
            continue
        visible.append(card)
    return visible


def _row_from_measurement(m: dict[str, Any], docs_by_id: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    docs_by_id = docs_by_id or {}
    doc = docs_by_id.get(str(m.get("document_id") or "")) or {}
    return {
        "metric": m.get("metric"),
        "metric_type": m.get("metric") or m.get("metric_type"),
        "value": m.get("value"),
        "units": m.get("units") or m.get("unit"),
        "unit": m.get("units") or m.get("unit"),
        "measured_at": m.get("measured_at"),
        "provenance": m.get("provenance") or doc.get("provenance"),
        "source": m.get("source") or doc.get("source_system"),
        "quality": m.get("quality") or {"unit_compatible": m.get("unit_compatible", True)},
        "unit_compatible": m.get("unit_compatible", True),
        "acquisition_mode": m.get("acquisition_mode") or "IMPORTED",
        "context": m.get("context") or m.get("tag") or "",
        "notes": m.get("notes") or "",
        "tags": m.get("tags") or doc.get("tags") or [],
        "document_id": m.get("document_id"),
        "confidence": m.get("confidence"),
        "invalid": m.get("invalid"),
        "fingerprint": m.get("fingerprint"),
        "observation_id": m.get("observation_id") or m.get("id"),
        "source_record_id": m.get("source_record_id"),
    }


class HealthSnapshotEngine:
    """Compose latest-valid observations into consumer Health Snapshot cards."""

    def __init__(
        self,
        store: Any | None = None,
        *,
        config: dict[str, Any] | None = None,
        clinical_rules: ClinicalRulesEngine | None = None,
        trend_engine: TrendEngine | None = None,
    ) -> None:
        self.store = store
        self.config = config or load_health_snapshot_config()
        self.rules = clinical_rules or ClinicalRulesEngine()
        self.trends = trend_engine or (TrendEngine(store) if store is not None else None)

    def generate(
        self,
        *,
        patient_id: str = "default-patient",
        as_of: str | datetime | None = None,
        observations: list[dict[str, Any]] | None = None,
        layout: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        as_of_dt = parse_iso(as_of) or datetime.now(timezone.utc)
        rows = list(observations) if observations is not None else self._collect_rows(patient_id)
        cards = self.build_cards(rows, as_of=as_of_dt)
        default_order = list(self.config.get("default_card_order") or [])
        cards = apply_layout(cards, layout, default_order)
        return {
            "schema_version": self.config.get("schema_version") or "hc.health_snapshot.v1",
            "generated_at": utc_now(),
            "as_of": as_of_dt.isoformat().replace("+00:00", "Z"),
            "patient_id": patient_id,
            "cards": cards,
            "card_count": len(cards),
            "disclaimer": self.config.get("disclaimer") or DISCLAIMER,
            "observational_only": True,
            "diagnostic": False,
            "prescriptive": False,
        }

    def build_cards(
        self,
        observations: list[dict[str, Any]],
        *,
        as_of: datetime | None = None,
    ) -> list[dict[str, Any]]:
        as_of_dt = as_of or datetime.now(timezone.utc)
        specs = self.config.get("metrics") or {}
        cards: list[dict[str, Any]] = []
        seen: set[str] = set()
        for metric_id, spec in specs.items():
            card = self._card_for(metric_id, spec, observations, as_of_dt)
            if card:
                cards.append(card)
                seen.add(metric_id)
                # Prefer Activity over Exercise Minutes when both exist as observations.
                for alias in (spec.get("aliases") or []):
                    seen.add(snapshot_metric_id(alias))
                seen.add(snapshot_metric_id(metric_id))
        # Surface other significant existing observations not in the initial set.
        extra_metrics = sorted(
            {
                snapshot_metric_id(observation_metric(r))
                for r in observations
                if is_valid_observation(r, rules=self.rules)
            }
            - seen
            - {
                "systolic_bp",
                "diastolic_bp",
                "systolic",
                "diastolic",
                "spo2",
                "ldl_c",
                "exercise_minutes",  # aliased into activity_minutes for Snapshot
            }
        )
        for metric in extra_metrics:
            if not metric or metric in {"unknown", "rhythm", "ecg_result"}:
                continue
            if metric in SNAPSHOT_METRIC_DEDUPE:
                continue
            spec = {
                "title": metric.replace("_", " ").title(),
                "unit": None,
                "kind": "scalar",
                "clinical": True,
                "detail_tab": "vault",
                "detail_category": "other",
                "detail_metric": metric,
            }
            card = self._card_for(metric, spec, observations, as_of_dt)
            if card:
                cards.append(card)
        return cards

    def metric_detail(
        self,
        metric_id: str,
        *,
        patient_id: str = "default-patient",
        as_of: str | datetime | None = None,
        observations: list[dict[str, Any]] | None = None,
        history_limit: int = 40,
    ) -> dict[str, Any]:
        """Drill-down payload for one Snapshot metric (history + stats, no fabricated rows)."""
        as_of_dt = parse_iso(as_of) or datetime.now(timezone.utc)
        rows = list(observations) if observations is not None else self._collect_rows(patient_id)
        want = snapshot_metric_id(metric_id)
        specs = self.config.get("metrics") or {}
        # Prefer configured Activity card when requesting exercise_minutes.
        preferred_id = want
        if preferred_id == "activity_minutes" and "activity_minutes" in specs:
            preferred_id = "activity_minutes"
        elif preferred_id in specs:
            preferred_id = preferred_id
        elif metric_id in specs:
            preferred_id = metric_id
        else:
            preferred_id = want

        card = None
        for c in self.build_cards(rows, as_of=as_of_dt):
            if c.get("metric_id") == preferred_id or snapshot_metric_id(c.get("metric_id")) == want:
                card = c
                break
        if card is None:
            return {
                "schema_version": self.config.get("schema_version") or "hc.health_snapshot.v1",
                "metric_id": preferred_id,
                "found": False,
                "card": None,
                "history": [],
                "stats": None,
                "disclaimer": self.config.get("disclaimer") or DISCLAIMER,
            }

        aliases = {want, preferred_id, canonicalize_metric(metric_id)}
        spec = specs.get(preferred_id) or {}
        for a in spec.get("aliases") or []:
            aliases.add(canonicalize_metric(a))
            aliases.add(snapshot_metric_id(a))
        if preferred_id == "blood_pressure":
            aliases.update({"systolic_bp", "diastolic_bp", "systolic", "diastolic"})
        if preferred_id == "activity_minutes":
            aliases.add("exercise_minutes")

        history_rows: list[dict[str, Any]] = []
        for r in rows:
            mid = observation_metric(r)
            if preferred_id == "blood_pressure":
                if mid not in {"systolic_bp", "diastolic_bp"}:
                    continue
            elif mid not in aliases and snapshot_metric_id(mid) not in aliases:
                continue
            if not is_valid_observation(r, rules=self.rules):
                continue
            measured = parse_iso(r.get("measured_at"))
            if measured is None:
                continue
            num = as_number(r.get("value"))
            hist_status = evaluate_consumer_status(
                metric=mid if preferred_id != "blood_pressure" else mid,
                value=r.get("value"),
                units=r.get("units") or r.get("unit"),
                context=observation_context(r),
                rules=self.rules,
                informational=bool(spec.get("informational") or spec.get("clinical") is False),
                currentness=CURRENTNESS_CURRENT,
            )
            history_rows.append(
                {
                    "metric": mid,
                    "value": r.get("value"),
                    "display_value": format_display_value(
                        r.get("value"), metric=mid, units=r.get("units") or r.get("unit")
                    ),
                    "units": r.get("units") or r.get("unit"),
                    "measured_at": r.get("measured_at"),
                    "provenance": r.get("provenance"),
                    "source": r.get("source"),
                    "source_metric": mid,
                    "fingerprint": r.get("fingerprint"),
                    "observation_id": r.get("observation_id") or r.get("id"),
                    "source_record_id": r.get("source_record_id"),
                    "historical_status": hist_status["status"],
                    "historical_status_text": hist_status["status_text"],
                    "historical_status_color": hist_status["status_color"],
                    "_ts": measured,
                    "_num": num,
                }
            )
        history_rows.sort(key=lambda item: item["_ts"], reverse=True)
        history_rows = dedupe_observation_history(history_rows)
        trimmed = history_rows[: max(1, int(history_limit or 40))]
        nums = [item["_num"] for item in trimmed if item.get("_num") is not None]
        stats = None
        if nums:
            stats = {
                "sample_count": len(nums),
                "average": round(sum(nums) / len(nums), 2),
                "minimum": min(nums),
                "maximum": max(nums),
            }
        for item in trimmed:
            item.pop("_ts", None)
            item.pop("_num", None)

        source_metric = None
        if preferred_id == "activity_minutes":
            for r in rows:
                if observation_metric(r) == "exercise_minutes" and is_valid_observation(r, rules=self.rules):
                    source_metric = "exercise_minutes"
                    break

        return {
            "schema_version": self.config.get("schema_version") or "hc.health_snapshot.v1",
            "metric_id": preferred_id,
            "found": True,
            "card": card,
            "history": trimmed,
            "stats": stats,
            "canonical_source_metric": source_metric,
            "filter_metrics": sorted(aliases),
            "disclaimer": self.config.get("disclaimer") or DISCLAIMER,
            "observational_only": True,
        }

    def _collect_rows(self, patient_id: str) -> list[dict[str, Any]]:
        if self.store is None:
            return []
        docs = {
            d.get("id"): d
            for d in (self.store.list_documents() or [])
            if str(d.get("patient_id") or "default-patient") == patient_id
        }
        rows: list[dict[str, Any]] = []
        for m in self.store.list_measurements() or []:
            doc = docs.get(str(m.get("document_id") or ""))
            if m.get("document_id") and str(m.get("document_id")) not in docs:
                # Measurements without a matching patient document still count if unscoped.
                if docs:
                    continue
            if doc is None and docs:
                # Keep orphan measurements for the default patient vault.
                pass
            rows.append(_row_from_measurement(m, docs))
        try:
            for o in self.store.list_observations() or []:
                if str(o.get("patient_id") or "default-patient") != patient_id:
                    continue
                row = dict(o)
                # Companion / Health Connect rows often store `unit` (singular).
                if row.get("units") is None and row.get("unit") is not None:
                    row["units"] = row.get("unit")
                rows.append(row)
        except Exception:
            pass
        return rows

    def _card_for(
        self,
        metric_id: str,
        spec: dict[str, Any],
        observations: list[dict[str, Any]],
        as_of: datetime,
    ) -> dict[str, Any] | None:
        kind = spec.get("kind") or "scalar"
        if kind == "composite_bp":
            return self._blood_pressure_card(spec, observations, as_of)
        aliases = [canonicalize_metric(a) for a in (spec.get("aliases") or [metric_id])]
        if canonicalize_metric(metric_id) not in aliases:
            aliases.insert(0, canonicalize_metric(metric_id))
        alias_set = set(aliases)
        alias_set.update(snapshot_metric_id(a) for a in list(alias_set))
        if "activity_minutes" in alias_set:
            alias_set.add("exercise_minutes")
        matching = [
            r
            for r in observations
            if observation_metric(r) in alias_set or snapshot_metric_id(observation_metric(r)) in alias_set
        ]
        latest = select_latest_valid(matching, rules=self.rules)
        if latest is None:
            return None  # do not manufacture empty metrics
        series = []
        for r in matching:
            if is_valid_observation(r, rules=self.rules):
                num = as_number(r.get("value"))
                if num is not None:
                    series.append((parse_iso(r.get("measured_at")), num))
        series.sort(key=lambda item: item[0] or datetime.min.replace(tzinfo=timezone.utc))
        values = [v for _, v in series]
        return self._scalar_card(
            metric_id=metric_id,
            spec=spec,
            latest=latest,
            values=values,
            as_of=as_of,
        )

    def _scalar_card(
        self,
        *,
        metric_id: str,
        spec: dict[str, Any],
        latest: dict[str, Any],
        values: list[float],
        as_of: datetime,
    ) -> dict[str, Any]:
        windows = self.config.get("freshness_windows_minutes") or {}
        multiplier = float(self.config.get("stale_escalation_multiplier") or 3)
        unit = latest.get("units") or latest.get("unit") or spec.get("unit")
        fresh = compute_freshness(
            metric=metric_id,
            measured_at=str(latest.get("measured_at") or ""),
            now=as_of,
            windows=windows,
            stale_multiplier=multiplier,
        )
        trend = trend_from_values(metric_id, values)
        status = evaluate_consumer_status(
            metric=metric_id,
            value=latest.get("value"),
            units=unit,
            context=observation_context(latest),
            rules=self.rules,
            informational=bool(spec.get("informational") or spec.get("clinical") is False),
            currentness=fresh["currentness"],
            sample_count=len(values),
        )
        # Preserve historical clinical reading for drill-down when Snapshot is UNKNOWN due to freshness.
        historical = evaluate_consumer_status(
            metric=metric_id,
            value=latest.get("value"),
            units=unit,
            context=observation_context(latest),
            rules=self.rules,
            informational=bool(spec.get("informational") or spec.get("clinical") is False),
            currentness=CURRENTNESS_CURRENT,
            sample_count=len(values),
        )
        display_unit = spec.get("unit") or unit
        if metric_id == "sleep_duration":
            display_unit = "h"
        source_metric = observation_metric(latest)
        card = {
            "metric_id": metric_id,
            "title": spec.get("title") or metric_id.replace("_", " ").title(),
            "display_value": format_display_value(latest.get("value"), metric=metric_id, units=unit),
            "unit": display_unit,
            "status": status["status"],
            "status_text": status["status_text"],
            "status_color": status["status_color"],
            "status_reason": status["reason"],
            "historical_status": historical["status"],
            "historical_status_text": historical["status_text"],
            "historical_status_color": historical["status_color"],
            "measured_at": latest.get("measured_at"),
            "freshness_status": fresh["freshness_status"],
            "currentness": fresh["currentness"],
            "freshness_label": fresh["label"],
            "age_seconds": fresh["age_seconds"],
            "trend_direction": trend["direction"],
            "trend_label": trend["label"],
            "trend_indicator": trend["indicator"],
            "provenance": latest.get("provenance"),
            "source": latest.get("source"),
            "source_metric": source_metric,
            "detail_tab": spec.get("detail_tab") or "vault",
            "detail_category": spec.get("detail_category") or "other",
            "detail_metric": spec.get("detail_metric") or metric_id,
            "informational": bool(spec.get("informational")),
            "context_note": spec.get("context_note"),
        }
        card["accessibility_label"] = accessibility_label(card)
        return card

    def _blood_pressure_card(
        self,
        spec: dict[str, Any],
        observations: list[dict[str, Any]],
        as_of: datetime,
    ) -> dict[str, Any] | None:
        sys_rows = [r for r in observations if observation_metric(r) == "systolic_bp"]
        dia_rows = [r for r in observations if observation_metric(r) == "diastolic_bp"]
        sys_latest = select_latest_valid(sys_rows, rules=self.rules)
        dia_latest = select_latest_valid(dia_rows, rules=self.rules)
        if sys_latest is None and dia_latest is None:
            return None
        pair = _bp_pair(sys_rows, dia_rows, rules=self.rules)
        sys_obs = pair[0] if pair else sys_latest
        dia_obs = pair[1] if pair else dia_latest
        measured = None
        if sys_obs and dia_obs:
            measured = max(
                parse_iso(sys_obs.get("measured_at")) or datetime.min.replace(tzinfo=timezone.utc),
                parse_iso(dia_obs.get("measured_at")) or datetime.min.replace(tzinfo=timezone.utc),
            )
        elif sys_obs:
            measured = parse_iso(sys_obs.get("measured_at"))
        elif dia_obs:
            measured = parse_iso(dia_obs.get("measured_at"))
        measured_at = measured.isoformat().replace("+00:00", "Z") if measured else None
        windows = self.config.get("freshness_windows_minutes") or {}
        multiplier = float(self.config.get("stale_escalation_multiplier") or 3)
        fresh = compute_freshness(
            metric="blood_pressure",
            measured_at=measured_at,
            now=as_of,
            windows=windows,
            stale_multiplier=multiplier,
        )
        sys_num = as_number((sys_obs or {}).get("value"))
        dia_num = as_number((dia_obs or {}).get("value"))
        if sys_num is not None and dia_num is not None:
            display = f"{format_display_value(sys_num)}/{format_display_value(dia_num)}"
            sys_status = evaluate_consumer_status(
                metric="systolic_bp",
                value=sys_num,
                units="mmHg",
                rules=self.rules,
                currentness=fresh["currentness"],
            )
            dia_status = evaluate_consumer_status(
                metric="diastolic_bp",
                value=dia_num,
                units="mmHg",
                rules=self.rules,
                currentness=fresh["currentness"],
            )
            status = _worse_status(sys_status["status"], dia_status["status"])
            reason = f"bp_pair:{sys_status['reason']}+{dia_status['reason']}"
        else:
            display = (
                format_display_value(sys_num)
                if sys_num is not None
                else format_display_value(dia_num)
            )
            status = STATUS_UNKNOWN
            reason = "incomplete_bp_pair"
        sys_series = [
            as_number(r.get("value"))
            for r in sys_rows
            if is_valid_observation(r, rules=self.rules) and as_number(r.get("value")) is not None
        ]
        trend = trend_from_values("systolic_bp", [v for v in sys_series if v is not None])
        source = (sys_obs or dia_obs or {}).get("source")
        provenance = (sys_obs or dia_obs or {}).get("provenance")
        card = {
            "metric_id": "blood_pressure",
            "title": spec.get("title") or "Blood Pressure",
            "display_value": display,
            "unit": spec.get("unit") or "mmHg",
            "status": status,
            "status_text": status_text(status),
            "status_color": status_color(status),
            "status_reason": reason,
            "measured_at": measured_at,
            "freshness_status": fresh["freshness_status"],
            "currentness": fresh["currentness"],
            "freshness_label": fresh["label"],
            "age_seconds": fresh["age_seconds"],
            "trend_direction": trend["direction"],
            "trend_label": trend["label"],
            "trend_indicator": trend["indicator"],
            "provenance": provenance,
            "source": source,
            "detail_tab": spec.get("detail_tab") or "vault",
            "detail_category": spec.get("detail_category") or "blood_pressure",
            "detail_metric": spec.get("detail_metric") or "systolic_bp",
            "informational": False,
            "context_note": spec.get("context_note"),
        }
        card["accessibility_label"] = accessibility_label(card)
        return card


def _worse_status(a: str, b: str) -> str:
    rank = {
        STATUS_UNKNOWN: 0,
        STATUS_NORMAL: 1,
        STATUS_CAUTION: 2,
        STATUS_ATTENTION: 3,
    }
    # Incomplete clinical picture should not hide a known caution/attention.
    if STATUS_UNKNOWN in (a, b) and STATUS_ATTENTION not in (a, b) and STATUS_CAUTION not in (a, b):
        return STATUS_UNKNOWN if a == STATUS_UNKNOWN or b == STATUS_UNKNOWN else a
    return a if rank.get(a, 0) >= rank.get(b, 0) else b


def _bp_pair(
    sys_rows: list[dict[str, Any]],
    dia_rows: list[dict[str, Any]],
    *,
    rules: ClinicalRulesEngine,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Prefer systolic/diastolic sharing the same timestamp; else closest within 5 minutes."""
    valid_sys = [r for r in sys_rows if is_valid_observation(r, rules=rules)]
    valid_dia = [r for r in dia_rows if is_valid_observation(r, rules=rules)]
    dia_by_ts = {}
    for r in valid_dia:
        ts = str(r.get("measured_at") or "")
        prev = dia_by_ts.get(ts)
        if not prev or str(r.get("measured_at") or "") >= str(prev.get("measured_at") or ""):
            dia_by_ts[ts] = r
    paired: list[tuple[datetime, dict[str, Any], dict[str, Any]]] = []
    for s in valid_sys:
        ts = str(s.get("measured_at") or "")
        if ts in dia_by_ts:
            measured = parse_iso(ts)
            if measured:
                paired.append((measured, s, dia_by_ts[ts]))
    if paired:
        paired.sort(key=lambda item: item[0], reverse=True)
        return paired[0][1], paired[0][2]
    # Closest pair within 5 minutes
    best: tuple[float, datetime, dict[str, Any], dict[str, Any]] | None = None
    for s in valid_sys:
        st = parse_iso(s.get("measured_at"))
        if st is None:
            continue
        for d in valid_dia:
            dt = parse_iso(d.get("measured_at"))
            if dt is None:
                continue
            delta = abs((st - dt).total_seconds())
            if delta > 300:
                continue
            newest = max(st, dt)
            if best is None or newest > best[1] or (newest == best[1] and delta < best[0]):
                best = (delta, newest, s, d)
    if best:
        return best[2], best[3]
    return None
