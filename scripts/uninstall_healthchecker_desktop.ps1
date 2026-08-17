[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$installRoot = "C:\Program Files\HealthChecker"
$dataRoot = "C:\ProgramData\HealthChecker"
$shortcut = Join-Path ([Environment]::GetFolderPath("CommonDesktopDirectory")) "HealthChecker.lnk"

if (Test-Path -LiteralPath $shortcut -PathType Leaf) {
    Remove-Item -LiteralPath $shortcut -Force
}
if (Test-Path -LiteralPath $installRoot -PathType Container) {
    $resolved = (Resolve-Path -LiteralPath $installRoot).Path
    if ($resolved -ne $installRoot) { throw "uninstall_path_identity_failed" }
    Remove-Item -LiteralPath $installRoot -Recurse -Force
}

# Deliberately preserve the authoritative vault, account state, configuration,
# protected keys, logs and backups. Data deletion is a separate authenticated,
# recovery-verified product operation and is never implied by uninstall.
Write-Output "HealthChecker application removed. User data preserved at $dataRoot."
