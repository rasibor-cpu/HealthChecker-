# HC-355C — read-only governed runtime health verification.
#
# HTTP 200 alone is NOT sufficient evidence that the HealthChecker consumer
# runtime is healthy: an orphaned uvicorn child (left behind by a dead
# supervisor) can continue serving /healthz indefinitely. This script checks
# every element of the governed chain independently and only reports HEALTHY
# when ALL of the following hold:
#
#   1. (optional) scheduled task is in the expected state
#   2. supervisor PID (from the pid file) exists and is alive
#   3. heartbeat file exists, is parseable, and is fresh
#   4. heartbeat state is "running"
#   5. child PID (from the heartbeat) exists and is alive
#   6. child's ParentProcessId equals the live supervisor PID
#   7. the expected bind address/port is listened on by that exact child PID
#   8. the local /healthz probe succeeds
#
# This script performs NO process termination, NO restart, and NO mutation of
# any kind. It is strictly read-only observability.

[CmdletBinding()]
param(
    [string]$ConfigPath = "C:\ProgramData\HealthChecker\config\production.json",
    [string]$TaskName = "HealthCheckerConsumerRuntime",
    [int]$HeartbeatStalenessSeconds = 30,
    [switch]$AsJson
)

function Test-HealthCheckerRuntimeHealth {
    <#
        .SYNOPSIS
        Read-only verification of the governed HealthChecker consumer runtime.

        .DESCRIPTION
        Pure function (no side effects) so it can be unit tested against an
        isolated fake supervisor/child pair without touching the production
        scheduled task, port 8766, or any real process.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$RuntimeStateDir,
        [Parameter(Mandatory = $true)][string]$BindAddress,
        [Parameter(Mandatory = $true)][int]$Port,
        [int]$HeartbeatStalenessSeconds = 30,
        [string]$TaskName = $null
    )

    $reasons = [System.Collections.Generic.List[string]]::new()
    $pidPath = Join-Path $RuntimeStateDir "healthchecker-consumer-api.pid"
    $heartbeatPath = Join-Path $RuntimeStateDir "healthchecker-consumer-api.heartbeat.json"

    $taskState = $null
    if ($TaskName) {
        try {
            $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
            $taskState = [string]$task.State
            if ($taskState -ne "Running") {
                $reasons.Add("task_not_running:$taskState")
            }
        } catch {
            $taskState = "unknown"
            $reasons.Add("task_lookup_failed")
        }
    }

    $supervisorPid = 0
    if (Test-Path -LiteralPath $pidPath) {
        $pidText = (Get-Content -LiteralPath $pidPath -Raw -ErrorAction SilentlyContinue)
        if ($pidText) { $pidText = $pidText.Trim() }
        $parsed = 0
        if ($pidText -and [int]::TryParse($pidText, [ref]$parsed)) {
            $supervisorPid = $parsed
        } else {
            $reasons.Add("supervisor_pid_invalid")
        }
    } else {
        $reasons.Add("pid_file_missing")
    }

    $supervisorAlive = $false
    if ($supervisorPid -gt 0) {
        $supervisorAlive = [bool](Get-Process -Id $supervisorPid -ErrorAction SilentlyContinue)
        if (-not $supervisorAlive) { $reasons.Add("supervisor_not_alive") }
    }

    $heartbeat = $null
    if (Test-Path -LiteralPath $heartbeatPath) {
        try {
            $raw = Get-Content -LiteralPath $heartbeatPath -Raw -ErrorAction Stop
            if ($raw) { $heartbeat = $raw | ConvertFrom-Json -ErrorAction Stop }
        } catch {
            $reasons.Add("heartbeat_unreadable")
        }
        if (-not $heartbeat) { $reasons.Add("heartbeat_unreadable") }
    } else {
        $reasons.Add("heartbeat_missing")
    }

    $heartbeatState = $null
    $heartbeatFresh = $false
    $childPid = 0
    if ($heartbeat) {
        $heartbeatState = [string]$heartbeat.state
        try {
            $hbTime = [DateTimeOffset]::Parse([string]$heartbeat.at_utc)
            $ageSeconds = ([DateTimeOffset]::UtcNow - $hbTime).TotalSeconds
            $heartbeatFresh = ($ageSeconds -ge -2) -and ($ageSeconds -le $HeartbeatStalenessSeconds)
            if (-not $heartbeatFresh) {
                $reasons.Add("heartbeat_stale:$([math]::Round($ageSeconds, 1))s")
            }
        } catch {
            $reasons.Add("heartbeat_timestamp_invalid")
        }
        if ($heartbeatState -ne "running") {
            $reasons.Add("heartbeat_state_not_running:$heartbeatState")
        }
        if ($heartbeat.PSObject.Properties.Name -contains "child_pid" -and [int]$heartbeat.child_pid -gt 0) {
            $childPid = [int]$heartbeat.child_pid
        }
    }
    if ($childPid -le 0) { $reasons.Add("heartbeat_child_pid_missing") }

    $childAlive = $false
    if ($childPid -gt 0) {
        $childAlive = [bool](Get-Process -Id $childPid -ErrorAction SilentlyContinue)
        if (-not $childAlive) { $reasons.Add("child_not_alive") }
    }

    $childParentMatches = $false
    if ($childPid -gt 0) {
        if ($childAlive -and $supervisorAlive -and $supervisorPid -gt 0) {
            try {
                $childProc = Get-CimInstance Win32_Process -Filter "ProcessId=$childPid" -ErrorAction Stop
                if ($childProc -and [int]$childProc.ParentProcessId -eq $supervisorPid) {
                    $childParentMatches = $true
                } else {
                    $reasons.Add("child_parent_mismatch")
                }
            } catch {
                $reasons.Add("child_parent_lookup_failed")
            }
        } else {
            $reasons.Add("child_parent_mismatch")
        }
    }

    $listenerOwnedByChild = $false
    try {
        $listeners = Get-NetTCPConnection -State Listen -LocalAddress $BindAddress -LocalPort $Port -ErrorAction SilentlyContinue
        if (-not $listeners) {
            $reasons.Add("listener_absent")
        } else {
            $ownerIds = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
            if ($childPid -gt 0 -and ($ownerIds -contains $childPid)) {
                $listenerOwnedByChild = $true
            } else {
                $reasons.Add("listener_owner_mismatch")
            }
        }
    } catch {
        $reasons.Add("listener_check_failed")
    }

    $localHealthz = $false
    try {
        $request = [System.Net.HttpWebRequest]::Create("http://${BindAddress}:${Port}/healthz")
        $request.Method = "GET"
        $request.Timeout = 2000
        $request.ReadWriteTimeout = 2000
        $request.Proxy = New-Object System.Net.WebProxy
        $response = $request.GetResponse()
        try {
            $localHealthz = ([int]$response.StatusCode -eq 200)
        } finally {
            $response.Close()
        }
    } catch {
        $localHealthz = $false
    }
    if (-not $localHealthz) { $reasons.Add("local_healthz_failed") }

    $healthy = ($reasons.Count -eq 0)

    [PSCustomObject]@{
        Healthy              = $healthy
        Reasons              = @($reasons)
        TaskState            = $taskState
        SupervisorPid        = $supervisorPid
        SupervisorAlive      = $supervisorAlive
        ChildPid             = $childPid
        ChildAlive           = $childAlive
        ChildParentMatches   = $childParentMatches
        HeartbeatState       = $heartbeatState
        HeartbeatFresh       = $heartbeatFresh
        ListenerOwnedByChild = $listenerOwnedByChild
        LocalHealthz         = $localHealthz
    }
}

# Only run as a CLI health check when invoked directly (not when dot-sourced
# by tests to reuse the Test-HealthCheckerRuntimeHealth function in isolation).
if ($MyInvocation.InvocationName -ne ".") {
    if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
        Write-Error "config_missing: $ConfigPath"
        exit 2
    }
    try {
        $config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    } catch {
        Write-Error "config_invalid"
        exit 2
    }
    $result = Test-HealthCheckerRuntimeHealth `
        -RuntimeStateDir $config.runtime_state_dir `
        -BindAddress $config.bind_address `
        -Port ([int]$config.port) `
        -HeartbeatStalenessSeconds $HeartbeatStalenessSeconds `
        -TaskName $TaskName

    if ($AsJson) {
        $result | ConvertTo-Json -Depth 5
    } else {
        $result | Format-List
    }
    if ($result.Healthy) { exit 0 } else { exit 1 }
}
