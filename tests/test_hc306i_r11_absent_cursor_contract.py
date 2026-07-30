"""HC-306I-R11 — absent-cursor contract and non-final chunk semantics."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.health_vault.api import companion_observations_handler
from backend.health_vault.companion.delivery import resolve_cursor_advancement_request
from backend.health_vault.companion.security import MAX_OBSERVATIONS_PER_BATCH, MAX_PAYLOAD_BYTES
from backend.health_vault.companion.pairing import CompanionPairingService
from backend.health_vault.models import utc_now
from backend.health_vault.vault_store import VaultStore


@pytest.fixture()
def store(tmp_path: Path) -> VaultStore:
    return VaultStore(root=tmp_path / "vault")


def _pair(store: VaultStore) -> tuple[str, str]:
    start = CompanionPairingService(store=store).start_pairing(display_name="R11 Phone")
    conf = CompanionPairingService(store=store).confirm_pairing(
        pair_code=start["pair_code"], device_label="R11"
    )
    assert conf["ok"] is True
    return conf["device_id"], conf["device_token"]


def _obs(i: int = 1, metric: str = "steps"):
    return {
        "observation_id": f"obs-{i}",
        "source_record_id": f"hc-rec-{i}",
        "metric_type": metric,
        "value": float(i),
        "unit": "count",
        "measured_at": f"2026-07-30T12:{i:02d}:00Z",
        "acquisition_mode": "DELAYED",
    }


def _body(observations=None, **extra):
    base = {
        "batch_id": extra.pop("batch_id", "batch-1"),
        "nonce": extra.pop("nonce", "nonce-1"),
        "sent_at": extra.pop("sent_at", utc_now()),
        "observations": observations if observations is not None else [_obs()],
    }
    base.update(extra)
    return base


def test_resolve_absent_present_invalid_cursor_modes():
    assert resolve_cursor_advancement_request({}) == ("absent", None)
    assert resolve_cursor_advancement_request({"observations": []}) == ("absent", None)
    mode, cur = resolve_cursor_advancement_request({"next_cursor": {"changes_token": " tok-1 "}})
    assert mode == "present"
    assert cur == {"changes_token": "tok-1"}
    assert resolve_cursor_advancement_request({"next_cursor": None})[0] == "invalid"
    assert resolve_cursor_advancement_request({"next_cursor": {"changes_token": None}})[0] == "invalid"
    assert resolve_cursor_advancement_request({"next_cursor": {"changes_token": ""}})[0] == "invalid"
    assert resolve_cursor_advancement_request({"next_cursor": "tok"})[0] == "invalid"
    assert resolve_cursor_advancement_request({"cursor": {"changes_token": "legacy"}})[0] == "present"


def test_missing_next_cursor_does_not_alter_host_cursor(store: VaultStore):
    _, token = _pair(store)
    # Seed an existing cursor
    from backend.health_vault.monitoring.ingestion import IngestionCoordinator

    coord = IngestionCoordinator(store=store)
    coord.save_cursor(
        "health_connect", {"changes_token": "prior-tok"}, patient_id="default-patient"
    )
    out = companion_observations_handler(
        _body(batch_id="nofinal", nonce="n-nofinal", observations=[_obs(1)]),
        authorization="Bearer " + token,
        store=store,
        local_dev=True,
    )
    assert out["ok"] is True
    assert out["cursor_advanced"] is False
    assert out["cursor"] == {"changes_token": "prior-tok"}
    assert coord.get_cursor("health_connect", patient_id="default-patient") == {
        "changes_token": "prior-tok"
    }


def test_nonfinal_ack_reports_cursor_advanced_false(store: VaultStore):
    _, token = _pair(store)
    out = companion_observations_handler(
        _body(batch_id="chunk-a", nonce="n-a", observations=[_obs(2)]),
        authorization="Bearer " + token,
        store=store,
        local_dev=True,
    )
    assert out["ok"] is True
    assert out["status"] == "accepted"
    assert out["cursor_advanced"] is False
    assert int(out["stored"] or 0) == 1


def test_explicit_valid_final_cursor_advances_once(store: VaultStore):
    _, token = _pair(store)
    from backend.health_vault.monitoring.ingestion import IngestionCoordinator

    coord = IngestionCoordinator(store=store)
    body = _body(
        batch_id="final-once",
        nonce="n-final",
        next_cursor={"changes_token": "final-tok"},
        observations=[_obs(3)],
    )
    first = companion_observations_handler(
        body, authorization="Bearer " + token, store=store, local_dev=True
    )
    assert first["ok"] is True
    assert first["cursor_advanced"] is True
    assert first["cursor"] == {"changes_token": "final-tok"}
    assert coord.get_cursor("health_connect", patient_id="default-patient") == {
        "changes_token": "final-tok"
    }
    # Second delivery with different batch must not rewrite unless requested
    second = companion_observations_handler(
        _body(batch_id="nofinal-2", nonce="n-2", observations=[_obs(4)]),
        authorization="Bearer " + token,
        store=store,
        local_dev=True,
    )
    assert second["cursor_advanced"] is False
    assert coord.get_cursor("health_connect", patient_id="default-patient") == {
        "changes_token": "final-tok"
    }


def test_explicit_null_and_malformed_cursor_fail_closed(store: VaultStore):
    _, token = _pair(store)
    for body in (
        _body(batch_id="null-c", nonce="n-null", next_cursor=None, observations=[_obs(5)]),
        _body(
            batch_id="null-tok",
            nonce="n-null-tok",
            next_cursor={"changes_token": None},
            observations=[_obs(6)],
        ),
        _body(
            batch_id="empty-tok",
            nonce="n-empty",
            next_cursor={"changes_token": "  "},
            observations=[_obs(7)],
        ),
        _body(
            batch_id="bad-c",
            nonce="n-bad",
            next_cursor="not-an-object",
            observations=[_obs(8)],
        ),
    ):
        out = companion_observations_handler(
            body, authorization="Bearer " + token, store=store, local_dev=True
        )
        assert out["ok"] is False
        assert out["status"] == "malformed"
        assert "next_cursor_invalid" in (out.get("errors") or [])
    assert len(store.list_observations()) == 0


def test_duplicate_nonfinal_ack_idempotent_without_cursor_advance(store: VaultStore):
    _, token = _pair(store)
    body = _body(batch_id="dup-nf", nonce="n-dup-nf", observations=[_obs(9)])
    first = companion_observations_handler(
        body, authorization="Bearer " + token, store=store, local_dev=True
    )
    second = companion_observations_handler(
        body, authorization="Bearer " + token, store=store, local_dev=True
    )
    assert first["ok"] is True
    assert first["cursor_advanced"] is False
    assert second["status"] == "duplicate_ack"
    assert second["idempotent"] is True
    assert second["cursor_advanced"] is False
    assert len(store.list_observations()) == 1


def test_duplicate_final_ack_preserves_original_cursor_result(store: VaultStore):
    _, token = _pair(store)
    body = _body(
        batch_id="dup-final",
        nonce="n-dup-final",
        next_cursor={"changes_token": "final-dup"},
        observations=[_obs(10)],
    )
    first = companion_observations_handler(
        body, authorization="Bearer " + token, store=store, local_dev=True
    )
    second = companion_observations_handler(
        body, authorization="Bearer " + token, store=store, local_dev=True
    )
    assert first["cursor_advanced"] is True
    assert first["cursor"] == {"changes_token": "final-dup"}
    assert second["status"] == "duplicate_ack"
    assert second["cursor_advanced"] is True
    assert second["cursor"] == {"changes_token": "final-dup"}
    assert len(store.list_observations()) == 1


def test_host_limits_unchanged():
    assert MAX_OBSERVATIONS_PER_BATCH == 200
    assert MAX_PAYLOAD_BYTES == 512_000
