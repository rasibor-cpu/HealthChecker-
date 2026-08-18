[CmdletBinding()]
param(
    [string]$ConfigPath = "C:\ProgramData\HealthChecker\config\production.json"
)

$ErrorActionPreference = "Stop"
$python = "C:\ProgramData\HealthChecker\tools\python\3.12.10\python.exe"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Stop-WithCode([string]$Code) {
    throw "HealthChecker production startup failed: $Code"
}

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { Stop-WithCode "runtime_missing" }
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) { Stop-WithCode "config_missing" }

try { $config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json }
catch { Stop-WithCode "config_invalid" }

if ($config.service_id -ne "healthchecker.consumer.api") { Stop-WithCode "service_identity_invalid" }
$bindAddress = [string]$config.bind_address
$publicOrigin = [string]$config.public_origin
$port = [int]$config.port
$stateDir = [string]$config.runtime_state_dir
$logDir = [string]$config.log_dir
$restartLimit = [Math]::Max(0, [Math]::Min(20, [int]$config.restart_limit))
$backoff = [Math]::Max(1, [Math]::Min(60, [int]$config.restart_backoff_seconds))

if ($config.transport -ne "cloudflare_tunnel") { Stop-WithCode "transport_invalid" }
if ($config.tunnel_service_id -ne "healthchecker.cloudflare.tunnel") { Stop-WithCode "tunnel_service_identity_invalid" }
if ($bindAddress -ne "127.0.0.1") { Stop-WithCode "loopback_bind_required" }
if ($port -eq 8765) { Stop-WithCode "css_port_collision_forbidden" }
if ($port -lt 1 -or $port -gt 65535) { Stop-WithCode "port_invalid" }
try { $origin = [Uri]$publicOrigin } catch { Stop-WithCode "public_origin_invalid" }
if ($origin.Scheme -ne "https" -or $origin.Host -ne "health.capitalstratasystems.com" -or
    $origin.Port -ne 443 -or $origin.UserInfo -or $origin.PathAndQuery -ne "/") {
    Stop-WithCode "approved_https_origin_required"
}

New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$pidPath = Join-Path $stateDir "healthchecker-consumer-api.pid"
$heartbeatPath = Join-Path $stateDir "healthchecker-consumer-api.heartbeat.json"
$logPath = Join-Path $logDir "healthchecker-runtime.log"

if (Test-Path -LiteralPath $pidPath) {
    $oldPid = [int](Get-Content -LiteralPath $pidPath -Raw)
    if (Get-Process -Id $oldPid -ErrorAction SilentlyContinue) { Stop-WithCode "instance_already_running" }
    Remove-Item -LiteralPath $pidPath -Force
}
if (Get-NetTCPConnection -State Listen -LocalAddress $bindAddress -LocalPort $port -ErrorAction SilentlyContinue) {
    Stop-WithCode "port_already_occupied"
}

$PID | Set-Content -LiteralPath $pidPath -Encoding ascii -NoNewline
$attempt = 0
try {
    while ($true) {
        $attempt++
        @{ service = "healthchecker.consumer.api"; state = "starting"; attempt = $attempt; at_utc = [DateTime]::UtcNow.ToString("o") } |
            ConvertTo-Json -Compress | Set-Content -LiteralPath $heartbeatPath -Encoding utf8
        Add-Content -LiteralPath $logPath -Value "event=runtime_start attempt=$attempt"
        Set-Location -LiteralPath $repositoryRoot
        & $python -m uvicorn backend.health_vault.api:create_health_vault_app `
            --factory --host $bindAddress --port $port `
            --no-access-log
        $exitCode = $LASTEXITCODE
        Add-Content -LiteralPath $logPath -Value "event=runtime_exit code=$exitCode attempt=$attempt"
        if ($exitCode -eq 0) { break }
        if ($attempt -gt $restartLimit) { Stop-WithCode "restart_limit_exceeded" }
        Start-Sleep -Seconds ([Math]::Min(60, $backoff * $attempt))
    }
} finally {
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
    @{ service = "healthchecker.consumer.api"; state = "stopped"; at_utc = [DateTime]::UtcNow.ToString("o") } |
        ConvertTo-Json -Compress | Set-Content -LiteralPath $heartbeatPath -Encoding utf8
}
