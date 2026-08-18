[CmdletBinding()]
param([string]$ConfigPath = "C:\ProgramData\HealthChecker\config\production.json")
$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) { throw "production_config_missing" }
$config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
if ($config.tunnel_service_id -ne "healthchecker.cloudflare.tunnel") { throw "tunnel_service_identity_invalid" }
if ($config.public_origin -ne "https://health.capitalstratasystems.com") { throw "tunnel_hostname_invalid" }
$cloudflared = [string]$config.cloudflared_executable
$tunnelConfig = [string]$config.tunnel_config_path
if (-not (Test-Path -LiteralPath $cloudflared -PathType Leaf)) { throw "cloudflared_missing" }
if (-not (Test-Path -LiteralPath $tunnelConfig -PathType Leaf)) { throw "tunnel_config_missing" }
if ($tunnelConfig -notlike "C:\ProgramData\HealthChecker\config\*") { throw "tunnel_config_path_invalid" }
$stateDir = [string]$config.runtime_state_dir
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
$pidPath = Join-Path $stateDir "healthchecker-cloudflare-tunnel.pid"
if (Test-Path -LiteralPath $pidPath) {
    $oldPid = [int](Get-Content -LiteralPath $pidPath -Raw)
    if (Get-Process -Id $oldPid -ErrorAction SilentlyContinue) { throw "tunnel_already_running" }
    Remove-Item -LiteralPath $pidPath -Force
}
$PID | Set-Content -LiteralPath $pidPath -Encoding ascii -NoNewline
try {
    & $cloudflared tunnel --config $tunnelConfig run
    if ($LASTEXITCODE -ne 0) { throw "tunnel_runtime_failed" }
} finally {
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
}
