[CmdletBinding()]
param(
    [string]$ConfigPath = "C:\ProgramData\HealthChecker\config\production.json"
)

$ErrorActionPreference = "Stop"

function Stop-WithCode([string]$Code) {
    throw "HealthChecker production startup failed: $Code"
}

function Write-HcHeartbeat {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$State,
        [int]$Attempt = 0,
        [int]$ChildPid = 0,
        [string]$Reason = "",
        [int]$ConsecutiveFailures = 0
    )
    $payload = [ordered]@{
        service = "healthchecker.consumer.api"
        state   = $State
        at_utc  = [DateTime]::UtcNow.ToString("o")
    }
    if ($Attempt -gt 0) { $payload.attempt = $Attempt }
    if ($ChildPid -gt 0) { $payload.child_pid = $ChildPid }
    if ($Reason) { $payload.reason = $Reason }
    if ($ConsecutiveFailures -gt 0) { $payload.consecutive_failures = $ConsecutiveFailures }
    $json = $payload | ConvertTo-Json -Compress
    [System.IO.File]::WriteAllText($Path, $json)
}

function Test-HcProcessAlive([int]$ProcessId) {
    if ($ProcessId -le 0) { return $false }
    return [bool](Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Test-HcLoopbackService {
    param(
        [Parameter(Mandatory = $true)][string]$BindAddress,
        [Parameter(Mandatory = $true)][int]$Port,
        [string]$Path = "/healthz",
        [int]$TimeoutMs = 2000
    )
    $client = $null
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $async = $client.BeginConnect($BindAddress, $Port, $null, $null)
        $opened = $async.AsyncWaitHandle.WaitOne(500, $false)
        if (-not $opened) { return $false }
        $client.EndConnect($async)
    } catch {
        return $false
    } finally {
        if ($client) { $client.Close() }
    }
    if (-not $Path) { $Path = "/healthz" }
    if (-not $Path.StartsWith("/")) { $Path = "/" + $Path }
    try {
        $request = [System.Net.HttpWebRequest]::Create("http://${BindAddress}:${Port}${Path}")
        $request.Method = "GET"
        $request.Timeout = $TimeoutMs
        $request.ReadWriteTimeout = $TimeoutMs
        $request.Proxy = New-Object System.Net.WebProxy
        $response = $request.GetResponse()
        try {
            $code = [int]$response.StatusCode
            return ($code -eq 200)
        } finally {
            $response.Close()
        }
    } catch {
        return $false
    }
}

function Stop-HcOwnedChild {
    param(
        [System.Diagnostics.Process]$Child,
        [string]$BindAddress,
        [int]$Port
    )
    if ($Child) {
        $rootId = 0
        try { $rootId = [int]$Child.Id } catch { $rootId = 0 }
        if ($rootId -gt 0) {
            Get-CimInstance Win32_Process -Filter "ParentProcessId=$rootId" -ErrorAction SilentlyContinue | ForEach-Object {
                Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            }
            if (-not $Child.HasExited) {
                Stop-Process -Id $rootId -Force -ErrorAction SilentlyContinue
            }
            try { $null = $Child.WaitForExit(15000) } catch { }
        }
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while ([DateTime]::UtcNow -lt $deadline) {
        $stillListening = Get-NetTCPConnection -State Listen -LocalAddress $BindAddress -LocalPort $Port -ErrorAction SilentlyContinue
        if (-not $stillListening) { return }
        $ownerIds = @($stillListening | Select-Object -ExpandProperty OwningProcess -Unique)
        foreach ($ownerId in $ownerIds) {
            if ($Child -and ($ownerId -eq $Child.Id)) {
                Stop-Process -Id $ownerId -Force -ErrorAction SilentlyContinue
            }
        }
        Start-Sleep -Milliseconds 200
    }
}

function Start-HcUvicornChild {
    param(
        [Parameter(Mandatory = $true)][string]$Python,
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][string]$BindAddress,
        [Parameter(Mandatory = $true)][int]$Port
    )
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Python
    $psi.Arguments = "-m uvicorn backend.health_vault.api:create_health_vault_app --factory --host $BindAddress --port $Port --no-access-log"
    $psi.WorkingDirectory = $InstallRoot
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    if (-not $proc.Start()) { return $null }
    return $proc
}

$assertRuntime = Join-Path $PSScriptRoot "Assert-HealthCheckerManagedRuntime.ps1"
if (-not (Test-Path -LiteralPath $assertRuntime -PathType Leaf)) { Stop-WithCode "managed_runtime_assert_missing" }
try {
    $python = & $assertRuntime
} catch {
    Stop-WithCode "runtime_missing"
}
if (-not $python) { Stop-WithCode "runtime_missing" }

$resolver = Join-Path $PSScriptRoot "Resolve-HealthCheckerInstallRoot.ps1"
if (-not (Test-Path -LiteralPath $resolver -PathType Leaf)) { Stop-WithCode "install_root_resolver_missing" }
$installRoot = & $resolver -ScriptsDirectory $PSScriptRoot
if (-not $installRoot) { Stop-WithCode "install_root_unresolved" }

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
$child = $null
$exitState = "stopped"
$healthPollSeconds = 1
$probeFailureThreshold = 3
$readyTimeoutSeconds = 30
try {
    Set-Location -LiteralPath $installRoot
    while ($true) {
        if ($child -and -not $child.HasExited) {
            Add-Content -LiteralPath $logPath -Value "event=runtime_duplicate_child_prevented"
            Stop-HcOwnedChild -Child $child -BindAddress $bindAddress -Port $port
            $child = $null
        }
        $attempt++
        Write-HcHeartbeat -Path $heartbeatPath -State "starting" -Attempt $attempt
        Add-Content -LiteralPath $logPath -Value "event=runtime_start attempt=$attempt"
        $child = Start-HcUvicornChild -Python $python -InstallRoot $installRoot -BindAddress $bindAddress -Port $port
        if (-not $child) {
            Add-Content -LiteralPath $logPath -Value "event=runtime_exit code=start_failed attempt=$attempt"
            if ($attempt -gt $restartLimit) {
                $exitState = "failed"
                Write-HcHeartbeat -Path $heartbeatPath -State "failed" -Attempt $attempt -Reason "restart_limit_exceeded"
                Stop-WithCode "restart_limit_exceeded"
            }
            Start-Sleep -Seconds ([Math]::Min(60, $backoff * $attempt))
            continue
        }
        $childPid = [int]$child.Id
        $readyDeadline = [DateTime]::UtcNow.AddSeconds($readyTimeoutSeconds)
        $becameHealthy = $false
        while ([DateTime]::UtcNow -lt $readyDeadline) {
            if (-not (Test-HcProcessAlive -ProcessId $childPid)) { break }
            if (Test-HcLoopbackService -BindAddress $bindAddress -Port $port) {
                $becameHealthy = $true
                break
            }
            Start-Sleep -Milliseconds 200
        }
        $probeExhausted = $false
        if ($becameHealthy) {
            Write-HcHeartbeat -Path $heartbeatPath -State "running" -Attempt $attempt -ChildPid $childPid
            Add-Content -LiteralPath $logPath -Value "event=runtime_healthy attempt=$attempt"
            $consecutiveProbeFailures = 0
            while (Test-HcProcessAlive -ProcessId $childPid) {
                if (Test-HcLoopbackService -BindAddress $bindAddress -Port $port) {
                    if ($consecutiveProbeFailures -gt 0) {
                        Add-Content -LiteralPath $logPath -Value "event=runtime_probe_recovered attempt=$attempt"
                    }
                    $consecutiveProbeFailures = 0
                    Write-HcHeartbeat -Path $heartbeatPath -State "running" -Attempt $attempt -ChildPid $childPid
                } else {
                    $consecutiveProbeFailures++
                    Add-Content -LiteralPath $logPath -Value "event=runtime_degraded consecutive=$consecutiveProbeFailures attempt=$attempt"
                    if ($consecutiveProbeFailures -ge $probeFailureThreshold) {
                        $probeExhausted = $true
                        break
                    }
                    Write-HcHeartbeat -Path $heartbeatPath -State "degraded" -Attempt $attempt -ChildPid $childPid -Reason "probe_failure" -ConsecutiveFailures $consecutiveProbeFailures
                }
                Start-Sleep -Seconds $healthPollSeconds
            }
        }
        $childAlive = Test-HcProcessAlive -ProcessId $childPid
        $serving = $false
        if (-not $probeExhausted) {
            $serving = Test-HcLoopbackService -BindAddress $bindAddress -Port $port
        }
        $reason = "child_exit"
        $exitCode = "unknown"
        if ($childAlive -and $probeExhausted) { $reason = "service_unavailable" }
        elseif ($childAlive -and -not $serving) { $reason = "service_unavailable" }
        elseif (-not $becameHealthy) { $reason = "ready_timeout" }
        try {
            $child.Refresh()
            if ($child.HasExited) { $exitCode = [string]$child.ExitCode }
        } catch { }
        Add-Content -LiteralPath $logPath -Value "event=runtime_unhealthy reason=$reason attempt=$attempt"
        Add-Content -LiteralPath $logPath -Value "event=runtime_exit code=$exitCode attempt=$attempt"
        Write-HcHeartbeat -Path $heartbeatPath -State "restarting" -Attempt $attempt -ChildPid $childPid -Reason $reason
        Stop-HcOwnedChild -Child $child -BindAddress $bindAddress -Port $port
        $child = $null
        if ($attempt -gt $restartLimit) {
            $exitState = "failed"
            Write-HcHeartbeat -Path $heartbeatPath -State "failed" -Attempt $attempt -Reason "restart_limit_exceeded"
            Stop-WithCode "restart_limit_exceeded"
        }
        $delay = [Math]::Min(60, $backoff * $attempt)
        Add-Content -LiteralPath $logPath -Value "event=runtime_backoff seconds=$delay attempt=$attempt"
        Start-Sleep -Seconds $delay
    }
} finally {
    Stop-HcOwnedChild -Child $child -BindAddress $bindAddress -Port $port
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
    if ($exitState -eq "failed") {
        Write-HcHeartbeat -Path $heartbeatPath -State "failed" -Reason "restart_limit_exceeded"
    } else {
        Write-HcHeartbeat -Path $heartbeatPath -State "stopped"
    }
}
