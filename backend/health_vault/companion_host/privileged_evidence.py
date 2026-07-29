"""
HC-306B external privileged evidence model for host preflight.

This module validates operator-provided privileged evidence (Mode B) and
retains support for legacy same-process elevation gating (Mode A).
It does not activate runtime and does not weaken activation fail-closed checks.
"""

from __future__ import annotations

import base64
import hmac
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

MODE_LEGACY_SAME_PROCESS = "legacy_same_process"
MODE_EXTERNAL_EVIDENCE = "external_privileged_evidence"
SCHEMA_VERSION_V1 = "hc306b.external_evidence.v1.r1"

REQUIRED_FIELDS = (
    "schema_version",
    "timestamp_utc",
    "check_timestamps_utc",
    "hostname",
    "machine_identifier",
    "windows_boot_time",
    "repository_path",
    "branch",
    "head_commit",
    "origin_head",
    "worktree_clean",
    "ahead_behind",
    "elevation_verified",
    "bitlocker_status",
    "filesystem",
    "tailscale_node_id",
    "tailscale_dns_name",
    "tailscale_ipv4",
    "companion_service_present",
    "caddy_running",
    "companion_process_running",
    "required_ports",
    "vault_paths",
    "attestation_uuid",
    "attestation_sequence",
    "signer_id",
    "signature_timestamp_utc",
    "evidence_signature",
    "evidence_sha256",
)

HEX_40 = re.compile(r"^[0-9a-f]{40}$")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_REQUIRED_PORTS = ("8743", "8744", "8765", "8877")
EXPECTED_VAULT_PATH_FLAGS = ("programdata_healthchecker", "monitoring_vault", "host_env")
EXPECTED_CHECK_TIMESTAMPS = (
    "elevation_verified",
    "bitlocker_status",
    "workspace",
    "ports",
    "runtime_inactive",
)
BITLOCKER_PROTECTION_STATUS = frozenset({"on", "off", "unknown"})
BITLOCKER_VOLUME_STATUS = frozenset({"fullyencrypted", "encryptioninprogress", "fullydecrypted", "unknown"})
FILESYSTEM_ALLOWED = frozenset({"ntfs"})


class EvidenceValidationError(ValueError):
    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class FreshnessPolicy:
    elevation_max_age: timedelta = timedelta(minutes=10)
    bitlocker_max_age: timedelta = timedelta(minutes=30)
    workspace_max_age: timedelta = timedelta(minutes=5)
    ports_max_age: timedelta = timedelta(minutes=2)
    runtime_inactive_max_age: timedelta = timedelta(minutes=2)
    signature_max_age: timedelta = timedelta(minutes=10)


@dataclass(frozen=True)
class EvidenceContext:
    hostname: str
    machine_identifier: str
    windows_boot_time: str
    repository_path: str
    branch: str
    head_commit: str
    origin_head: str
    tailscale_node_id: str
    tailscale_dns_name: str
    tailscale_ipv4: str


@dataclass(frozen=True)
class TrustedSigner:
    signer_id: str
    algorithm: str
    key: bytes


def validate_preflight_mode(
    *,
    mode: str,
    executor_is_elevated: bool | None = None,
    evidence_bundle: dict[str, Any] | None = None,
    evidence_context: EvidenceContext | None = None,
    freshness_policy: FreshnessPolicy | None = None,
    now_utc: datetime | None = None,
    known_evidence_hashes: set[str] | None = None,
    known_attestation_ids: set[str] | None = None,
    known_attestation_sequences: dict[str, int] | None = None,
    trusted_signers: dict[str, TrustedSigner] | None = None,
    audit_dir: Path | None = None,
) -> None:
    if mode == MODE_LEGACY_SAME_PROCESS:
        if executor_is_elevated is not True:
            raise EvidenceValidationError("legacy_elevation_required")
        return
    if mode == MODE_EXTERNAL_EVIDENCE:
        if evidence_bundle is None or evidence_context is None:
            raise EvidenceValidationError("external_evidence_required")
        validate_privileged_evidence_bundle(
            evidence_bundle=evidence_bundle,
            evidence_context=evidence_context,
            freshness_policy=freshness_policy,
            now_utc=now_utc,
            known_evidence_hashes=known_evidence_hashes,
            known_attestation_ids=known_attestation_ids,
            known_attestation_sequences=known_attestation_sequences,
            trusted_signers=trusted_signers,
            audit_dir=audit_dir,
        )
        return
    raise EvidenceValidationError("preflight_mode_invalid")


