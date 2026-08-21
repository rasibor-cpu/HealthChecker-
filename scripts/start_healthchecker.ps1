[CmdletBinding()]
param(
    [string]$ConfigPath = "C:\ProgramData\HealthChecker\config\production.json"
)

$ErrorActionPreference = "Stop"

# Consumer desktop entry point. Always starts the production HealthChecker path
# (127.0.0.1:8766 via production config). No silent demo or legacy-port fallback.
# Never binds CSS port 8765. Requires install-root markers and managed runtime.

$assertRuntime = Join-Path $PSScriptRoot "Assert-HealthCheckerManagedRuntime.ps1"
if (-not (Test-Path -LiteralPath $assertRuntime -PathType Leaf)) {
    throw "HealthChecker startup failed: managed_runtime_assert_missing"
}
$null = & $assertRuntime

$resolver = Join-Path $PSScriptRoot "Resolve-HealthCheckerInstallRoot.ps1"
if (-not (Test-Path -LiteralPath $resolver -PathType Leaf)) {
    throw "HealthChecker startup failed: install_root_resolver_missing"
}
$installRoot = & $resolver -ScriptsDirectory $PSScriptRoot
if (-not $installRoot) {
    throw "HealthChecker startup failed: install_root_unresolved"
}

$production = Join-Path $PSScriptRoot "start_healthchecker_production.ps1"
if (-not (Test-Path -LiteralPath $production -PathType Leaf)) {
    throw "HealthChecker startup failed: production_launcher_missing"
}
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw "HealthChecker startup failed: config_missing ($ConfigPath). See docs/ops/HC321_B2_DESKTOP_INSTALL_UNINSTALL.md"
}

Set-Location -LiteralPath $installRoot
& $production -ConfigPath $ConfigPath
if ($LASTEXITCODE -ne 0) {
    throw "HealthChecker stopped with exit code $LASTEXITCODE."
}
