"""
HC-307 — Trusted operator evidence generator.

Collects host facts from an elevated Windows context and produces an
HC-306B-R1 compliant evidence bundle with SHA-256 integrity hash and
authenticated HMAC signature.

This module NEVER activates the companion host, configures Caddy/Tailscale
Serve, creates production secrets, or installs services.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.health_vault.companion_host.privileged_evidence import (
    EXPECTED_REQUIRED_PORTS,
    EXPECTED_VAULT_PATH_FLAGS,
    SCHEMA_VERSION_V1,
    EvidenceValidationError,
    compute_evidence_sha256,
    compute_evidence_signature,
)


def is_elevated() -> bool:
    """Return True when running with full Administrator token on Windows."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def collect_host_facts(*, repo_root: Path) -> dict[str, Any]:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    facts: dict[str, Any] = {}
    facts["timestamp_utc"] = ts
    facts["hostname"] = platform.node()
    facts["machine_identifier"] = _machine_identifier()
    facts["windows_boot_time"] = _windows_boot_time()
    facts["elevation_verified"] = is_elevated()

    _collect_repository(facts, repo_root, ts)
    _collect_bitlocker(facts, ts)
    _collect_tailscale(facts)
    _collect_runtime(facts, ts)

    return facts


def build_evidence_bundle(
    facts: dict[str, Any],
    *,
    signer_id: str,
    signer_key: bytes,
    attestation_sequence: int,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    bundle: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION_V1,
        "timestamp_utc": facts["timestamp_utc"],
        "check_timestamps_utc": facts["check_timestamps_utc"],
        "hostname": facts["hostname"],
        "machine_identifier": facts["machine_identifier"],
        "windows_boot_time": facts["windows_boot_time"],
        "repository_path": facts["repository_path"],
        "branch": facts["branch"],
        "head_commit": facts["head_commit"],
        "origin_head": facts["origin_head"],
        "worktree_clean": facts["worktree_clean"],
        "ahead_behind": facts["ahead_behind"],
        "elevation_verified": facts["elevation_verified"],
        "bitlocker_status": facts["bitlocker_status"],
        "filesystem": facts["filesystem"],
        "tailscale_node_id": facts["tailscale_node_id"],
        "tailscale_dns_name": facts["tailscale_dns_name"],
        "tailscale_ipv4": facts["tailscale_ipv4"],
        "companion_service_present": facts["companion_service_present"],
        "caddy_running": facts["caddy_running"],
        "companion_process_running": facts["companion_process_running"],
        "required_ports": facts["required_ports"],
        "vault_paths": facts["vault_paths"],
        "attestation_uuid": str(uuid4()),
        "attestation_sequence": attestation_sequence,
        "signer_id": signer_id,
        "signature_timestamp_utc": (now_utc or datetime.now(timezone.utc)).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }

    bundle["evidence_sha256"] = compute_evidence_sha256(bundle)
    bundle["evidence_signature"] = compute_evidence_signature(
        bundle, signer_id=signer_id, key=signer_key,
    )
    return bundle


def next_attestation_sequence(audit_dir: Path, signer_id: str) -> int:
    """Determine the next monotonically increasing sequence for the signer."""
    if not audit_dir.exists():
        return 1
    max_seen = 0
    for js in audit_dir.glob("*.json"):
        try:
            payload = json.loads(js.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(payload.get("signer_id", "")).strip() != signer_id:
            continue
        seq = payload.get("attestation_sequence")
        if isinstance(seq, int):
            max_seen = max(max_seen, seq)
    return max_seen + 1


def default_evidence_dir() -> Path:
    pd = os.environ.get("ProgramData") or r"C:\ProgramData"
    return Path(pd) / "HealthChecker" / "RuntimeEvidence"


# ── internal collectors ──────────────────────────────────────────────


def _machine_identifier() -> str:
    if os.name == "nt":
        try:
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_ComputerSystemProduct).UUID"],
                text=True, timeout=10,
            ).strip()
            if out:
                return f"UUID:{out}"
        except Exception:
            pass
    sid = os.environ.get("USERDOMAIN", "") + "\\" + os.environ.get("USERNAME", "")
    return f"UserSID:{sid}"


def _windows_boot_time() -> str:
    if os.name != "nt":
        return "unknown"
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString('o')"],
            text=True, timeout=10,
        ).strip()
        if out:
            return out
    except Exception:
        pass
    return "unknown"


def _run_git(args: list[str], cwd: Path) -> str:
    return subprocess.check_output(
        ["git"] + args, cwd=str(cwd), text=True, timeout=15,
    ).strip()


