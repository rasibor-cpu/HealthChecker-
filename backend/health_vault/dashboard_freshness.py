"""HC330 fail-safe dashboard freshness semantics.

This module is intentionally clinical-rule agnostic. It evaluates whether the
consumer landing summary has enough *current data* to safely present a normal
headline. Historical observations remain visible elsewhere; stale/missing data
must never be promoted into a current clinical classification.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


FAILSAFE_DATA_STALE = "data_stale"
FAILSAFE_DATA_UNAVAILABLE = "data_unavailable"


@dataclass(frozen=True)
class FreshnessCoverage:
    observed_metrics: int
    current_metrics: int
    stale_metrics: int
    missing_metrics: int

    @property
    def current_ratio(self) -> float:
        if self.observed_metrics <= 0:
            return 0.0
        return self.current_metrics / self.observed_metrics

    def to_dict(self) -> dict[str, Any]:
        return {
            "observed_metrics": self.observed_metrics,
            "current_metrics": self.current_metrics,
            "stale_metrics": self.stale_metrics,
            "missing_metrics": self.missing_metrics,
            "current_ratio": self.current_ratio,
        }


def freshness_coverage(freshness_path: dict[str, Any] | None) -> FreshnessCoverage:
    path = freshness_path if isinstance(freshness_path, dict) else {}
    by_metric = path.get("by_metric")
    rows = by_metric if isinstance(by_metric, dict) else {}

    observed = 0
    current = 0
    stale = 0
    missing = 0

    for row in rows.values():
        item = row if isinstance(row, dict) else {}
        has_observation = bool(item.get("vault_latest_at") or item.get("api_latest_at"))
        currentness = str(item.get("currentness") or "missing").lower()
        if has_observation:
            observed += 1
            if currentness == "current":
                current += 1
            else:
                stale += 1
        else:
            missing += 1

    return FreshnessCoverage(
        observed_metrics=observed,
        current_metrics=current,
        stale_metrics=stale,
        missing_metrics=missing,
    )


def derive_fail_safe_overall_status(
    base_status: str,
    freshness_path: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    """Return a freshness-safe consumer headline status plus data-quality detail.

    Warning/attention states are preserved: freshness must never suppress a
    potentially important signal. Only a nominal ``normal`` headline is gated.

    Rules for a nominal base state:
    * no observed headline metric -> ``data_unavailable``;
    * zero current headline metrics -> ``data_stale``;
    * a strict majority of observed headline metrics stale -> ``data_stale``;
    * otherwise retain ``normal`` while exposing coverage detail to consumers.
    """

    normalized = str(base_status or "").strip().lower() or FAILSAFE_DATA_UNAVAILABLE
    coverage = freshness_coverage(freshness_path)
    detail = coverage.to_dict()
    detail["base_status"] = normalized

    if normalized != "normal":
        detail["reason"] = "non_normal_status_preserved"
        return normalized, detail

    if coverage.observed_metrics == 0:
        detail["reason"] = "no_currently_evaluable_headline_metrics"
        return FAILSAFE_DATA_UNAVAILABLE, detail

    if coverage.current_metrics == 0:
        detail["reason"] = "all_observed_headline_metrics_stale"
        return FAILSAFE_DATA_STALE, detail

    if coverage.stale_metrics > coverage.current_metrics:
        detail["reason"] = "majority_of_observed_headline_metrics_stale"
        return FAILSAFE_DATA_STALE, detail

    detail["reason"] = "sufficient_current_coverage_for_nominal_headline"
    return normalized, detail
