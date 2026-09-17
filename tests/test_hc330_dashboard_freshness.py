from backend.health_vault.dashboard_freshness import (
    FAILSAFE_DATA_STALE,
    FAILSAFE_DATA_UNAVAILABLE,
    derive_fail_safe_overall_status,
    freshness_coverage,
)


def _path(*, current=0, stale=0, missing=0):
    by_metric = {}
    for idx in range(current):
        by_metric[f"current_{idx}"] = {
            "vault_latest_at": "2026-09-17T12:00:00Z",
            "api_latest_at": "2026-09-17T12:00:00Z",
            "currentness": "current",
        }
    for idx in range(stale):
        by_metric[f"stale_{idx}"] = {
            "vault_latest_at": "2026-09-07T12:00:00Z",
            "api_latest_at": "2026-09-07T12:00:00Z",
            "currentness": "stale",
        }
    for idx in range(missing):
        by_metric[f"missing_{idx}"] = {
            "vault_latest_at": None,
            "api_latest_at": None,
            "currentness": "missing",
        }
    return {"by_metric": by_metric}


def test_no_observed_metrics_cannot_render_normal():
    status, detail = derive_fail_safe_overall_status("normal", _path(missing=8))
    assert status == FAILSAFE_DATA_UNAVAILABLE
    assert detail["reason"] == "no_currently_evaluable_headline_metrics"


def test_all_stale_metrics_cannot_render_normal():
    status, detail = derive_fail_safe_overall_status("normal", _path(stale=4, missing=4))
    assert status == FAILSAFE_DATA_STALE
    assert detail["current_metrics"] == 0
    assert detail["stale_metrics"] == 4


def test_majority_stale_metrics_cannot_render_normal():
    status, detail = derive_fail_safe_overall_status("normal", _path(current=2, stale=3, missing=3))
    assert status == FAILSAFE_DATA_STALE
    assert detail["reason"] == "majority_of_observed_headline_metrics_stale"


def test_mixed_current_and_stale_without_stale_majority_can_remain_normal():
    status, detail = derive_fail_safe_overall_status("normal", _path(current=3, stale=2, missing=3))
    assert status == "normal"
    assert detail["reason"] == "sufficient_current_coverage_for_nominal_headline"


def test_attention_or_warning_is_never_suppressed_by_freshness_gate():
    status, detail = derive_fail_safe_overall_status("warning", _path(stale=8))
    assert status == "warning"
    assert detail["reason"] == "non_normal_status_preserved"


def test_coverage_counts_observed_separately_from_missing():
    coverage = freshness_coverage(_path(current=2, stale=3, missing=3))
    assert coverage.observed_metrics == 5
    assert coverage.current_metrics == 2
    assert coverage.stale_metrics == 3
    assert coverage.missing_metrics == 3
    assert coverage.current_ratio == 0.4
