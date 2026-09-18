"""HC330 dashboard current-evidence guard.

Prevents a reassuring overall headline when the monitoring evidence used to
represent the current picture is stale or missing. This is a data-quality gate,
not a clinical rules engine.
"""
from __future__ import annotations

from typing import Any

CURRENT = "current"


def current_evidence_guard(freshness_path: dict[str, Any] | None) -> dict[str, Any]:
    """Summarise whether inspectable monitoring evidence is current enough.

    The guard deliberately does not infer health from historical values. A metric
    counts as current only when HC323 marks its currentness as ``current``.
    """
    path = freshness_path or {}
    by_metric = path.get("by_metric") if isinstance(path.get("by_metric"), dict) else {}
    inspected = []
    current = []
    unavailable = []
    for metric, row in by_metric.items():
        if not isinstance(row, dict):
            continue
        inspected.append(metric)
        if str(row.get("currentness") or "").lower() == CURRENT:
            current.append(metric)
        else:
            unavailable.append(metric)

    # If HC monitoring exists but none of its inspectable metrics is current, a
    # generic Normal headline is unsafe/misleading. Mixed evidence remains
    # available to the existing per-card logic, with an explicit quality flag.
    has_persisted_monitoring = bool(path.get("companion_observation_count"))
    no_current = bool(inspected) and not current
    insufficient = has_persisted_monitoring and no_current
    return {
        "insufficient_current_evidence": insufficient,
        "current_metric_count": len(current),
        "unavailable_metric_count": len(unavailable),
        "current_metrics": current,
        "unavailable_metrics": unavailable,
        "headline_status_override": "unknown" if insufficient else None,
        "headline_label_override": (
            "Current health status unavailable — data stale" if insufficient else None
        ),
        "reason": (
            "Health Connect observations exist, but no inspected headline metric is current."
            if insufficient
            else "Current-evidence guard did not require a headline override."
        ),
    }
