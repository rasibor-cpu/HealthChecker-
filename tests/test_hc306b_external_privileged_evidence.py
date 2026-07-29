from __future__ import annotations

import json
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.health_vault.companion_host import (  # noqa: E402
    ActivationError,
    EvidenceContext,
    EvidenceValidationError,
    MODE_EXTERNAL_EVIDENCE,
    MODE_LEGACY_SAME_PROCESS,
    SCHEMA_VERSION_V1,
    TrustedSigner,
    append_evidence_record_append_only,
    compute_evidence_sha256,
    compute_evidence_signature,
    load_and_validate_activation,
    validate_preflight_mode,
    validate_privileged_evidence_bundle,
)


def _now() -> datetime:
    return datetime(2026, 7, 29, 3, 42, 15, tzinfo=timezone.utc)


def _ctx() -> EvidenceContext:
    return EvidenceContext(
        hostname="healthchecker-host",
        machine_identifier="SID:S-1-5-21-example",
        windows_boot_time="2026-07-29T01:00:00Z",
        repository_path=str(ROOT),
        branch="main",
        head_commit="de54b169c006b69cb814e39ff8e530a42c038e42",
        origin_head="de54b169c006b69cb814e39ff8e530a42c038e42",
        tailscale_node_id="n1234567890abcdef",
        tailscale_dns_name="healthchecker-host.tail76ad4e.ts.net.",
        tailscale_ipv4="100.96.145.21",
    )


def _trusted_signers() -> dict[str, TrustedSigner]:
    return {
        "ops-elevated-1": TrustedSigner(
            signer_id="ops-elevated-1",
            algorithm="hmac-sha256",
            key=b"0123456789abcdef0123456789abcdef",
        )
    }


def _bundle(now: datetime | None = None, *, sequence: int = 10) -> dict:
    now = now or _now()
    ts = now.isoformat().replace("+00:00", "Z")
    out = {
        "schema_version": SCHEMA_VERSION_V1,
        "timestamp_utc": ts,
        "hostname": "healthchecker-host",
        "machine_identifier": "SID:S-1-5-21-example",
        "windows_boot_time": "2026-07-29T01:00:00Z",
        "repository_path": str(ROOT),
        "branch": "main",
        "head_commit": "de54b169c006b69cb814e39ff8e530a42c038e42",
        "origin_head": "de54b169c006b69cb814e39ff8e530a42c038e42",
        "worktree_clean": True,
        "ahead_behind": "0 0",
        "elevation_verified": True,
        "bitlocker_status": {"protection_status": "On", "volume_status": "FullyEncrypted"},
        "filesystem": "NTFS",
        "tailscale_node_id": "n1234567890abcdef",
        "tailscale_dns_name": "healthchecker-host.tail76ad4e.ts.net.",
        "tailscale_ipv4": "100.96.145.21",
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
        "attestation_uuid": str(uuid.UUID("5a52f969-9da6-4560-ae26-2bb4f4de875b")),
        "attestation_sequence": sequence,
        "signer_id": "ops-elevated-1",
        "signature_timestamp_utc": ts,
    }
    out["evidence_sha256"] = compute_evidence_sha256(out)
    out["evidence_signature"] = compute_evidence_signature(
        out, signer_id=out["signer_id"], key=_trusted_signers()["ops-elevated-1"].key
    )
    return out


def test_valid_external_evidence_bundle_passes():
    validate_privileged_evidence_bundle(
        evidence_bundle=_bundle(),
        evidence_context=_ctx(),
        trusted_signers=_trusted_signers(),
        now_utc=_now() + timedelta(seconds=30),
    )


def test_stale_evidence_fails():
    stale = _bundle(_now() - timedelta(minutes=3))
    with pytest.raises(EvidenceValidationError) as ei:
        validate_privileged_evidence_bundle(
            evidence_bundle=stale,
            evidence_context=_ctx(),
            trusted_signers=_trusted_signers(),
            now_utc=_now(),
        )
    assert ei.value.code in {"evidence_ports_stale", "evidence_runtime_inactive_stale"}


