# HC-307 — Validate a previously generated HC-306B-R1 evidence bundle.
#
# Usage:
#   .\Validate-PrivilegedEvidence.ps1 -EvidencePath <path-to-evidence.json>
#     [-RepoRoot C:\rasib\source\healthchecker]
#
# Environment requirements:
#   HC_EVIDENCE_TRUSTED_SIGNERS_JSON  — or —
#   HC_EVIDENCE_SIGNER_ID + HC_EVIDENCE_SIGNER_KEY
#
# This script NEVER activates the companion host.

param(
    [Parameter(Mandatory=$true)]
    [string]$EvidencePath,

    [string]$RepoRoot
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $EvidencePath)) {
    Write-Host "FAIL  Evidence file not found: $EvidencePath" -ForegroundColor Red
    exit 1
}

if (-not $RepoRoot) {
    $RepoRoot = (git -C $PSScriptRoot rev-parse --show-toplevel 2>$null)
    if (-not $RepoRoot) {
        $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
    }
}
$RepoRoot = (Resolve-Path $RepoRoot).Path

$escapedEvidence = $EvidencePath.Replace("'", "''")
$escapedRepo = $RepoRoot.Replace("'", "''")

$pyScript = @"
import sys, json, os
sys.path.insert(0, r'$escapedRepo')
from pathlib import Path
from backend.health_vault.companion_host.evidence_generator import (
    collect_host_facts, default_evidence_dir,
)
from backend.health_vault.companion_host.privileged_evidence import (
    EvidenceContext, EvidenceValidationError, validate_privileged_evidence_bundle,
)

evidence_path = r'$escapedEvidence'
with open(evidence_path, 'r', encoding='utf-8') as f:
    bundle = json.load(f)

repo = Path(r'$escapedRepo')
facts = collect_host_facts(repo_root=repo)

ctx = EvidenceContext(
    hostname=facts['hostname'],
    machine_identifier=facts['machine_identifier'],
    windows_boot_time=facts['windows_boot_time'],
    repository_path=facts['repository_path'],
    branch=facts['branch'],
    head_commit=facts['head_commit'],
    origin_head=facts['origin_head'],
    tailscale_node_id=facts['tailscale_node_id'],
    tailscale_dns_name=facts['tailscale_dns_name'],
    tailscale_ipv4=facts['tailscale_ipv4'],
)

audit_dir = default_evidence_dir()

try:
    validate_privileged_evidence_bundle(
        evidence_bundle=bundle,
        evidence_context=ctx,
        audit_dir=audit_dir if audit_dir.exists() else None,
    )
    print(json.dumps({'result': 'PASS', 'detail': 'Evidence validated successfully.'}))
except EvidenceValidationError as exc:
    print(json.dumps({'result': 'FAIL', 'detail': exc.code}))
except Exception as exc:
    print(json.dumps({'result': 'FAIL', 'detail': str(exc)}))
"@

$pyResult = python -c $pyScript 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "FAIL  Validation execution failed:" -ForegroundColor Red
    Write-Host $pyResult
    exit 1
}

$data = $pyResult | ConvertFrom-Json

if ($data.result -eq "PASS") {
    Write-Host "PASS  $($data.detail)" -ForegroundColor Green
    exit 0
} else {
    Write-Host "FAIL  $($data.detail)" -ForegroundColor Red
    exit 1
}
