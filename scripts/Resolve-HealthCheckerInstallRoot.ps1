# Resolves HealthChecker application root for Program Files deploy and source-tree layouts.
# Does not require git or a source checkout. Fail-closed when install markers are missing.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$ScriptsDirectory = $PSScriptRoot,
    [Parameter(Mandatory = $false)]
    [string]$OverrideRoot = $env:HEALTHCHECKER_INSTALL_ROOT
)

$ErrorActionPreference = "Stop"

function Test-HealthCheckerInstallRoot([string]$Candidate) {
    if (-not $Candidate) { return $false }
    if (-not (Test-Path -LiteralPath $Candidate -PathType Container)) { return $false }
    $apiMarker = Join-Path $Candidate "backend\health_vault\api.py"
    $moduleMarker = Join-Path $Candidate "backend\health_vault\__init__.py"
    $scriptsMarker = Join-Path $Candidate "scripts\start_healthchecker_production.ps1"
    return (
        (Test-Path -LiteralPath $apiMarker -PathType Leaf) -and
        (Test-Path -LiteralPath $moduleMarker -PathType Leaf) -and
        (Test-Path -LiteralPath $scriptsMarker -PathType Leaf)
    )
}

$candidates = New-Object System.Collections.Generic.List[string]
if ($OverrideRoot) {
    [void]$candidates.Add([IO.Path]::GetFullPath($OverrideRoot))
}
# Script-parent is the install root for both Program Files and source-tree layouts.
[void]$candidates.Add([IO.Path]::GetFullPath((Join-Path $ScriptsDirectory "..")))

foreach ($candidate in $candidates) {
    if (Test-HealthCheckerInstallRoot $candidate) {
        Write-Output $candidate
        return
    }
}

throw "HealthChecker install root unresolved: install_root_markers_missing"
