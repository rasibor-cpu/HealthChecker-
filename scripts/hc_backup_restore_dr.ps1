<#
.SYNOPSIS
  Governed HealthChecker encrypted backup / verify / isolated restore / interrupted recovery.

.NOTES
  Never point RestoreIsolated at the live production vault for drills.
  Recovery and vault keys are read from files; they are not logged.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Create", "Verify", "RestoreIsolated", "RecoverInterrupted")]
    [string]$Action,

    [string]$VaultRoot,
    [string]$BackupPath,
    [string]$TargetRoot,
    [string]$RecoveryKeyFile,
    [string]$VaultKeyFile
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

function Read-KeyFile([string]$Path) {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) {
        throw "key_file_missing"
    }
    $bytes = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $Path))
    if ($bytes.Length -lt 32) {
        throw "key_insufficient_length"
    }
    return $bytes
}

function Set-TempKeyEnv([byte[]]$RecoveryKey, [byte[]]$VaultKey = $null) {
    $env:HC_TMP_RECOVERY_KEY_HEX = ([System.BitConverter]::ToString($RecoveryKey) -replace "-", "").ToLowerInvariant()
    if ($null -ne $VaultKey) {
        $env:HC_TMP_VAULT_KEY_HEX = ([System.BitConverter]::ToString($VaultKey) -replace "-", "").ToLowerInvariant()
    }
}

function Clear-TempKeyEnv {
    Remove-Item Env:HC_TMP_RECOVERY_KEY_HEX -ErrorAction SilentlyContinue
    Remove-Item Env:HC_TMP_VAULT_KEY_HEX -ErrorAction SilentlyContinue
}

try {
    switch ($Action) {
        "Create" {
            if (-not $VaultRoot -or -not $BackupPath -or -not $RecoveryKeyFile -or -not $VaultKeyFile) {
                throw "Create requires -VaultRoot -BackupPath -RecoveryKeyFile -VaultKeyFile"
            }
            Set-TempKeyEnv (Read-KeyFile $RecoveryKeyFile) (Read-KeyFile $VaultKeyFile)
            $code = @"
from pathlib import Path
import os, json
from backend.health_vault.recovery import create_encrypted_backup, verify_encrypted_backup
from backend.health_vault.vault_store import VaultStore
rk = bytes.fromhex(os.environ['HC_TMP_RECOVERY_KEY_HEX'])
vk = bytes.fromhex(os.environ['HC_TMP_VAULT_KEY_HEX'])
vault = VaultStore(root=Path(r'$($VaultRoot.Replace("'", "''"))'), encryption_key=vk)
backup = create_encrypted_backup(vault, Path(r'$($BackupPath.Replace("'", "''"))'), rk)
print(json.dumps(verify_encrypted_backup(backup, rk), sort_keys=True))
"@
            python -c $code
            if ($LASTEXITCODE -ne 0) { throw "backup_create_failed" }
        }
        "Verify" {
            if (-not $BackupPath -or -not $RecoveryKeyFile) {
                throw "Verify requires -BackupPath -RecoveryKeyFile"
            }
            Set-TempKeyEnv (Read-KeyFile $RecoveryKeyFile)
            $code = @"
from pathlib import Path
import os, json
from backend.health_vault.recovery import verify_encrypted_backup
rk = bytes.fromhex(os.environ['HC_TMP_RECOVERY_KEY_HEX'])
print(json.dumps(verify_encrypted_backup(Path(r'$($BackupPath.Replace("'", "''"))'), rk), sort_keys=True))
"@
            python -c $code
            if ($LASTEXITCODE -ne 0) { throw "backup_verify_failed" }
        }
        "RestoreIsolated" {
            if (-not $BackupPath -or -not $TargetRoot -or -not $RecoveryKeyFile -or -not $VaultKeyFile) {
                throw "RestoreIsolated requires -BackupPath -TargetRoot -RecoveryKeyFile -VaultKeyFile"
            }
            Set-TempKeyEnv (Read-KeyFile $RecoveryKeyFile) (Read-KeyFile $VaultKeyFile)
            $code = @"
from pathlib import Path
import os, json
from backend.health_vault.recovery import (
    verify_encrypted_backup,
    restore_encrypted_backup,
    vault_content_fingerprint,
)
rk = bytes.fromhex(os.environ['HC_TMP_RECOVERY_KEY_HEX'])
vk = bytes.fromhex(os.environ['HC_TMP_VAULT_KEY_HEX'])
backup = Path(r'$($BackupPath.Replace("'", "''"))')
target = Path(r'$($TargetRoot.Replace("'", "''"))')
verification = verify_encrypted_backup(backup, rk)
restored = restore_encrypted_backup(backup, target, rk, vk, require_empty_target=True)
fp = vault_content_fingerprint(restored)
print(json.dumps({
    'ok': True,
    'verification': verification,
    'restored_fingerprint': fp,
    'match': verification['content_fingerprint'] == fp,
}, sort_keys=True))
"@
            python -c $code
            if ($LASTEXITCODE -ne 0) { throw "isolated_restore_failed" }
        }
        "RecoverInterrupted" {
            if (-not $TargetRoot) { throw "RecoverInterrupted requires -TargetRoot" }
            $code = @"
from pathlib import Path
import json
from backend.health_vault.recovery import recover_interrupted_restore
path = recover_interrupted_restore(Path(r'$($TargetRoot.Replace("'", "''"))'))
print(json.dumps({'ok': True, 'recovered': str(path)}))
"@
            python -c $code
            if ($LASTEXITCODE -ne 0) { throw "interrupted_recovery_failed" }
        }
    }
}
finally {
    Clear-TempKeyEnv
}
