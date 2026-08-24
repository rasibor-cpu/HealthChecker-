"""HC324 — incremental Health Connect freshness: inventory vs empty change-feed."""

from __future__ import annotations

from backend.health_vault.companion.delivery import merge_iso_latest_maps
from backend.health_vault.freshness_path import build_freshness_path
from backend.health_vault.models import create_measurement
from backend.health_vault.vault_store import VaultStore


def test_empty_batch_does_not_erase_prior_latest_timestamps():
    merged = merge_iso_latest_maps(
        {"heart_rate": "2026-08-18T10:00:00Z"},
        {},
        {"heart_rate": "2026-08-18T10:00:00Z"},
        {},
        {"heart_rate": "2026-08-24T12:00:00Z"},
        {},
    )
    assert merged["heart_rate"] == "2026-08-24T12:00:00Z"


def test_freshness_path_classifies_health_connect_inventory_ahead_of_vault(tmp_path):
    store = VaultStore(root=tmp_path / "vault", encryption_key=b"3" * 32)
    data = store._read_index()
    data["observations"].append(
        {
            "patient_id": "patient-A",
            "observation_id": "hr-18",
            "metric_type": "heart_rate",
            "value": 72,
            "unit": "bpm",
            "measured_at": "2026-08-18T10:00:00Z",
            "source": "health_connect_companion",
            "connector_id": "health_connect",
            "source_record_id": "src-18",
        }
    )
    data["measurements"].append(
        create_measurement(
            document_id="hc-hr",
            metric="heart_rate",
            value=72,
            units="bpm",
            measured_at="2026-08-18T10:00:00Z",
        ).to_dict()
    )
    store._write_index(data)
    store.save_companion_status(
        {
            "last_success_at": "2026-08-24T11:43:00Z",
            "last_attempt_at": "2026-08-24T11:43:00Z",
            "latest_received_by_metric": {"heart_rate": "2026-08-18T10:00:00Z"},
            "health_connect": {
                "latest_by_metric": {"heart_rate": "2026-08-18T10:00:00Z"},
                "inventory_latest_by_metric": {"heart_rate": "2026-08-24T12:35:00Z"},
                "granted_scope": "heart_rate,steps,spo2,sleep",
                "catch_up_applied": True,
            },
        }
    )
    path = build_freshness_path(store, "patient-A")
    assert path["by_metric"]["heart_rate"]["vault_latest_at"].startswith("2026-08-18")
    assert path["by_metric"]["heart_rate"]["health_connect_latest_at"].startswith("2026-08-24")
    assert path["latest_health_connect_inventory_at"].startswith("2026-08-24")
    assert path["break"]["boundary"] == "health_connect_has_newer_than_change_feed"


def test_freshness_path_source_app_boundary_when_inventory_matches_vault(tmp_path):
    store = VaultStore(root=tmp_path / "vault", encryption_key=b"3" * 32)
    data = store._read_index()
    data["observations"].append(
        {
            "patient_id": "patient-A",
            "observation_id": "hr-18",
            "metric_type": "heart_rate",
            "value": 72,
            "unit": "bpm",
            "measured_at": "2026-08-18T10:00:00Z",
            "source": "health_connect_companion",
            "connector_id": "health_connect",
            "source_record_id": "src-18",
        }
    )
    store._write_index(data)
    store.save_companion_status(
        {
            "last_success_at": "2026-08-24T11:43:00Z",
            "health_connect": {
                "latest_by_metric": {"heart_rate": "2026-08-18T10:00:00Z"},
                "inventory_latest_by_metric": {"heart_rate": "2026-08-18T10:00:00Z"},
            },
        }
    )
    path = build_freshness_path(store, "patient-A")
    assert path["break"]["boundary"] == "health_connect_or_source_app_not_writing_newer_samples"


def test_catch_up_contract_in_companion_and_ui_sources():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    reader = (root / "android/app/src/main/java/com/healthchecker/companion/healthconnect/HealthConnectReader.kt").read_text(
        encoding="utf-8"
    )
    catch_up = (root / "android/app/src/main/java/com/healthchecker/companion/healthconnect/FreshnessCatchUp.kt").read_text(
        encoding="utf-8"
    )
    gradle = (root / "android/app/build.gradle.kts").read_text(encoding="utf-8")
    js = (root / "js/health_vault/records.js").read_text(encoding="utf-8")
    sw = (root / "service-worker.js").read_text(encoding="utf-8")
    assert "inventoryLatest" in reader
    assert "catchUpNewest" in reader
    assert "CATCH_UP_MAX_OBSERVATIONS" in catch_up
    assert "versionCode = 324" in gradle
    assert "Latest in Health Connect" in js
    assert 'CACHE_REVISION = "hc324a"' in sw