def test_missing_required_field_fails():
    b = _bundle()
    del b["tailscale_node_id"]
    b["evidence_sha256"] = compute_evidence_sha256(b)
    b["evidence_signature"] = compute_evidence_signature(
        b, signer_id=b["signer_id"], key=_trusted_signers()["ops-elevated-1"].key
    )
    with pytest.raises(EvidenceValidationError) as ei:
        validate_privileged_evidence_bundle(
            evidence_bundle=b,
            evidence_context=_ctx(),
            trusted_signers=_trusted_signers(),
            now_utc=_now(),
        )
    assert ei.value.code == "evidence_field_missing"


def test_modified_payload_hash_bypass_fails():
    b = _bundle()
    b["hostname"] = "evil-host"
    with pytest.raises(EvidenceValidationError) as ei:
        validate_privileged_evidence_bundle(
            evidence_bundle=b,
            evidence_context=_ctx(),
            trusted_signers=_trusted_signers(),
            now_utc=_now(),
        )
    assert ei.value.code == "evidence_sha256_mismatch"


def test_invalid_signature_fails():
    b = _bundle()
    b["evidence_signature"] = "hmac-sha256:ops-elevated-1:" + ("0" * 64)
    with pytest.raises(EvidenceValidationError) as ei:
        validate_privileged_evidence_bundle(
            evidence_bundle=b,
            evidence_context=_ctx(),
            trusted_signers=_trusted_signers(),
            now_utc=_now(),
        )
    assert ei.value.code == "evidence_signature_mismatch"


def test_wrong_signer_fails():
    b = _bundle()
    b["signer_id"] = "unknown-signer"
    b["evidence_sha256"] = compute_evidence_sha256(b)
    b["evidence_signature"] = compute_evidence_signature(
        b, signer_id=b["signer_id"], key=b"abcdefabcdefabcdefabcdefabcdefab"
    )
    with pytest.raises(EvidenceValidationError) as ei:
        validate_privileged_evidence_bundle(
            evidence_bundle=b,
            evidence_context=_ctx(),
            trusted_signers=_trusted_signers(),
            now_utc=_now(),
        )
    assert ei.value.code == "evidence_signer_untrusted"


def test_wrong_machine_repository_branch_head_fail():
    b = _bundle()
    b["hostname"] = "other-host"
    b["evidence_sha256"] = compute_evidence_sha256(b)
    b["evidence_signature"] = compute_evidence_signature(
        b, signer_id=b["signer_id"], key=_trusted_signers()["ops-elevated-1"].key
    )
    with pytest.raises(EvidenceValidationError) as ei:
        validate_privileged_evidence_bundle(
            evidence_bundle=b,
            evidence_context=_ctx(),
            trusted_signers=_trusted_signers(),
            now_utc=_now(),
        )
    assert ei.value.code == "hostname_mismatch"

    b = _bundle()
    b["repository_path"] = "C:/other/repo"
    b["evidence_sha256"] = compute_evidence_sha256(b)
    b["evidence_signature"] = compute_evidence_signature(
        b, signer_id=b["signer_id"], key=_trusted_signers()["ops-elevated-1"].key
    )
    with pytest.raises(EvidenceValidationError) as ei:
        validate_privileged_evidence_bundle(
            evidence_bundle=b,
            evidence_context=_ctx(),
            trusted_signers=_trusted_signers(),
            now_utc=_now(),
        )
    assert ei.value.code == "repository_path_mismatch"

    b = _bundle()
    b["branch"] = "feature-x"
    b["evidence_sha256"] = compute_evidence_sha256(b)
    b["evidence_signature"] = compute_evidence_signature(
        b, signer_id=b["signer_id"], key=_trusted_signers()["ops-elevated-1"].key
    )
    with pytest.raises(EvidenceValidationError) as ei:
        validate_privileged_evidence_bundle(
            evidence_bundle=b,
            evidence_context=_ctx(),
            trusted_signers=_trusted_signers(),
            now_utc=_now(),
        )
    assert ei.value.code == "branch_mismatch"

    b = _bundle()
    b["head_commit"] = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    b["evidence_sha256"] = compute_evidence_sha256(b)
    b["evidence_signature"] = compute_evidence_signature(
        b, signer_id=b["signer_id"], key=_trusted_signers()["ops-elevated-1"].key
    )
    with pytest.raises(EvidenceValidationError) as ei:
        validate_privileged_evidence_bundle(
            evidence_bundle=b,
            evidence_context=_ctx(),
            trusted_signers=_trusted_signers(),
            now_utc=_now(),
        )
    assert ei.value.code == "head_commit_mismatch"


