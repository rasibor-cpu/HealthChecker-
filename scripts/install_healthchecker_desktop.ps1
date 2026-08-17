[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$PackageDirectory,
    [switch]$PreserveUserData = $true
)

$ErrorActionPreference = "Stop"
$installRoot = "C:\Program Files\HealthChecker"
$dataRoot = "C:\ProgramData\HealthChecker"
$managedPython = "C:\ProgramData\HealthChecker\tools\python\3.12.10\python.exe"
if (-not (Test-Path -LiteralPath $managedPython -PathType Leaf)) { throw "managed_runtime_missing" }
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

New-Item -ItemType Directory -Path "$dataRoot\data", "$dataRoot\config", "$dataRoot\secrets", "$dataRoot\logs" -Force | Out-Null
$next = "$installRoot.next"
if (Test-Path -LiteralPath $next) { Remove-Item -LiteralPath $next -Recurse -Force }
Copy-Item -LiteralPath $PackageDirectory -Destination $next -Recurse
$previous = "$installRoot.previous"
if (Test-Path -LiteralPath $previous) { Remove-Item -LiteralPath $previous -Recurse -Force }
if (Test-Path -LiteralPath $installRoot) { Move-Item -LiteralPath $installRoot -Destination $previous }
try { Move-Item -LiteralPath $next -Destination $installRoot }
catch {
    if (Test-Path -LiteralPath $previous) { Move-Item -LiteralPath $previous -Destination $installRoot }
    throw
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath("CommonDesktopDirectory")) "HealthChecker.lnk"))
$shortcut.TargetPath = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
$shortcut.Arguments = "-NoProfile -ExecutionPolicy RemoteSigned -WindowStyle Hidden -File `"$installRoot\scripts\start_healthchecker.ps1`""
$shortcut.WorkingDirectory = $installRoot
$shortcut.Save()

# Uninstallers must honor PreserveUserData: ProgramData health data is never
# removed implicitly. Destructive data removal requires a separate recovery-
# verified, explicit operation that is intentionally outside this installer.
Write-Output "HealthChecker installed. User data preserved at $dataRoot."
