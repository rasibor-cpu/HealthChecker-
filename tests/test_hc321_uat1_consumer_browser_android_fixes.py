"""HC321-UAT1 — consumer browser + Android screenshot policy regression tests."""

from __future__ import annotations

from pathlib import Path

from backend.health_vault.dashboard_service import (
    DashboardService,
    _health_connect_sync_summary,
    _merge_trend_planes,
)
from backend.health_vault.vault_store import VaultStore

ROOT = Path(__file__).resolve().parents[1]
ANDROID_UI = ROOT / "android/app/src/main/java/com/healthchecker/companion/ui"


def test_consumer_launcher_does_not_blanket_flag_secure():
    source = (ANDROID_UI / "ConsumerLauncherActivity.kt").read_text(encoding="utf-8")
    on_create = source.split("fun onCreate", 1)[1].split("fun onResume", 1)[0]
    assert "window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)" not in on_create
    assert "applySecureWindow(false)" in on_create
    assert "SecureWindowPolicy" in source
    assert "refreshSecureWindowFromDom" in source
    policy = (ANDROID_UI / "SecureWindowPolicy.kt").read_text(encoding="utf-8")
    assert "shouldSecureWindow" in policy
    assert "loginSurfaceVisible" in policy


def test_dashboard_js_greeting_avoids_raw_patient_id(tmp_path: Path):
    js = (ROOT / "js/health_vault/dashboard.js").read_text(encoding="utf-8")
    assert "consumerGreeting()" in js
    assert "My Health Dashboard" in js
    assert "Welcome, ${this.patientId" not in js
    assert "Welcome, ${this.patientId ||" not in js


def test_trend_js_labels_health_connect_observational():
    dash = (ROOT / "js/health_vault/dashboard.js").read_text(encoding="utf-8")
    surfaces = (ROOT / "js/health_vault/consumer_surfaces.js").read_text(encoding="utf-8")
    assert 'Health Connect observational' in dash
    assert "combined_clinical_and_health_connect" in dash
    assert 'Health Connect observational' in surfaces


def test_sync_summary_observations_present_without_pairing(tmp_path: Path):
    store = VaultStore(root=tmp_path / "vault", encryption_key=b"U" * 32)
    data = store._read_index()
    data.setdefault("observations", [])
    data["observations"].append(
        {
            "patient_id": "00000",
            "metric_type": "heart_rate",
            "value": 72,
            "measured_at": "2026-08-18T12:00:00Z",
            "source": "health_connect_companion",
            "connector_id": "health_connect",
        }
    )
    store._write_index(data)
    sync = _health_connect_sync_summary(store, "00000")
    assert sync["observation_count"] == 1
    assert sync["sync_state"] == "observations_present"
    assert sync["sync_state"] != "not_configured"


def test_dashboard_summary_exposes_display_name_and_hc_sync(tmp_path: Path):
    store = VaultStore(root=tmp_path / "vault", encryption_key=b"U" * 32)
    store.update_profile({"display_name": "Alex Example"}, patient_id="00000")
    data = store._read_index()
    data.setdefault("observations", [])
    for i, value in enumerate((91, 92, 93)):
        data["observations"].append(
            {
                "patient_id": "00000",
                "metric_type": "oxygen_saturation",
                "value": value,
                "measured_at": f"2026-08-18T11:0{i}:00Z",
                "source": "health_connect_companion",
                "connector_id": "health_connect",
            }
        )
    # Unprovenanced clinical cache must not relabel HC oxygen as clinical/lab.
    data["trends"] = {
        "00000": {
            "oxygen_saturation": {
                "metric": "oxygen_saturation",
                "latest": 90,
                "sample_count": 2,
            },
            "heart_rate": {
                "metric": "heart_rate",
                "latest": 70,
                "sample_count": 3,
                "provenance": "clinical",
                "data_plane": "clinical",
            },
        }
    }
    data["observations"].extend(
        {
            "patient_id": "00000",
            "metric_type": "heart_rate",
            "value": v,
            "measured_at": f"2026-08-18T12:0{i}:00Z",
            "source": "health_connect_companion",
            "connector_id": "health_connect",
        }
        for i, v in enumerate((100, 101, 102))
    )
    store._write_index(data)

    summary = DashboardService(store).get_summary("00000").to_dict()
    assert summary["display_name"] == "Alex Example"
    status = next(w for w in summary["widgets"] if w["widget_id"] == "status_summary")["payload"]
    assert status["health_connect_sync"]["sync_state"] == "observations_present"
    assert status["health_connect_observation_count"] == 6
    trends = next(w for w in summary["widgets"] if w["widget_id"] == "trends_widget")["payload"]["trends"]
    assert trends["oxygen_saturation"]["provenance"] == "health_connect_observational"
    assert trends["heart_rate"]["provenance"] == "combined_clinical_and_health_connect"


def test_merge_planes_keeps_clinical_only_clinical():
    merged = _merge_trend_planes(
        {"creatinine": {"metric": "creatinine", "latest": 1.1, "provenance": "clinical"}},
        {},
    )
    assert merged["creatinine"]["provenance"] == "clinical"