def test_wrong_tailscale_node_fails():
    b = _bundle()
    b["tailscale_node_id"] = "nwrong"
    b["evidence_sha256"] = compute_evidence_sha256(b)
    b["evidence_signature"] = compute_evidence_signature(
        b, signer_id=b["signer_id"], key=_trusted_signers()["ops-elevated-1"].key
    )
    with pytest.raises(EvidenceValidationError) as ei:
        validate_privileged_evidence_bundle(
            evidence_bundle=b,
            evidence_context=_ctx(),
            trusted_signers=_trusted_signers(),
            now_utc=_now(),
        )
    assert ei.value.code == "tailscale_node_id_mismatch"


def test_replay_uuid_and_sequence_detected(tmp_path: Path):
    b = _bundle()
    append_evidence_record_append_only(evidence_bundle=b, audit_dir=tmp_path)
    with pytest.raises(EvidenceValidationError) as ei:
        validate_privileged_evidence_bundle(
            evidence_bundle=b,
            evidence_context=_ctx(),
            trusted_signers=_trusted_signers(),
            now_utc=_now(),
            audit_dir=tmp_path,
        )
    assert ei.value.code in {"evidence_attestation_replay_detected", "evidence_replay_detected"}

    b2 = _bundle(sequence=9)
    b2["attestation_uuid"] = str(uuid.uuid4())
    b2["evidence_sha256"] = compute_evidence_sha256(b2)
    b2["evidence_signature"] = compute_evidence_signature(
        b2, signer_id=b2["signer_id"], key=_trusted_signers()["ops-elevated-1"].key
    )
    with pytest.raises(EvidenceValidationError) as ei:
        validate_privileged_evidence_bundle(
            evidence_bundle=b2,
            evidence_context=_ctx(),
            trusted_signers=_trusted_signers(),
            now_utc=_now(),
            audit_dir=tmp_path,
        )
    assert ei.value.code == "evidence_sequence_replay_detected"


def test_non_elevated_automation_with_valid_external_evidence_passes():
    validate_preflight_mode(
        mode=MODE_EXTERNAL_EVIDENCE,
        executor_is_elevated=False,
        evidence_bundle=_bundle(),
        evidence_context=_ctx(),
        trusted_signers=_trusted_signers(),
        now_utc=_now(),
    )


def test_legacy_mode_requires_elevation():
    with pytest.raises(EvidenceValidationError) as ei:
        validate_preflight_mode(mode=MODE_LEGACY_SAME_PROCESS, executor_is_elevated=False)
    assert ei.value.code == "legacy_elevation_required"
    validate_preflight_mode(mode=MODE_LEGACY_SAME_PROCESS, executor_is_elevated=True)


def test_runtime_still_fails_closed_without_runtime_prerequisites(tmp_path: Path):
    # HC-306B preflight can pass in external mode...
    validate_preflight_mode(
        mode=MODE_EXTERNAL_EVIDENCE,
        executor_is_elevated=False,
        evidence_bundle=_bundle(),
        evidence_context=_ctx(),
        trusted_signers=_trusted_signers(),
        now_utc=_now(),
    )
    # ...but runtime activation remains independently fail-closed.
    env = {
        "HC_HOST_ACTIVATION": "",
        "HC_COMPANION_ADMIN_TOKEN": "test-admin-token-24chars-min!!",
        "HC_COMPANION_PEPPER": "test-pepper-value-24chars-min!!",
        "HC_PROXY_SHARED_TOKEN": "test-proxy-shared-token-24min!!",
        "HC_MONITORING_VAULT_ROOT": str(tmp_path / "monitoring_vault"),
        "HC_TRUSTED_PROXY_MODE": "tailscale_https",
        "HC_EXTERNAL_HTTPS_ORIGIN": "https://healthchecker-host.tail76ad4e.ts.net",
        "HC_BIND_HOST": "127.0.0.1",
        "HC_BIND_PORT": "8743",
    }
    with pytest.raises(ActivationError) as ei:
        load_and_validate_activation(environ=env, repo_root=ROOT)
    assert ei.value.code == "host_activation_required"


