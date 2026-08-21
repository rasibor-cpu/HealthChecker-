<#
.SYNOPSIS
  Governed vault schema checkpoint + migrate (temp/copy vaults only for drills).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Validate", "CheckpointAndMigrate")]
    [string]$Action,

    [Parameter(Mandatory = $true)]
    [string]$VaultRoot,

    [Parameter(Mandatory = $true)]
    [string]$VaultKeyFile,

    [string]$RecoveryKeyFile,
    [string]$CheckpointPath,
    [string]$TargetSchema = "hc.health_vault.v1"
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Read-KeyFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { throw "key_file_missing" }
    $bytes = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $Path))
    if ($bytes.Length -lt 32) { throw "key_insufficient_length" }
    return $bytes
}

$vk = Read-KeyFile $VaultKeyFile
$env:HC_TMP_VAULT_KEY_HEX = ([System.BitConverter]::ToString($vk) -replace "-", "").ToLowerInvariant()
try {
    if ($Action -eq "Validate") {
        python -c @"
from pathlib import Path
import os, json
from backend.health_vault.recovery import VaultMigrationManager, CURRENT_SCHEMA
from backend.health_vault.vault_store import VaultStore
vk = bytes.fromhex(os.environ['HC_TMP_VAULT_KEY_HEX'])
vault = VaultStore(root=Path(r'$($VaultRoot.Replace("'", "''"))'), encryption_key=vk)
VaultMigrationManager().validate_current(vault)
print(json.dumps({'ok': True, 'schema_version': vault._read_index().get('schema_version'), 'current': CURRENT_SCHEMA}))
"@
        if ($LASTEXITCODE -ne 0) { throw "schema_validate_failed" }
        return
    }

    if (-not $RecoveryKeyFile -or -not $CheckpointPath) {
        throw "CheckpointAndMigrate requires -RecoveryKeyFile -CheckpointPath"
    }
    $rk = Read-KeyFile $RecoveryKeyFile
    $env:HC_TMP_RECOVERY_KEY_HEX = ([System.BitConverter]::ToString($rk) -replace "-", "").ToLowerInvariant()
    python -c @"
from pathlib import Path
import os, json
from backend.health_vault.recovery import VaultMigrationManager, verify_encrypted_backup
from backend.health_vault.vault_store import VaultStore
vk = bytes.fromhex(os.environ['HC_TMP_VAULT_KEY_HEX'])
rk = bytes.fromhex(os.environ['HC_TMP_RECOVERY_KEY_HEX'])
vault = VaultStore(root=Path(r'$($VaultRoot.Replace("'", "''"))'), encryption_key=vk)
backup = VaultMigrationManager().migrate_with_encrypted_checkpoint(
    vault,
    r'$($TargetSchema.Replace("'", "''"))',
    rk,
    Path(r'$($CheckpointPath.Replace("'", "''"))'),
)
print(json.dumps({
    'ok': True,
    'schema_version': vault._read_index().get('schema_version'),
    'checkpoint': str(backup),
    'verification': verify_encrypted_backup(backup, rk),
}, sort_keys=True))
"@
    if ($LASTEXITCODE -ne 0) { throw "schema_migrate_failed" }
}
finally {
    Remove-Item Env:HC_TMP_VAULT_KEY_HEX -ErrorAction SilentlyContinue
    Remove-Item Env:HC_TMP_RECOVERY_KEY_HEX -ErrorAction SilentlyContinue
}