def validate_privileged_evidence_bundle(
    *,
    evidence_bundle: dict[str, Any],
    evidence_context: EvidenceContext,
    freshness_policy: FreshnessPolicy | None = None,
    now_utc: datetime | None = None,
    known_evidence_hashes: set[str] | None = None,
    known_attestation_ids: set[str] | None = None,
    known_attestation_sequences: dict[str, int] | None = None,
    trusted_signers: dict[str, TrustedSigner] | None = None,
    audit_dir: Path | None = None,
) -> None:
    policy = freshness_policy or FreshnessPolicy()
    current = now_utc or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    for field in REQUIRED_FIELDS:
        if field not in evidence_bundle:
            raise EvidenceValidationError("evidence_field_missing")

    if evidence_bundle.get("schema_version") != SCHEMA_VERSION_V1:
        raise EvidenceValidationError("evidence_schema_version_invalid")

    ts_global = _parse_utc_timestamp(evidence_bundle.get("timestamp_utc"), "evidence_timestamp_invalid")
    _verify_attestation_uuid(evidence_bundle.get("attestation_uuid"))
    signer_id = _require_non_empty_str("signer_id", evidence_bundle.get("signer_id"))
    _verify_attestation_sequence(evidence_bundle.get("attestation_sequence"))
    sig_ts = _parse_utc_timestamp(evidence_bundle.get("signature_timestamp_utc"), "evidence_signature_timestamp_invalid")
    _verify_evidence_hash(evidence_bundle)
    _verify_evidence_signature(evidence_bundle, trusted_signers=trusted_signers)

    _require_str_eq("hostname", evidence_bundle.get("hostname"), evidence_context.hostname)
    _require_str_eq(
        "machine_identifier", evidence_bundle.get("machine_identifier"), evidence_context.machine_identifier
    )
    _require_str_eq(
        "windows_boot_time", evidence_bundle.get("windows_boot_time"), evidence_context.windows_boot_time
    )
    _require_path_eq("repository_path", evidence_bundle.get("repository_path"), evidence_context.repository_path)
    _require_str_eq("branch", evidence_bundle.get("branch"), evidence_context.branch)
    _require_git_sha("head_commit", evidence_bundle.get("head_commit"))
    _require_git_sha("origin_head", evidence_bundle.get("origin_head"))
    _require_str_eq("head_commit", evidence_bundle.get("head_commit"), evidence_context.head_commit)
    _require_str_eq("origin_head", evidence_bundle.get("origin_head"), evidence_context.origin_head)
    _require_ahead_behind_zero(evidence_bundle.get("ahead_behind"))
    _require_bool_eq("worktree_clean", evidence_bundle.get("worktree_clean"), expected=True)
    _require_bool_eq("elevation_verified", evidence_bundle.get("elevation_verified"), expected=True)
    _validate_bitlocker_status(evidence_bundle.get("bitlocker_status"))
    _validate_filesystem(evidence_bundle.get("filesystem"))
    _require_str_eq("tailscale_node_id", evidence_bundle.get("tailscale_node_id"), evidence_context.tailscale_node_id)
    _require_str_eq(
        "tailscale_dns_name", evidence_bundle.get("tailscale_dns_name"), evidence_context.tailscale_dns_name
    )
    _require_str_eq("tailscale_ipv4", evidence_bundle.get("tailscale_ipv4"), evidence_context.tailscale_ipv4)
    _require_bool_eq("companion_service_present", evidence_bundle.get("companion_service_present"), expected=False)
    _require_bool_eq("caddy_running", evidence_bundle.get("caddy_running"), expected=False)
    _require_bool_eq("companion_process_running", evidence_bundle.get("companion_process_running"), expected=False)
    _validate_required_ports(evidence_bundle.get("required_ports"))
    _validate_vault_paths(evidence_bundle.get("vault_paths"))

    check_ts = evidence_bundle.get("check_timestamps_utc")
    if not isinstance(check_ts, dict):
        raise EvidenceValidationError("evidence_check_timestamps_invalid")
    _validate_check_timestamps_keys(check_ts)

    _assert_fresh(
        _parse_utc_timestamp(check_ts.get("elevation_verified"), "evidence_timestamp_invalid"),
        current,
        policy.elevation_max_age,
        "evidence_elevation_stale",
    )
    _assert_fresh(
        _parse_utc_timestamp(check_ts.get("bitlocker_status"), "evidence_timestamp_invalid"),
        current,
        policy.bitlocker_max_age,
        "evidence_bitlocker_stale",
    )
    _assert_fresh(
        _parse_utc_timestamp(check_ts.get("workspace"), "evidence_timestamp_invalid"),
        current,
        policy.workspace_max_age,
        "evidence_workspace_stale",
    )
    _assert_fresh(
        _parse_utc_timestamp(check_ts.get("ports"), "evidence_timestamp_invalid"),
        current,
        policy.ports_max_age,
        "evidence_ports_stale",
    )
    _assert_fresh(
        _parse_utc_timestamp(check_ts.get("runtime_inactive"), "evidence_timestamp_invalid"),
        current,
        policy.runtime_inactive_max_age,
        "evidence_runtime_inactive_stale",
    )
    _assert_fresh(sig_ts, current, policy.signature_max_age, "evidence_signature_stale")
    _assert_fresh(ts_global, current, policy.workspace_max_age, "evidence_workspace_stale")

    evidence_hash = str(evidence_bundle.get("evidence_sha256", "")).strip().lower()
    if known_evidence_hashes and evidence_hash in {h.strip().lower() for h in known_evidence_hashes}:
        raise EvidenceValidationError("evidence_replay_detected")
    attestation_id = str(evidence_bundle.get("attestation_uuid", "")).strip().lower()
    if known_attestation_ids and attestation_id in {str(v).strip().lower() for v in known_attestation_ids}:
        raise EvidenceValidationError("evidence_attestation_replay_detected")
    sequence = int(evidence_bundle.get("attestation_sequence"))
    if known_attestation_sequences:
        seen = known_attestation_sequences.get(signer_id)
        if seen is not None and sequence <= int(seen):
            raise EvidenceValidationError("evidence_sequence_replay_detected")
    if audit_dir and _evidence_hash_seen_in_audit_dir(audit_dir, evidence_hash):
        raise EvidenceValidationError("evidence_replay_detected")
    if audit_dir and _attestation_seen_in_audit_dir(audit_dir, attestation_id):
        raise EvidenceValidationError("evidence_attestation_replay_detected")
    if audit_dir and _sequence_replayed_in_audit_dir(audit_dir, signer_id, sequence):
        raise EvidenceValidationError("evidence_sequence_replay_detected")