def test_audit_records_are_append_only(tmp_path: Path):
    b = _bundle()
    json_path, sha_path = append_evidence_record_append_only(evidence_bundle=b, audit_dir=tmp_path)
    assert json_path.exists()
    assert sha_path.exists()
    body = json.loads(json_path.read_text(encoding="utf-8"))
    assert body["evidence_sha256"] == b["evidence_sha256"]
    with pytest.raises(EvidenceValidationError) as ei:
        append_evidence_record_append_only(evidence_bundle=b, audit_dir=tmp_path)
    assert ei.value.code == "evidence_record_exists"


def test_atomic_write_collision_only_one_writer_wins(tmp_path: Path):
    b = _bundle()
    results: list[str] = []

    def _write():
        try:
            append_evidence_record_append_only(evidence_bundle=b, audit_dir=tmp_path)
            results.append("ok")
        except EvidenceValidationError:
            results.append("exists")

    t1 = threading.Thread(target=_write)
    t2 = threading.Thread(target=_write)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert sorted(results) == ["exists", "ok"]


def test_policy_failures_rejected():
    signers = _trusted_signers()
    b = _bundle()
    b["elevation_verified"] = False
    b["evidence_sha256"] = compute_evidence_sha256(b)
    b["evidence_signature"] = compute_evidence_signature(
        b, signer_id=b["signer_id"], key=signers["ops-elevated-1"].key
    )
    with pytest.raises(EvidenceValidationError) as ei:
        validate_privileged_evidence_bundle(evidence_bundle=b, evidence_context=_ctx(), trusted_signers=signers)
    assert ei.value.code == "elevation_verified_policy_failed"

    b = _bundle()
    b["companion_process_running"] = True
    b["evidence_sha256"] = compute_evidence_sha256(b)
    b["evidence_signature"] = compute_evidence_signature(
        b, signer_id=b["signer_id"], key=signers["ops-elevated-1"].key
    )
    with pytest.raises(EvidenceValidationError) as ei:
        validate_privileged_evidence_bundle(evidence_bundle=b, evidence_context=_ctx(), trusted_signers=signers)
    assert ei.value.code == "companion_process_running_policy_failed"

    b = _bundle()
    b["required_ports"]["8744"] = "LISTEN"
    b["evidence_sha256"] = compute_evidence_sha256(b)
    b["evidence_signature"] = compute_evidence_signature(
        b, signer_id=b["signer_id"], key=signers["ops-elevated-1"].key
    )
    with pytest.raises(EvidenceValidationError) as ei:
        validate_privileged_evidence_bundle(evidence_bundle=b, evidence_context=_ctx(), trusted_signers=signers)
    assert ei.value.code == "required_ports_policy_failed"

    b = _bundle()
    b["worktree_clean"] = False
    b["evidence_sha256"] = compute_evidence_sha256(b)
    b["evidence_signature"] = compute_evidence_signature(
        b, signer_id=b["signer_id"], key=signers["ops-elevated-1"].key
    )
    with pytest.raises(EvidenceValidationError) as ei:
        validate_privileged_evidence_bundle(evidence_bundle=b, evidence_context=_ctx(), trusted_signers=signers)
    assert ei.value.code == "worktree_clean_policy_failed"


def test_signature_expired_fails():
    signers = _trusted_signers()
    b = _bundle(now=_now())
    old_sig = (_now() - timedelta(minutes=11)).isoformat().replace("+00:00", "Z")
    b["signature_timestamp_utc"] = old_sig
    b["evidence_sha256"] = compute_evidence_sha256(b)
    b["evidence_signature"] = compute_evidence_signature(
        b, signer_id=b["signer_id"], key=signers["ops-elevated-1"].key
    )
    with pytest.raises(EvidenceValidationError) as ei:
        validate_privileged_evidence_bundle(
            evidence_bundle=b,
            evidence_context=_ctx(),
            trusted_signers=signers,
            now_utc=_now(),
        )
    assert ei.value.code == "evidence_signature_stale"
