from backend.health_vault.dashboard_freshness_guard import current_evidence_guard
from pathlib import Path


def test_stale_monitoring_cannot_support_normal_headline():
    result = current_evidence_guard({
        "companion_observation_count": 14672,
        "by_metric": {
            "heart_rate": {"currentness": "stale"},
            "oxygen_saturation": {"currentness": "stale"},
            "steps": {"currentness": "stale"},
            "sleep_duration": {"currentness": "stale"},
        },
    })
    assert result["insufficient_current_evidence"] is True
    assert result["headline_status_override"] == "unknown"
    assert result["headline_label_override"] == "Current health status unavailable — data stale"


def test_current_monitoring_does_not_force_override():
    result = current_evidence_guard({
        "companion_observation_count": 10,
        "by_metric": {
            "heart_rate": {"currentness": "current"},
            "oxygen_saturation": {"currentness": "stale"},
        },
    })
    assert result["insufficient_current_evidence"] is False
    assert result["headline_status_override"] is None


def test_no_monitoring_history_does_not_invent_health_status():
    result = current_evidence_guard({"companion_observation_count": 0, "by_metric": {}})
    assert result["insufficient_current_evidence"] is False
    assert result["headline_status_override"] is None

def test_dashboard_consumes_current_evidence_guard():
    root = Path(__file__).parents[1]
    backend = (root / "backend" / "health_vault" / "dashboard_service.py").read_text(
        encoding="utf-8"
    )
    frontend = (root / "js" / "health_vault" / "dashboard.js").read_text(
        encoding="utf-8"
    )

    assert "freshness_guard = current_evidence_guard(freshness_path)" in backend
    assert 'status = str(freshness_guard.get("headline_status_override") or "unknown")' in backend
    assert '"status_label_override": freshness_guard.get("headline_label_override")' in backend
    assert "payload.status_label_override || payload.status.toUpperCase()" in frontend
    assert 'payload.status === "normal" ? "ok" : "muted"' in frontend

