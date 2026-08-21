# Fail-closed check for the governed HealthChecker Python runtime.
# Does not download or install Python. Operators must place an approved runtime
# at the fixed ProgramData path (see docs/ops/HC321_B2_DESKTOP_RUNTIME_PREREQUISITE.md).
[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$ManagedPythonPath = $(
        if ($env:HEALTHCHECKER_MANAGED_PYTHON) { $env:HEALTHCHECKER_MANAGED_PYTHON }
        else { "C:\ProgramData\HealthChecker\tools\python\3.12.10\python.exe" }
    ),
    [Parameter(Mandatory = $false)]
    [string]$ExpectedVersionPrefix = "3.12"
)

$ErrorActionPreference = "Stop"

function Stop-ManagedRuntimeMissing([string]$Detail) {
    $doc = "docs/ops/HC321_B2_DESKTOP_RUNTIME_PREREQUISITE.md"
    throw (
        "managed_runtime_missing: HealthChecker requires the governed Python runtime at " +
        "'$ManagedPythonPath'. $Detail Supported bootstrap: place an organization-approved " +
        "CPython $ExpectedVersionPrefix.x win_amd64 runtime at that fixed path before install " +
        "or start. Internet downloaders and ungoverned Python installs are not supported. " +
        "See $doc."
    )
}

if (-not $ManagedPythonPath) {
    Stop-ManagedRuntimeMissing "Path is empty."
}
if (-not (Test-Path -LiteralPath $ManagedPythonPath -PathType Leaf)) {
    Stop-ManagedRuntimeMissing "File not found."
}

try {
    $probe = & $ManagedPythonPath --version 2>&1 | Out-String
} catch {
    Stop-ManagedRuntimeMissing "Executable could not be probed."
}
if ($probe -notmatch [regex]::Escape("Python $ExpectedVersionPrefix")) {
    Stop-ManagedRuntimeMissing "Unexpected runtime identity: $($probe.Trim())."
}

Write-Output $ManagedPythonPath