def append_evidence_record_append_only(*, evidence_bundle: dict[str, Any], audit_dir: Path) -> tuple[Path, Path]:
    if "evidence_signature" not in evidence_bundle:
        raise EvidenceValidationError("evidence_signature_missing")
    _verify_evidence_hash(evidence_bundle)
    ts = _parse_utc_timestamp(evidence_bundle.get("timestamp_utc"), "evidence_timestamp_invalid")
    stamp = ts.strftime("%Y-%m-%dT%H-%M-%SZ")
    attestation_uuid = str(evidence_bundle.get("attestation_uuid", "")).strip().lower()
    out_dir = Path(audit_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = f"{stamp}_{attestation_uuid}"
    json_path = out_dir / f"{base}.json"
    sha_path = out_dir / f"{base}.sha256"
    payload = json.dumps(evidence_bundle, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    digest = str(evidence_bundle["evidence_sha256"]).strip().lower()
    try:
        _write_text_exclusive(json_path, payload)
    except FileExistsError as exc:
        raise EvidenceValidationError("evidence_record_exists") from exc
    try:
        _write_text_exclusive(sha_path, f"{digest}  {json_path.name}\n")
    except FileExistsError as exc:
        try:
            json_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise EvidenceValidationError("evidence_record_exists") from exc
    except Exception:
        try:
            json_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    return json_path, sha_path


def _canonical_payload_without_hash(evidence_bundle: dict[str, Any]) -> bytes:
    payload = dict(evidence_bundle)
    payload.pop("evidence_sha256", None)
    payload.pop("evidence_signature", None)
    canon = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return canon.encode("utf-8")


def _verify_evidence_hash(evidence_bundle: dict[str, Any]) -> None:
    evidence_hash = str(evidence_bundle.get("evidence_sha256", "")).strip().lower()
    if not HEX_64.match(evidence_hash):
        raise EvidenceValidationError("evidence_sha256_invalid")
    digest = hashlib.sha256(_canonical_payload_without_hash(evidence_bundle)).hexdigest()
    if digest != evidence_hash:
        raise EvidenceValidationError("evidence_sha256_mismatch")


def compute_evidence_sha256(evidence_bundle_without_hash: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_payload_without_hash(evidence_bundle_without_hash)).hexdigest()


def compute_evidence_signature(
    evidence_bundle_without_signature: dict[str, Any],
    *,
    signer_id: str,
    key: bytes,
    algorithm: str = "hmac-sha256",
) -> str:
    if algorithm != "hmac-sha256":
        raise EvidenceValidationError("evidence_signature_algorithm_invalid")
    signer = str(signer_id or "").strip()
    if not signer:
        raise EvidenceValidationError("signer_id_invalid")
    payload = dict(evidence_bundle_without_signature)
    payload.pop("evidence_signature", None)
    canon = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hmac.new(key, canon, hashlib.sha256).hexdigest()
    return f"hmac-sha256:{signer}:{digest}"


def _verify_evidence_signature(
    evidence_bundle: dict[str, Any],
    *,
    trusted_signers: dict[str, TrustedSigner] | None,
) -> None:
    sig = str(evidence_bundle.get("evidence_signature", "")).strip()
    parts = sig.split(":")
    if len(parts) != 3:
        raise EvidenceValidationError("evidence_signature_invalid")
    algo, signer_from_sig, digest = parts
    if algo != "hmac-sha256" or not HEX_64.match(digest.lower()):
        raise EvidenceValidationError("evidence_signature_invalid")
    signer_id = _require_non_empty_str("signer_id", evidence_bundle.get("signer_id"))
    if signer_from_sig != signer_id:
        raise EvidenceValidationError("evidence_signature_signer_mismatch")

    signers = trusted_signers or _load_trusted_signers_from_env()
    trusted = signers.get(signer_id) if signers else None
    if trusted is None:
        raise EvidenceValidationError("evidence_signer_untrusted")
    if trusted.algorithm != "hmac-sha256":
        raise EvidenceValidationError("evidence_signature_algorithm_invalid")

    payload = dict(evidence_bundle)
    payload.pop("evidence_signature", None)
    canon = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    expected = hmac.new(trusted.key, canon, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, digest.lower()):
        raise EvidenceValidationError("evidence_signature_mismatch")


def _load_trusted_signers_from_env() -> dict[str, TrustedSigner]:
    raw = os.environ.get("HC_EVIDENCE_TRUSTED_SIGNERS_JSON", "").strip()
    if not raw:
        raise EvidenceValidationError("evidence_trusted_signers_missing")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EvidenceValidationError("evidence_trusted_signers_invalid") from exc
    if not isinstance(parsed, dict) or not parsed:
        raise EvidenceValidationError("evidence_trusted_signers_invalid")
    out: dict[str, TrustedSigner] = {}
    for signer_id, cfg in parsed.items():
        if not isinstance(cfg, dict):
            raise EvidenceValidationError("evidence_trusted_signers_invalid")
        algo = str(cfg.get("algorithm", "")).strip()
        if algo != "hmac-sha256":
            raise EvidenceValidationError("evidence_trusted_signers_invalid")
        key_raw = str(cfg.get("key", "")).strip()
        key_bytes = _decode_key_material(key_raw)
        out[str(signer_id).strip()] = TrustedSigner(
            signer_id=str(signer_id).strip(),
            algorithm=algo,
            key=key_bytes,
        )
    return out


def _decode_key_material(value: str) -> bytes:
    if value.startswith("base64:"):
        try:
            raw = base64.b64decode(value[len("base64:") :], validate=True)
        except Exception as exc:
            raise EvidenceValidationError("evidence_trusted_signers_invalid") from exc
    elif value.startswith("hex:"):
        try:
            raw = bytes.fromhex(value[len("hex:") :])
        except ValueError as exc:
            raise EvidenceValidationError("evidence_trusted_signers_invalid") from exc
    else:
        raw = value.encode("utf-8")
    if len(raw) < 32:
        raise EvidenceValidationError("evidence_trusted_signers_invalid")
    return raw


def _parse_utc_timestamp(value: Any, err_code: str) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise EvidenceValidationError(err_code)
    norm = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(norm)
    except ValueError as exc:
        raise EvidenceValidationError(err_code) from exc
    if parsed.tzinfo is None:
        raise EvidenceValidationError(err_code)
    return parsed.astimezone(timezone.utc)


def _assert_fresh(ts: datetime, now_utc: datetime, max_age: timedelta, err_code: str) -> None:
    age = now_utc - ts
    if age < timedelta(0):
        raise EvidenceValidationError("evidence_timestamp_in_future")
    if age > max_age:
        raise EvidenceValidationError(err_code)


def _require_non_empty(field: str, value: Any) -> None:
    if not str(value or "").strip():
        raise EvidenceValidationError(f"{field}_invalid")


def _require_bool(field: str, value: Any) -> None:
    if not isinstance(value, bool):
        raise EvidenceValidationError(f"{field}_invalid")


def _require_bool_eq(field: str, value: Any, *, expected: bool) -> None:
    _require_bool(field, value)
    if value is not expected:
        raise EvidenceValidationError(f"{field}_policy_failed")


def _require_git_sha(field: str, value: Any) -> None:
    raw = str(value or "").strip().lower()
    if not HEX_40.match(raw):
        raise EvidenceValidationError(f"{field}_invalid")


def _normalize_path(value: str) -> str:
    return str(Path(value).resolve()).replace("\\", "/").rstrip("/").lower()


def _require_path_eq(field: str, actual: Any, expected: str) -> None:
    if _normalize_path(str(actual or "")) != _normalize_path(expected):
        raise EvidenceValidationError(f"{field}_mismatch")


def _require_str_eq(field: str, actual: Any, expected: str) -> None:
    if str(actual or "").strip() != str(expected or "").strip():
        raise EvidenceValidationError(f"{field}_mismatch")


def _require_dict(field: str, value: Any) -> None:
    if not isinstance(value, dict) or not value:
        raise EvidenceValidationError(f"{field}_invalid")


def _require_non_empty_str(field: str, value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise EvidenceValidationError(f"{field}_invalid")
    return raw


def _require_ahead_behind_zero(value: Any) -> None:
    if isinstance(value, str):
        raw = value.strip()
        m = re.match(r"^(\d+)\s+(\d+)$", raw)
        if not m:
            raise EvidenceValidationError("ahead_behind_invalid")
        ahead = int(m.group(1))
        behind = int(m.group(2))
        if ahead != 0 or behind != 0:
            raise EvidenceValidationError("ahead_behind_policy_failed")
        return
    if isinstance(value, (list, tuple)) and len(value) == 2 and all(isinstance(v, int) and v >= 0 for v in value):
        if value[0] != 0 or value[1] != 0:
            raise EvidenceValidationError("ahead_behind_policy_failed")
        return
    raise EvidenceValidationError("ahead_behind_invalid")


def _validate_bitlocker_status(value: Any) -> None:
    if not isinstance(value, dict):
        raise EvidenceValidationError("bitlocker_status_invalid")
    required = ("protection_status", "volume_status")
    for k in required:
        if k not in value:
            raise EvidenceValidationError("bitlocker_status_invalid")
    p = str(value.get("protection_status", "")).strip().lower()
    v = str(value.get("volume_status", "")).strip().lower()
    if p not in BITLOCKER_PROTECTION_STATUS or v not in BITLOCKER_VOLUME_STATUS:
        raise EvidenceValidationError("bitlocker_status_invalid")
    if p != "on":
        raise EvidenceValidationError("bitlocker_status_policy_failed")


def _validate_filesystem(value: Any) -> None:
    fs = str(value or "").strip().lower()
    if fs not in FILESYSTEM_ALLOWED:
        raise EvidenceValidationError("filesystem_invalid")


def _validate_required_ports(value: Any) -> None:
    if not isinstance(value, dict):
        raise EvidenceValidationError("required_ports_invalid")
    for port in EXPECTED_REQUIRED_PORTS:
        if port not in value:
            raise EvidenceValidationError("required_ports_invalid")
        state = str(value.get(port, "")).strip().upper()
        if state != "FREE":
            raise EvidenceValidationError("required_ports_policy_failed")


def _validate_vault_paths(value: Any) -> None:
    if not isinstance(value, dict):
        raise EvidenceValidationError("vault_paths_invalid")
    for key in EXPECTED_VAULT_PATH_FLAGS:
        if key not in value or not isinstance(value[key], bool):
            raise EvidenceValidationError("vault_paths_invalid")
    if any(bool(value[key]) for key in EXPECTED_VAULT_PATH_FLAGS):
        raise EvidenceValidationError("vault_paths_policy_failed")


def _verify_attestation_uuid(value: Any) -> None:
    raw = str(value or "").strip()
    try:
        UUID(raw)
    except ValueError as exc:
        raise EvidenceValidationError("attestation_uuid_invalid") from exc


def _verify_attestation_sequence(value: Any) -> None:
    if not isinstance(value, int) or value <= 0:
        raise EvidenceValidationError("attestation_sequence_invalid")


def _validate_check_timestamps_keys(value: dict[str, Any]) -> None:
    for key in EXPECTED_CHECK_TIMESTAMPS:
        if key not in value:
            raise EvidenceValidationError("evidence_check_timestamps_invalid")


def _evidence_hash_seen_in_audit_dir(audit_dir: Path, evidence_hash: str) -> bool:
    if not Path(audit_dir).exists():
        return False
    target = evidence_hash.strip().lower()
    for sha_file in Path(audit_dir).glob("*.sha256"):
        try:
            body = sha_file.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in body.splitlines():
            lhs = line.strip().split()[0].lower() if line.strip() else ""
            if lhs == target:
                return True
    return False


def _attestation_seen_in_audit_dir(audit_dir: Path, attestation_uuid: str) -> bool:
    if not Path(audit_dir).exists():
        return False
    target = attestation_uuid.strip().lower()
    for js in Path(audit_dir).glob("*.json"):
        try:
            payload = json.loads(js.read_text(encoding="utf-8"))
        except Exception:
            continue
        got = str(payload.get("attestation_uuid", "")).strip().lower()
        if got == target:
            return True
    return False


def _sequence_replayed_in_audit_dir(audit_dir: Path, signer_id: str, sequence: int) -> bool:
    if not Path(audit_dir).exists():
        return False
    signer = signer_id.strip()
    max_seen = 0
    for js in Path(audit_dir).glob("*.json"):
        try:
            payload = json.loads(js.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(payload.get("signer_id", "")).strip() != signer:
            continue
        seq_raw = payload.get("attestation_sequence")
        if isinstance(seq_raw, int):
            max_seen = max(max_seen, seq_raw)
    return sequence <= max_seen


def _write_text_exclusive(path: Path, content: str) -> None:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    fd = os.open(str(path), flags)
    try:
        os.write(fd, content.encode("utf-8"))
    finally:
        os.close(fd)
