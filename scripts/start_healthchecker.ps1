[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$python = "C:\ProgramData\HealthChecker\tools\python\3.12.10\python.exe"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "HealthChecker Python runtime was not found at: $python"
}

$listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
if ($listener) {
    throw "HealthChecker cannot start because port $Port is already in use. Choose another port with -Port."
}

Set-Location -LiteralPath $repositoryRoot
Write-Host "Starting HealthChecker at http://127.0.0.1:$Port"
& $python -m uvicorn backend.health_vault.api:create_health_vault_app `
    --factory `
    --host 127.0.0.1 `
    --port $Port

if ($LASTEXITCODE -ne 0) {
    throw "HealthChecker stopped with exit code $LASTEXITCODE."
}
