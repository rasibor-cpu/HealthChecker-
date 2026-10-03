[CmdletBinding()]
param(
    [string]$ConfigPath = "C:\ProgramData\HealthChecker\config\production.json"
)

$ErrorActionPreference = "Stop"

function Stop-WithCode([string]$Code) {
    throw "HealthChecker production startup failed: $Code"
}

# The scheduled task runs this supervisor as an interactive process (LogonType
# Interactive is required for DPAPI credential loading). Windows invokes console
# handlers on a native callback thread, so the handler must not call PowerShell
# scriptblocks or cmdlets. The child is also contained in a kill-on-close job so
# any unexpected supervisor exit cannot leave a serving orphan.
$hcConsoleCtrlHandlerSource = @"
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

public static class HcConsoleCtrlHandler {
    public delegate bool HandlerRoutine(int ctrlType);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool SetConsoleCtrlHandler(HandlerRoutine handler, bool add);

    public const int CTRL_C_EVENT = 0;
    public const int CTRL_BREAK_EVENT = 1;
    public const int CTRL_CLOSE_EVENT = 2;
    public const int CTRL_LOGOFF_EVENT = 5;
    public const int CTRL_SHUTDOWN_EVENT = 6;

    private static HandlerRoutine _handler;
    private static int _pendingSignal = -1;

    public static bool Register() {
        _handler = Handle;
        return SetConsoleCtrlHandler(_handler, true);
    }

    public static bool Handle(int ctrlType) {
        Interlocked.Exchange(ref _pendingSignal, ctrlType);
        return ctrlType == CTRL_LOGOFF_EVENT ||
            ctrlType == CTRL_CLOSE_EVENT ||
            ctrlType == CTRL_SHUTDOWN_EVENT;
    }

    public static int TakePendingSignal() {
        return Interlocked.Exchange(ref _pendingSignal, -1);
    }
}

public static class HcManagedChildJob {
    private const uint JobObjectExtendedLimitInformation = 9;
    private const uint JobObjectLimitKillOnJobClose = 0x2000;
    private const uint CreateSuspended = 0x00000004;
    private const uint CreateNoWindow = 0x08000000;
    private const uint StartfUseStdHandles = 0x00000100;
    private const uint GenericRead = 0x80000000;
    private const uint GenericWrite = 0x40000000;
    private const uint FileShareRead = 0x00000001;
    private const uint FileShareWrite = 0x00000002;
    private const uint OpenExisting = 3;
    private const uint FileAttributeNormal = 0x00000080;

    private static readonly object JobLock = new object();
    private static IntPtr _jobHandle = IntPtr.Zero;

