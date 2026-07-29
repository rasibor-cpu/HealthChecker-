# HC-307 — Trusted Operator Evidence Generator
#
# This script MUST be run from an elevated Administrator PowerShell.
# It collects host facts and generates an HC-306B-R1 compliant evidence bundle.
#
# This script NEVER activates the companion host, configures Caddy/Tailscale
# Serve, creates production secrets, or installs services.
#
# Usage:
#   .\Generate-PrivilegedEvidence.ps1 [-RepoRoot C:\rasib\source\healthchecker]
#
# Environment requirements:
#   HC_EVIDENCE_SIGNER_ID      — signer identity (e.g. "ops-elevated-1")
#   HC_EVIDENCE_SIGNER_KEY     — signing key (hex:... or base64:... or raw >=32 chars)
#
# Output:
#   %ProgramData%\HealthChecker\RuntimeEvidence\<timestamp>_<uuid>.json
#   %ProgramData%\HealthChecker\RuntimeEvidence\<timestamp>_<uuid>.sha256

param(
    [string]$RepoRoot
)

$ErrorActionPreference = "Stop"

# ── Elevation gate ───────────────────────────────────────────────────

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host "FAIL  Administrator privileges required." -ForegroundColor Red
    exit 1
}

Write-Host "PASS  Administrator verified" -ForegroundColor Green

# ── Resolve repo root ────────────────────────────────────────────────

if (-not $RepoRoot) {
    $RepoRoot = (git -C $PSScriptRoot rev-parse --show-toplevel 2>$null)
    if (-not $RepoRoot) {
        $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
    }
}
$RepoRoot = (Resolve-Path $RepoRoot).Path

# ── Check signer config ─────────────────────────────────────────────

$signerId = $env:HC_EVIDENCE_SIGNER_ID
$signerKey = $env:HC_EVIDENCE_SIGNER_KEY
if (-not $signerId -or -not $signerKey) {
    Write-Host "FAIL  HC_EVIDENCE_SIGNER_ID and HC_EVIDENCE_SIGNER_KEY must be set." -ForegroundColor Red
    exit 1
}

# ── Invoke Python generator ──────────────────────────────────────────

$pyScript = @"
import sys, json, os
sys.path.insert(0, r'$RepoRoot')
from backend.health_vault.companion_host.evidence_generator import (
    collect_host_facts, build_evidence_bundle, next_attestation_sequence,
    default_evidence_dir,
)
from backend.health_vault.companion_host.privileged_evidence import (
    append_evidence_record_append_only, _decode_key_material,
)
from pathlib import Path

repo = Path(r'$RepoRoot')
signer_id = os.environ['HC_EVIDENCE_SIGNER_ID']
signer_key_raw = os.environ['HC_EVIDENCE_SIGNER_KEY']
signer_key = _decode_key_material(signer_key_raw)

audit_dir = default_evidence_dir()

facts = collect_host_facts(repo_root=repo)
seq = next_attestation_sequence(audit_dir, signer_id)
bundle = build_evidence_bundle(
    facts, signer_id=signer_id, signer_key=signer_key,
    attestation_sequence=seq,
)

json_path, sha_path = append_evidence_record_append_only(
    evidence_bundle=bundle, audit_dir=audit_dir,
)

result = {
    'json_path': str(json_path),
    'sha_path': str(sha_path),
    'attestation_uuid': bundle['attestation_uuid'],
    'attestation_sequence': bundle['attestation_sequence'],
    'evidence_sha256': bundle['evidence_sha256'],
    'checks': {
        'elevation_verified': bundle['elevation_verified'],
        'worktree_clean': bundle['worktree_clean'],
        'bitlocker_protection': bundle['bitlocker_status']['protection_status'],
        'filesystem': bundle['filesystem'],
        'companion_service_present': bundle['companion_service_present'],
        'caddy_running': bundle['caddy_running'],
        'companion_process_running': bundle['companion_process_running'],
        'ports': bundle['required_ports'],
    },
}
print(json.dumps(result))
"@

$pyResult = python -c $pyScript 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "FAIL  Evidence generation failed:" -ForegroundColor Red
    Write-Host $pyResult
    exit 1
}

$data = $pyResult | ConvertFrom-Json

# ── Report results ───────────────────────────────────────────────────

$checks = $data.checks
if ($checks.worktree_clean) {
    Write-Host "PASS  Repository clean" -ForegroundColor Green
} else {
    Write-Host "WARN  Repository has uncommitted changes" -ForegroundColor Yellow
}

$blStatus = $checks.bitlocker_protection
if ($blStatus -ieq "On") {
    Write-Host "PASS  BitLocker protected" -ForegroundColor Green
} else {
    Write-Host "WARN  BitLocker status: $blStatus" -ForegroundColor Yellow
}

$allPortsFree = $true
foreach ($p in $checks.ports.PSObject.Properties) {
    if ($p.Value -ne "FREE") { $allPortsFree = $false }
}
if ($allPortsFree) {
    Write-Host "PASS  Ports free" -ForegroundColor Green
} else {
    Write-Host "WARN  Some ports occupied" -ForegroundColor Yellow
}

$runtimeInactive = (-not $checks.companion_service_present) -and
                   (-not $checks.caddy_running) -and
                   (-not $checks.companion_process_running)
if ($runtimeInactive) {
    Write-Host "PASS  Runtime inactive" -ForegroundColor Green
} else {
    Write-Host "WARN  Runtime components detected" -ForegroundColor Yellow
}

Write-Host "PASS  Evidence signed" -ForegroundColor Green
Write-Host "PASS  Evidence stored" -ForegroundColor Green
Write-Host ""
Write-Host "Evidence bundle successfully generated." -ForegroundColor Cyan
Write-Host "  JSON:     $($data.json_path)"
Write-Host "  SHA256:   $($data.sha_path)"
Write-Host "  UUID:     $($data.attestation_uuid)"
Write-Host "  Sequence: $($data.attestation_sequence)"

exit 0
