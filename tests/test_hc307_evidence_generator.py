"""
HC-307 — Trusted operator evidence generator tests.

Tests the evidence_generator module in isolation using synthetic facts.
Never activates runtime, configures Caddy/Tailscale, or creates production secrets.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.health_vault.companion_host.evidence_generator import (
    build_evidence_bundle,
    collect_host_facts,
    default_evidence_dir,
    is_elevated,
    next_attestation_sequence,
)
from backend.health_vault.companion_host.privileged_evidence import (
    SCHEMA_VERSION_V1,
    EvidenceContext,
    EvidenceValidationError,
    TrustedSigner,
    append_evidence_record_append_only,
    compute_evidence_sha256,
    compute_evidence_signature,
    validate_privileged_evidence_bundle,
)


SIGNER_ID = "test-signer-hc307"
SIGNER_KEY = b"0123456789abcdef0123456789abcdef"


def _now() -> datetime:
    return datetime(2026, 7, 29, 4, 10, 0, tzinfo=timezone.utc)


def _ts(dt: datetime | None = None) -> str:
    return (dt or _now()).isoformat(timespec="seconds").replace("+00:00", "Z")


def _valid_facts(tmp_path: Path | None = None) -> dict:
    ts = _ts()
    return {
        "timestamp_utc": ts,
        "hostname": "test-host",
        "machine_identifier": "UUID:test-machine-id",
        "windows_boot_time": "2026-07-29T01:00:00Z",
        "elevation_verified": True,
        "repository_path": str(ROOT),
        "branch": "main",
        "head_commit": "de54b169c006b69cb814e39ff8e530a42c038e42",
        "origin_head": "de54b169c006b69cb814e39ff8e530a42c038e42",
        "worktree_clean": True,
        "ahead_behind": "0\t0",
        "bitlocker_status": {"protection_status": "On", "volume_status": "FullyEncrypted"},
        "filesystem": "NTFS",
        "tailscale_node_id": "nTestNode123",
        "tailscale_dns_name": "test-host.tailnet.ts.net.",
        "tailscale_ipv4": "100.64.0.1",
        "companion_service_present": False,
        "caddy_running": False,
        "companion_process_running": False,
        "required_ports": {"8743": "FREE", "8744": "FREE", "8765": "FREE", "8877": "FREE"},
        "vault_paths": {
            "programdata_healthchecker": False,
            "monitoring_vault": False,
            "host_env": False,
        },
        "check_timestamps_utc": {
            "elevation_verified": ts,
            "bitlocker_status": ts,
            "workspace": ts,
            "ports": ts,
            "runtime_inactive": ts,
        },
    }


def _signers() -> dict[str, TrustedSigner]:
    return {
        SIGNER_ID: TrustedSigner(
            signer_id=SIGNER_ID,
            algorithm="hmac-sha256",
            key=SIGNER_KEY,
        )
    }


def _ctx() -> EvidenceContext:
    return EvidenceContext(
        hostname="test-host",
        machine_identifier="UUID:test-machine-id",
        windows_boot_time="2026-07-29T01:00:00Z",
        repository_path=str(ROOT),
        branch="main",
        head_commit="de54b169c006b69cb814e39ff8e530a42c038e42",
        origin_head="de54b169c006b69cb814e39ff8e530a42c038e42",
        tailscale_node_id="nTestNode123",
        tailscale_dns_name="test-host.tailnet.ts.net.",
        tailscale_ipv4="100.64.0.1",
    )


# ── Build & validate round-trip ──────────────────────────────────────


def test_build_evidence_bundle_produces_valid_schema():
    facts = _valid_facts()
    bundle = build_evidence_bundle(
        facts, signer_id=SIGNER_ID, signer_key=SIGNER_KEY, attestation_sequence=1,
        now_utc=_now(),
    )
    assert bundle["schema_version"] == SCHEMA_VERSION_V1
    assert "evidence_sha256" in bundle
    assert "evidence_signature" in bundle
    assert "attestation_uuid" in bundle
    assert bundle["attestation_sequence"] == 1
    assert bundle["signer_id"] == SIGNER_ID


def test_generated_bundle_validates_successfully():
    facts = _valid_facts()
    bundle = build_evidence_bundle(
        facts, signer_id=SIGNER_ID, signer_key=SIGNER_KEY, attestation_sequence=1,
        now_utc=_now(),
    )
    validate_privileged_evidence_bundle(
        evidence_bundle=bundle,
        evidence_context=_ctx(),
        trusted_signers=_signers(),
        now_utc=_now() + timedelta(seconds=10),
    )


def test_signature_verification_round_trip():
    facts = _valid_facts()
    bundle = build_evidence_bundle(
        facts, signer_id=SIGNER_ID, signer_key=SIGNER_KEY, attestation_sequence=1,
        now_utc=_now(),
    )
    sig = bundle["evidence_signature"]
    parts = sig.split(":")
    assert len(parts) == 3
    assert parts[0] == "hmac-sha256"
    assert parts[1] == SIGNER_ID


def test_signature_generation_with_wrong_key_fails_validation():
    facts = _valid_facts()
    wrong_key = b"xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    bundle = build_evidence_bundle(
        facts, signer_id=SIGNER_ID, signer_key=wrong_key, attestation_sequence=1,
        now_utc=_now(),
    )
    with pytest.raises(EvidenceValidationError) as ei:
        validate_privileged_evidence_bundle(
            evidence_bundle=bundle,
            evidence_context=_ctx(),
            trusted_signers=_signers(),
            now_utc=_now() + timedelta(seconds=10),
        )
    assert ei.value.code == "evidence_signature_mismatch"


# ── Elevation gate ───────────────────────────────────────────────────


def test_is_elevated_returns_bool():
    result = is_elevated()
    assert isinstance(result, bool)


def test_non_elevated_facts_report_false():
    with patch(
        "backend.health_vault.companion_host.evidence_generator.is_elevated",
        return_value=False,
    ):
        facts = _valid_facts()
        facts["elevation_verified"] = False
        bundle = build_evidence_bundle(
            facts, signer_id=SIGNER_ID, signer_key=SIGNER_KEY, attestation_sequence=1,
        )
        with pytest.raises(EvidenceValidationError) as ei:
            validate_privileged_evidence_bundle(
                evidence_bundle=bundle,
                evidence_context=_ctx(),
                trusted_signers=_signers(),
                now_utc=_now() + timedelta(seconds=10),
            )
        assert ei.value.code == "elevation_verified_policy_failed"


# ── Dirty repository ─────────────────────────────────────────────────


def test_dirty_repository_fails_validation():
    facts = _valid_facts()
    facts["worktree_clean"] = False
    bundle = build_evidence_bundle(
        facts, signer_id=SIGNER_ID, signer_key=SIGNER_KEY, attestation_sequence=1,
        now_utc=_now(),
    )
    with pytest.raises(EvidenceValidationError) as ei:
        validate_privileged_evidence_bundle(
            evidence_bundle=bundle,
            evidence_context=_ctx(),
            trusted_signers=_signers(),
            now_utc=_now() + timedelta(seconds=10),
        )
    assert ei.value.code == "worktree_clean_policy_failed"


# ── Ports occupied ───────────────────────────────────────────────────


def test_ports_occupied_fails_validation():
    facts = _valid_facts()
    facts["required_ports"]["8743"] = "LISTEN"
    bundle = build_evidence_bundle(
        facts, signer_id=SIGNER_ID, signer_key=SIGNER_KEY, attestation_sequence=1,
        now_utc=_now(),
    )
    with pytest.raises(EvidenceValidationError) as ei:
        validate_privileged_evidence_bundle(
            evidence_bundle=bundle,
            evidence_context=_ctx(),
            trusted_signers=_signers(),
            now_utc=_now() + timedelta(seconds=10),
        )
    assert ei.value.code == "required_ports_policy_failed"


# ── Runtime active ───────────────────────────────────────────────────


def test_companion_running_fails_validation():
    facts = _valid_facts()
    facts["companion_process_running"] = True
    bundle = build_evidence_bundle(
        facts, signer_id=SIGNER_ID, signer_key=SIGNER_KEY, attestation_sequence=1,
        now_utc=_now(),
    )
    with pytest.raises(EvidenceValidationError) as ei:
        validate_privileged_evidence_bundle(
            evidence_bundle=bundle,
            evidence_context=_ctx(),
            trusted_signers=_signers(),
            now_utc=_now() + timedelta(seconds=10),
        )
    assert ei.value.code == "companion_process_running_policy_failed"


def test_caddy_running_fails_validation():
    facts = _valid_facts()
    facts["caddy_running"] = True
    bundle = build_evidence_bundle(
        facts, signer_id=SIGNER_ID, signer_key=SIGNER_KEY, attestation_sequence=1,
        now_utc=_now(),
    )
    with pytest.raises(EvidenceValidationError) as ei:
        validate_privileged_evidence_bundle(
            evidence_bundle=bundle,
            evidence_context=_ctx(),
            trusted_signers=_signers(),
            now_utc=_now() + timedelta(seconds=10),
        )
    assert ei.value.code == "caddy_running_policy_failed"


# ── BitLocker unavailable ────────────────────────────────────────────


def test_bitlocker_off_fails_validation():
    facts = _valid_facts()
    facts["bitlocker_status"] = {"protection_status": "Off", "volume_status": "FullyDecrypted"}
    bundle = build_evidence_bundle(
        facts, signer_id=SIGNER_ID, signer_key=SIGNER_KEY, attestation_sequence=1,
        now_utc=_now(),
    )
    with pytest.raises(EvidenceValidationError) as ei:
        validate_privileged_evidence_bundle(
            evidence_bundle=bundle,
            evidence_context=_ctx(),
            trusted_signers=_signers(),
            now_utc=_now() + timedelta(seconds=10),
        )
    assert ei.value.code == "bitlocker_status_policy_failed"


# ── Attestation sequence ─────────────────────────────────────────────


def test_next_attestation_sequence_empty_dir(tmp_path: Path):
    assert next_attestation_sequence(tmp_path, SIGNER_ID) == 1


def test_next_attestation_sequence_increments(tmp_path: Path):
    facts = _valid_facts()
    bundle = build_evidence_bundle(
        facts, signer_id=SIGNER_ID, signer_key=SIGNER_KEY, attestation_sequence=5,
        now_utc=_now(),
    )
    append_evidence_record_append_only(evidence_bundle=bundle, audit_dir=tmp_path)
    assert next_attestation_sequence(tmp_path, SIGNER_ID) == 6


# ── Append-only output ───────────────────────────────────────────────


def test_append_only_creates_files(tmp_path: Path):
    facts = _valid_facts()
    bundle = build_evidence_bundle(
        facts, signer_id=SIGNER_ID, signer_key=SIGNER_KEY, attestation_sequence=1,
        now_utc=_now(),
    )
    json_path, sha_path = append_evidence_record_append_only(
        evidence_bundle=bundle, audit_dir=tmp_path,
    )
    assert json_path.exists()
    assert sha_path.exists()
    body = json.loads(json_path.read_text(encoding="utf-8"))
    assert body["evidence_sha256"] == bundle["evidence_sha256"]


def test_append_only_rejects_overwrite(tmp_path: Path):
    facts = _valid_facts()
    bundle = build_evidence_bundle(
        facts, signer_id=SIGNER_ID, signer_key=SIGNER_KEY, attestation_sequence=1,
        now_utc=_now(),
    )
    append_evidence_record_append_only(evidence_bundle=bundle, audit_dir=tmp_path)
    with pytest.raises(EvidenceValidationError) as ei:
        append_evidence_record_append_only(evidence_bundle=bundle, audit_dir=tmp_path)
    assert ei.value.code == "evidence_record_exists"


# ── Default evidence dir ─────────────────────────────────────────────


def test_default_evidence_dir_returns_path():
    d = default_evidence_dir()
    assert isinstance(d, Path)
    assert "HealthChecker" in str(d)
    assert "RuntimeEvidence" in str(d)


# ── Bundle validation with audit replay ──────────────────────────────


def test_bundle_validation_detects_replay_in_audit(tmp_path: Path):
    facts = _valid_facts()
    bundle = build_evidence_bundle(
        facts, signer_id=SIGNER_ID, signer_key=SIGNER_KEY, attestation_sequence=1,
        now_utc=_now(),
    )
    append_evidence_record_append_only(evidence_bundle=bundle, audit_dir=tmp_path)
    with pytest.raises(EvidenceValidationError) as ei:
        validate_privileged_evidence_bundle(
            evidence_bundle=bundle,
            evidence_context=_ctx(),
            trusted_signers=_signers(),
            now_utc=_now() + timedelta(seconds=10),
            audit_dir=tmp_path,
        )
    assert "replay" in ei.value.code