    [StructLayout(LayoutKind.Sequential)]
    private struct SecurityAttributes {
        public int Length;
        public IntPtr SecurityDescriptor;
        [MarshalAs(UnmanagedType.Bool)]
        public bool InheritHandle;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct StartupInfo {
        public int Size;
        public string Reserved;
        public string Desktop;
        public string Title;
        public int X;
        public int Y;
        public int XSize;
        public int YSize;
        public int XCountChars;
        public int YCountChars;
        public int FillAttribute;
        public int Flags;
        public short ShowWindow;
        public short Reserved2Length;
        public IntPtr Reserved2;
        public IntPtr StandardInput;
        public IntPtr StandardOutput;
        public IntPtr StandardError;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ProcessInformation {
        public IntPtr Process;
        public IntPtr Thread;
        public uint ProcessId;
        public uint ThreadId;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct BasicLimitInformation {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IoCounters {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ExtendedLimitInformation {
        public BasicLimitInformation BasicLimit;
        public IoCounters Io;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateJobObject(IntPtr attributes, string name);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(
        IntPtr job, uint informationClass, ref ExtendedLimitInformation information, uint length);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateFile(
        string name, uint access, uint share, ref SecurityAttributes attributes,
        uint creationDisposition, uint flags, IntPtr templateFile);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool CreateProcess(
        string applicationName, StringBuilder commandLine, IntPtr processAttributes,
        IntPtr threadAttributes, bool inheritHandles, uint creationFlags,
        IntPtr environment, string currentDirectory, ref StartupInfo startupInfo,
        out ProcessInformation processInformation);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern uint ResumeThread(IntPtr thread);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool TerminateProcess(IntPtr process, uint exitCode);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr handle);

    public static void Initialize() {
        lock (JobLock) {
            if (_jobHandle != IntPtr.Zero) return;
            IntPtr job = CreateJobObject(IntPtr.Zero, null);
            if (job == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());

            ExtendedLimitInformation information = new ExtendedLimitInformation();
            information.BasicLimit.LimitFlags = JobObjectLimitKillOnJobClose;
            uint size = (uint)Marshal.SizeOf(typeof(ExtendedLimitInformation));
            if (!SetInformationJobObject(job, JobObjectExtendedLimitInformation, ref information, size)) {
                int error = Marshal.GetLastWin32Error();
                CloseHandle(job);
                throw new Win32Exception(error);
            }
            _jobHandle = job;
        }
    }

    public static int StartProcess(string executable, string arguments, string workingDirectory) {
        Initialize();
        SecurityAttributes security = new SecurityAttributes();
        security.Length = Marshal.SizeOf(typeof(SecurityAttributes));
        security.InheritHandle = true;
        IntPtr standardInput = CreateFile(
            "NUL", GenericRead, FileShareRead | FileShareWrite, ref security,
            OpenExisting, FileAttributeNormal, IntPtr.Zero);
        IntPtr standardOutput = CreateFile(
            "NUL", GenericWrite, FileShareRead | FileShareWrite, ref security,
            OpenExisting, FileAttributeNormal, IntPtr.Zero);
        IntPtr standardError = CreateFile(
            "NUL", GenericWrite, FileShareRead | FileShareWrite, ref security,
            OpenExisting, FileAttributeNormal, IntPtr.Zero);
        if (IsInvalidHandle(standardInput) || IsInvalidHandle(standardOutput) ||
            IsInvalidHandle(standardError)) {
            int error = Marshal.GetLastWin32Error();
            CloseIfValid(standardInput);
            CloseIfValid(standardOutput);
            CloseIfValid(standardError);
            throw new Win32Exception(error);
        }

        try {
            StartupInfo startup = new StartupInfo();
            startup.Size = Marshal.SizeOf(typeof(StartupInfo));
            startup.Flags = (int)StartfUseStdHandles;
            startup.StandardInput = standardInput;
            startup.StandardOutput = standardOutput;
            startup.StandardError = standardError;
            ProcessInformation process;
            string command = "\"" + executable + "\" " + arguments;
            StringBuilder mutableCommand = new StringBuilder(command, command.Length + 1);
            if (!CreateProcess(
                executable, mutableCommand, IntPtr.Zero, IntPtr.Zero, true,
                CreateSuspended | CreateNoWindow, IntPtr.Zero, workingDirectory,
                ref startup, out process)) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }

            bool resumed = false;
            try {
                if (!AssignProcessToJobObject(_jobHandle, process.Process)) {
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                }
                if (ResumeThread(process.Thread) == UInt32.MaxValue) {
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                }
                resumed = true;
                return checked((int)process.ProcessId);
            } finally {
                bool terminationFailed = false;
                int terminateError = 0;
                if (!resumed && !TerminateProcess(process.Process, 1)) {
                    terminationFailed = true;
                    terminateError = Marshal.GetLastWin32Error();
                }
                CloseHandle(process.Thread);
                CloseHandle(process.Process);
                if (terminationFailed) {
                    throw new Win32Exception(
                        terminateError,
                        "Failed to terminate the suspended HealthChecker child process.");
                }
            }
        } finally {
            CloseHandle(standardInput);
            CloseHandle(standardOutput);
            CloseHandle(standardError);
        }
    }

    private static bool IsInvalidHandle(IntPtr handle) {
        return handle == IntPtr.Zero || handle == new IntPtr(-1);
    }

    private static void CloseIfValid(IntPtr handle) {
        if (!IsInvalidHandle(handle)) CloseHandle(handle);
    }
}
"@
$hcDiagLogPath = "C:\ProgramData\HealthChecker\logs\healthchecker\healthchecker-runtime.log"
$hcNativeRuntimeTypesAvailable = $false
$hcCtrlHandlerAvailable = $false
try {
    Add-Type -TypeDefinition $hcConsoleCtrlHandlerSource -ErrorAction Stop
    $hcNativeRuntimeTypesAvailable = $true
    $hcHandlerResult = [HcConsoleCtrlHandler]::Register()
    $hcCtrlHandlerAvailable = [bool]$hcHandlerResult
    try { Add-Content -LiteralPath $hcDiagLogPath -Value "event=hc_diag_ctrl_handler_registered result=$hcHandlerResult pid=$PID" -ErrorAction SilentlyContinue } catch {}
} catch {
    try { Add-Content -LiteralPath $hcDiagLogPath -Value "event=hc_diag_ctrl_handler_registration_failed error=$($_.Exception.Message) pid=$PID" -ErrorAction SilentlyContinue } catch {}
}

function Write-HcPendingControlSignal {
    if (-not $hcCtrlHandlerAvailable) { return }
    $ctrlType = [HcConsoleCtrlHandler]::TakePendingSignal()
    if ($ctrlType -ge 0) {
        Add-Content -LiteralPath $hcDiagLogPath -Value "event=hc_diag_ctrl_signal type=$ctrlType pid=$PID"
    }
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

function Test-HcSupervisorIdentity {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][string]$ExpectedScriptPath,
        [Parameter(Mandatory = $true)][string]$ExpectedConfigPath
    )
    if ($ProcessId -le 0) { return $false }
    try {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop
        if (-not $process) { return $false }
        $name = ([string]$process.Name).ToLowerInvariant()
        if ($name -notin @("powershell.exe", "pwsh.exe")) { return $false }
        $commandLine = ([string]$process.CommandLine).ToLowerInvariant()
        $scriptPath = ([System.IO.Path]::GetFullPath($ExpectedScriptPath)).ToLowerInvariant()
        $configPath = ([System.IO.Path]::GetFullPath($ExpectedConfigPath)).ToLowerInvariant()
        return $commandLine.Contains($scriptPath) -and
            $commandLine.Contains("-configpath") -and
            $commandLine.Contains($configPath)
    } catch {
        return $false
    }
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

function Test-HcOrphanedUvicornChild {
    # HC-354: identifies a leftover uvicorn child left behind by a supervisor that
    # exited without running its finally cleanup (e.g. Stop-ScheduledTask uses
    # TerminateProcess and bypasses the script's finally block entirely). Only
    # returns true when the listening process's own identity (name + exact command
    # line signature) positively matches the uvicorn invocation this script uses,
    # so we never touch an unrelated process bound to the same port.
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][int]$Port
    )
    try {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop
        if (-not $process) { return $false }
        $name = ([string]$process.Name).ToLowerInvariant()
        if ($name -ne "python.exe") { return $false }
        $commandLine = ([string]$process.CommandLine).ToLowerInvariant()
        return $commandLine.Contains("uvicorn") -and
            $commandLine.Contains("backend.health_vault.api:create_health_vault_app") -and
            $commandLine.Contains("--port $Port".ToLowerInvariant())
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
    $arguments = "-m uvicorn backend.health_vault.api:create_health_vault_app --factory --host $BindAddress --port $Port --no-access-log"
    $childPid = [HcManagedChildJob]::StartProcess($Python, $arguments, $InstallRoot)
    return [System.Diagnostics.Process]::GetProcessById($childPid)
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

if (-not $hcNativeRuntimeTypesAvailable) {
    Stop-WithCode "native_runtime_support_unavailable"
}
[HcManagedChildJob]::Initialize()
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$pidPath = Join-Path $stateDir "healthchecker-consumer-api.pid"
$heartbeatPath = Join-Path $stateDir "healthchecker-consumer-api.heartbeat.json"
$logPath = Join-Path $logDir "healthchecker-runtime.log"

if (Test-Path -LiteralPath $pidPath) {
    $oldPid = 0
    $pidText = (Get-Content -LiteralPath $pidPath -Raw -ErrorAction SilentlyContinue).Trim()
    $validPid = [int]::TryParse($pidText, [Globalization.NumberStyles]::Integer, [Globalization.CultureInfo]::InvariantCulture, [ref]$oldPid)
    $scriptPath = Join-Path $PSScriptRoot "start_healthchecker_production.ps1"
    if ($validPid -and (Test-HcSupervisorIdentity -ProcessId $oldPid -ExpectedScriptPath $scriptPath -ExpectedConfigPath $ConfigPath)) {
        Stop-WithCode "instance_already_running"
    }
    $staleReason = if (-not $validPid) { "malformed" } elseif (Test-HcProcessAlive -ProcessId $oldPid) { "reused" } else { "dead" }
    Add-Content -LiteralPath $logPath -Value "event=runtime_stale_pid reason=$staleReason"
    Remove-Item -LiteralPath $pidPath -Force
}
$existingListener = Get-NetTCPConnection -State Listen -LocalAddress $bindAddress -LocalPort $port -ErrorAction SilentlyContinue
if ($existingListener) {
    # HC-354: if a prior supervisor exits without running its finally cleanup, its
    # uvicorn child can remain listening on this port. Reclaim ONLY when the owning
    # process's own identity positively matches our exact uvicorn invocation;
    # otherwise fail closed to avoid touching an unrelated process.
    $reclaimed = $false
    foreach ($ownerId in @($existingListener | Select-Object -ExpandProperty OwningProcess -Unique)) {
        if (Test-HcOrphanedUvicornChild -ProcessId $ownerId -Port $port) {
            Add-Content -LiteralPath $logPath -Value "event=runtime_orphan_child_reclaimed pid=$ownerId"
            Stop-Process -Id $ownerId -Force -ErrorAction SilentlyContinue
            $reclaimed = $true
        }
    }
    if ($reclaimed) {
        $deadline = [DateTime]::UtcNow.AddSeconds(10)
        while ([DateTime]::UtcNow -lt $deadline) {
            if (-not (Get-NetTCPConnection -State Listen -LocalAddress $bindAddress -LocalPort $port -ErrorAction SilentlyContinue)) { break }
            Start-Sleep -Milliseconds 200
        }
    }
    if (Get-NetTCPConnection -State Listen -LocalAddress $bindAddress -LocalPort $port -ErrorAction SilentlyContinue) {
        Stop-WithCode "port_already_occupied"
    }
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
        Write-HcPendingControlSignal
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
            Write-HcPendingControlSignal
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
                Write-HcPendingControlSignal
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
