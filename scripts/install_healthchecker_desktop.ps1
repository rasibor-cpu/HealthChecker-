[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$PackageDirectory,
    [string]$InstallRoot = "C:\Program Files\HealthChecker",
    [string]$DataRoot = "C:\ProgramData\HealthChecker",
    [string]$ShortcutDirectory = "",
    [string]$ManagedPythonPath = "",
    [string]$RuntimeTaskName = "HealthCheckerConsumerRuntime",
    [switch]$PreserveUserData = $true,
    [switch]$SkipShortcut
)

$ErrorActionPreference = "Stop"

$assertRuntime = Join-Path $PSScriptRoot "Assert-HealthCheckerManagedRuntime.ps1"
if (-not (Test-Path -LiteralPath $assertRuntime -PathType Leaf)) {
    throw "managed_runtime_assert_missing"
}
$assertArgs = @{}
if ($ManagedPythonPath) { $assertArgs["ManagedPythonPath"] = $ManagedPythonPath }
$null = & $assertRuntime @assertArgs

$manifest = Join-Path $PackageDirectory "release-manifest.json"
if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) { throw "release_manifest_missing" }
$release = Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json
if ($release.format -ne "hc.release.manifest.v1") { throw "release_manifest_invalid" }
foreach ($entry in $release.files) {
    $path = Join-Path $PackageDirectory ([string]$entry.path)
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "release_file_missing" }
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.sha256) {
        throw "release_integrity_failed"
    }
}

# Required production launcher markers must exist in the verified package before activation.
$requiredMarkers = @(
    "scripts\start_healthchecker.ps1",
    "scripts\start_healthchecker_production.ps1",
    "scripts\Resolve-HealthCheckerInstallRoot.ps1",
    "scripts\Assert-HealthCheckerManagedRuntime.ps1",
    "backend\health_vault\api.py",
    "config\healthchecker.release.json"
)
foreach ($marker in $requiredMarkers) {
    if (-not (Test-Path -LiteralPath (Join-Path $PackageDirectory $marker) -PathType Leaf)) {
        throw "release_required_marker_missing:$marker"
    }
}

New-Item -ItemType Directory -Path "$DataRoot\data", "$DataRoot\config", "$DataRoot\secrets", "$DataRoot\logs" -Force | Out-Null

$parent = Split-Path -Parent $InstallRoot
if ($parent -and -not (Test-Path -LiteralPath $parent)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
}

$next = "$InstallRoot.next"
if (Test-Path -LiteralPath $next) { Remove-Item -LiteralPath $next -Recurse -Force }
Copy-Item -LiteralPath $PackageDirectory -Destination $next -Recurse
$previous = "$InstallRoot.previous"
if (Test-Path -LiteralPath $previous) { Remove-Item -LiteralPath $previous -Recurse -Force }
if (Test-Path -LiteralPath $InstallRoot) { Move-Item -LiteralPath $InstallRoot -Destination $previous }
try {
    Move-Item -LiteralPath $next -Destination $InstallRoot
} catch {
    if (Test-Path -LiteralPath $previous) {
        Move-Item -LiteralPath $previous -Destination $InstallRoot
    }
    throw
}

if (-not $SkipShortcut) {
    if (-not $ShortcutDirectory) {
        $ShortcutDirectory = [Environment]::GetFolderPath("CommonDesktopDirectory")
    }
    if (-not (Test-Path -LiteralPath $ShortcutDirectory -PathType Container)) {
        New-Item -ItemType Directory -Path $ShortcutDirectory -Force | Out-Null
    }
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut((Join-Path $ShortcutDirectory "HealthChecker.lnk"))
    $shortcut.TargetPath = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    $launcher = Join-Path $InstallRoot "scripts\start_healthchecker.ps1"
    $shortcut.Arguments = "-NoProfile -ExecutionPolicy RemoteSigned -WindowStyle Hidden -File `"$launcher`""
    $shortcut.WorkingDirectory = $InstallRoot
    $shortcut.Save()
}

# PreserveUserData is the default contract: ProgramData health data is never
# removed by install. Destructive data removal requires a separate recovery-
# verified, explicit operation outside this installer.
if (-not $PreserveUserData) {
    Write-Warning "PreserveUserData switch is authoritative; installer never deletes ProgramData vault/config/secrets."
}
Write-Output "HealthChecker installed at $InstallRoot. User data preserved at $DataRoot. Runtime task name: $RuntimeTaskName"