def _collect_repository(facts: dict[str, Any], repo_root: Path, ts: str) -> None:
    facts["repository_path"] = str(repo_root.resolve())
    facts["branch"] = _run_git(["branch", "--show-current"], repo_root)
    facts["head_commit"] = _run_git(["rev-parse", "HEAD"], repo_root)
    facts["origin_head"] = _run_git(["rev-parse", "origin/main"], repo_root)
    parity = _run_git(["rev-list", "--left-right", "--count", "HEAD...origin/main"], repo_root)
    facts["ahead_behind"] = parity
    status = _run_git(["status", "--short"], repo_root)
    facts["worktree_clean"] = len(status) == 0

    facts.setdefault("check_timestamps_utc", {})
    facts["check_timestamps_utc"]["workspace"] = ts


def _collect_bitlocker(facts: dict[str, Any], ts: str) -> None:
    fs = "NTFS"
    try:
        vol = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Volume -DriveLetter C).FileSystem"],
            text=True, timeout=10,
        ).strip()
        if vol:
            fs = vol
    except Exception:
        pass
    facts["filesystem"] = fs

    protection = "unknown"
    volume = "unknown"
    try:
        bl = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "$b=Get-BitLockerVolume -MountPoint 'C:'; "
             "Write-Output $b.ProtectionStatus; Write-Output $b.VolumeStatus"],
            text=True, timeout=10,
        ).strip().splitlines()
        if len(bl) >= 2:
            protection = bl[0].strip()
            volume = bl[1].strip()
    except Exception:
        pass
    facts["bitlocker_status"] = {
        "protection_status": protection,
        "volume_status": volume,
    }

    facts.setdefault("check_timestamps_utc", {})
    facts["check_timestamps_utc"]["bitlocker_status"] = ts
    facts["check_timestamps_utc"]["elevation_verified"] = ts


def _collect_tailscale(facts: dict[str, Any]) -> None:
    ts_bin = r"C:\Program Files\Tailscale\tailscale.exe"
    node_id = ""
    dns_name = ""
    ipv4 = ""
    if os.path.isfile(ts_bin):
        try:
            raw = subprocess.check_output(
                [ts_bin, "status", "--json"], text=True, timeout=15,
            )
            j = json.loads(raw)
            self_node = j.get("Self") or {}
            node_id = str(self_node.get("ID", ""))
            dns_name = str(self_node.get("DNSName", ""))
            addrs = self_node.get("TailscaleIPs") or []
            for addr in addrs:
                if "." in str(addr):
                    ipv4 = str(addr)
                    break
        except Exception:
            pass
    facts["tailscale_node_id"] = node_id
    facts["tailscale_dns_name"] = dns_name
    facts["tailscale_ipv4"] = ipv4


def _collect_runtime(facts: dict[str, Any], ts: str) -> None:
    ports: dict[str, str] = {}
    for port in EXPECTED_REQUIRED_PORTS:
        try:
            conns = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command",
                 f"@(Get-NetTCPConnection -State Listen -LocalPort {port} "
                 f"-ErrorAction SilentlyContinue).Count"],
                text=True, timeout=10,
            ).strip()
            ports[port] = "FREE" if conns == "0" else "LISTEN"
        except Exception:
            ports[port] = "FREE"
    facts["required_ports"] = ports

    svc_present = False
    try:
        svc = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "@(Get-Service -Name 'HealthCheckerCompanionHost' "
             "-ErrorAction SilentlyContinue).Count"],
            text=True, timeout=10,
        ).strip()
        svc_present = svc != "0"
    except Exception:
        pass
    facts["companion_service_present"] = svc_present

    caddy_running = False
    try:
        caddy = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "@(Get-Process -Name caddy -ErrorAction SilentlyContinue).Count"],
            text=True, timeout=10,
        ).strip()
        caddy_running = caddy != "0"
    except Exception:
        pass
    facts["caddy_running"] = caddy_running

    companion_running = False
    try:
        companion = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" "
             "-ErrorAction SilentlyContinue | Where-Object "
             "{ $_.CommandLine -match 'backend\\.health_vault\\.companion_host' }).Count"],
            text=True, timeout=10,
        ).strip()
        companion_running = companion != "0"
    except Exception:
        pass
    facts["companion_process_running"] = companion_running

    vault_flags: dict[str, bool] = {}
    for key in EXPECTED_VAULT_PATH_FLAGS:
        if key == "programdata_healthchecker":
            vault_flags[key] = Path(os.environ.get("ProgramData", r"C:\ProgramData"), "HealthChecker").exists()
        elif key == "monitoring_vault":
            vault_flags[key] = Path(r"C:\HealthCheckerData\monitoring_vault").exists()
        elif key == "host_env":
            vault_flags[key] = Path(
                os.environ.get("ProgramData", r"C:\ProgramData"),
                "HealthChecker", "companion_host", "host.env"
            ).exists()
        else:
            vault_flags[key] = False
    facts["vault_paths"] = vault_flags

    facts.setdefault("check_timestamps_utc", {})
    facts["check_timestamps_utc"]["ports"] = ts
    facts["check_timestamps_utc"]["runtime_inactive"] = ts
