from pathlib import Path

from backend.health_vault.dashboard_freshness_guard import current_evidence_guard
from backend.health_vault.freshness_path import build_freshness_path


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

def test_freshness_path_uses_health_connect_only_and_metric_windows():
    class Store:
        def list_observations(self):
            return [
                {
                    "patient_id": "patient-A",
                    "metric_type": "heart_rate",
                    "value": 72,
                    "measured_at": "2026-09-28T06:00:00Z",
                    "source": "health_connect_companion",
                    "connector_id": "health_connect",
                },
                {
                    "patient_id": "patient-A",
                    "metric_type": "glucose_cgm_interstitial",
                    "value": 169,
                    "measured_at": "2026-09-26T10:00:00Z",
                    "source": "health_connect_companion",
                    "connector_id": "health_connect",
                },
            ]

        def list_measurements(self):
            # A recent manual measurement must not make monitoring look current.
            return [
                {
                    "patient_id": "patient-A",
                    "metric": "systolic_bp",
                    "value": 120,
                    "measured_at": "2026-09-28T09:59:00Z",
                    "provenance": "manual_upload",
                }
            ]

        def get_companion_status(self):
            return {}

    path = build_freshness_path(
        Store(),
        "patient-A",
        now="2026-09-28T10:00:00Z",
    )

    # Four hours is outside the configured 180-minute heart-rate window,
    # even though it is within the legacy seven-day default.
    assert path["by_metric"]["heart_rate"]["currentness"] == "stale"
    # Glucose subclasses use the configured one-day glucose family window,
    # not the seven-day default.
    assert path["by_metric"]["glucose_cgm_interstitial"]["currentness"] == "stale"
    assert path["by_metric"]["systolic_bp"]["vault_latest_at"] is None


def test_dashboard_consumes_guard_without_downgrading_warnings():
    root = Path(__file__).parents[1]
    backend = (root / "backend" / "health_vault" / "dashboard_service.py").read_text(
        encoding="utf-8"
    )
    classic = (root / "js" / "health_vault" / "dashboard.js").read_text(
        encoding="utf-8"
    )
    mobile = (root / "js" / "health_vault" / "mobile_consumer.js").read_text(
        encoding="utf-8"
    )

    assert "freshness_guard = current_evidence_guard(freshness_path)" in backend
    assert 'if status == "normal" and freshness_guard.get("insufficient_current_evidence")' in backend
    assert 'status_label_override = freshness_guard.get("headline_label_override")' in backend
    assert '"status_label_override": status_label_override' in backend
    assert "payload.status_label_override || payload.status.toUpperCase()" in classic
    assert "overall_status_label=status_label_override" in backend
    assert "summary.overall_status_label || status.status_label_override" in mobile
    assert 'payload.status === "normal" ? "ok" : "muted"' in classic
