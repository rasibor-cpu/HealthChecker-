from __future__ import annotations

from pathlib import Path

import pytest

from backend.health_vault.monitoring.ingestion import IngestionCoordinator
from backend.health_vault.vault_store import VaultStore


class RecordingBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def publish(self, event: str, payload: object) -> None:
        self.events.append((event, payload))


def _obs(i: int) -> dict[str, object]:
    return {
        "metric_type": "heart_rate",
        "value": 60 + (i % 30),
        "unit": "bpm",
        "measured_at": f"2026-08-13T12:{i % 60:02d}:00Z",
        "source_record_id": f"hc310e-batch-{i}",
        "acquisition_mode": "DELAYED",
        "source": "health_connect",
    }


def test_health_connect_batch_180_uses_one_index_read_and_write(tmp_path: Path):
    store = VaultStore(root=tmp_path / "vault")
    coord = IngestionCoordinator(store=store)

    counts = {"reads": 0, "writes": 0}

    original_read = store._read_index
    original_write = store._write_index

    def counted_read():
        counts["reads"] += 1
        return original_read()

    def counted_write(data):
        counts["writes"] += 1
        return original_write(data)

    store._read_index = counted_read
    store._write_index = counted_write

    result = coord.ingest_observations(
        [_obs(i) for i in range(180)],
        connector_id="health_connect",
        evaluate_freshness=False,
        batch_persist=True,
    )

    assert result["durable_success"] is True
    assert int(result["stored"]) == 180
    assert counts["reads"] == 1
    assert counts["writes"] == 1


def test_health_connect_batch_duplicate_fingerprint_is_idempotent(tmp_path: Path):
    store = VaultStore(root=tmp_path / "vault")
    coord = IngestionCoordinator(store=store)

    row = _obs(1)

    result = coord.ingest_observations(
        [row, dict(row)],
        connector_id="health_connect",
        evaluate_freshness=False,
        batch_persist=True,
    )

    assert result["durable_success"] is True
    assert int(result["stored"]) == 1
    assert int(result["skipped"]) == 1

    persisted = store.list_observations()
    assert len(persisted) == 1

    skipped = result["skipped_observations"]
    assert skipped[0]["reason"] in {"duplicate_fingerprint", "duplicate_source_identity"}


def test_health_connect_batch_commit_failure_rolls_back_payloads_and_events(
    tmp_path: Path,
):
    store = VaultStore(root=tmp_path / "vault")
    bus = RecordingBus()
    coord = IngestionCoordinator(store=store, bus=bus)

    original_write = store._write_index

    def fail_commit(data):
        raise OSError("synthetic_batch_commit_failure")

    store._write_index = fail_commit

    with pytest.raises(OSError, match="synthetic_batch_commit_failure"):
        coord.ingest_observations(
            [_obs(10), _obs(11)],
            connector_id="health_connect",
            evaluate_freshness=False,
            batch_persist=True,
        )

    # Restore ability to inspect the durable state.
    store._write_index = original_write

    index = store._read_index()

    assert (index.get("observations") or []) == []
    assert (index.get("documents") or []) == []
    assert (index.get("measurements") or []) == []
    assert (index.get("imports") or []) == []

    # No MONITORING_INGESTED event may escape before durable commit.
    assert bus.events == []

    payloads = list(store.documents_dir.glob("*.bin"))
    assert payloads == []


def test_health_connect_batch_opt_in_only(tmp_path: Path):
    store = VaultStore(root=tmp_path / "vault")
    coord = IngestionCoordinator(store=store)

    calls = {"batch": 0}
    original = coord._ingest_health_connect_batched

    def counted(*args, **kwargs):
        calls["batch"] += 1
        return original(*args, **kwargs)

    coord._ingest_health_connect_batched = counted

    # Ordinary HC-302 route must NOT activate batch persistence implicitly.
    first = coord.ingest_observations(
        [_obs(20)],
        connector_id="health_connect",
        evaluate_freshness=False,
    )
    assert first["durable_success"] is True
    assert calls["batch"] == 0

    # HC-310E companion route explicitly opts in.
    second = coord.ingest_observations(
        [_obs(21)],
        connector_id="health_connect",
        evaluate_freshness=False,
        batch_persist=True,
    )
    assert second["durable_success"] is True
    assert calls["batch"] == 1

def test_health_connect_batch_fingerprint_position_is_patient_scoped(tmp_path: Path):
    store = VaultStore(root=tmp_path / "vault")

    with store.observation_batch() as batch:
        first = {
            "observation_id": "patient-a-observation",
            "patient_id": "patient-a",
            "fingerprint": "shared-fingerprint",
            "metric_type": "heart_rate",
        }
        second = {
            "observation_id": "patient-b-observation",
            "patient_id": "patient-b",
            "fingerprint": "shared-fingerprint",
            "metric_type": "heart_rate",
        }

        store.batch_upsert_observation(batch, first)
        store.batch_upsert_observation(batch, second)

    rows = store.list_observations()
    assert len(rows) == 2

    by_patient = {row["patient_id"]: row for row in rows}
    assert by_patient["patient-a"]["observation_id"] == "patient-a-observation"
    assert by_patient["patient-b"]["observation_id"] == "patient-b-observation"

    assert (
        store.get_observation_by_fingerprint(
            "shared-fingerprint",
            patient_id="patient-a",
        )["observation_id"]
        == "patient-a-observation"
    )
    assert (
        store.get_observation_by_fingerprint(
            "shared-fingerprint",
            patient_id="patient-b",
        )["observation_id"]
        == "patient-b-observation"
    )
