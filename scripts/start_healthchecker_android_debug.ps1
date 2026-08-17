[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8766
)

$ErrorActionPreference = "Stop"
$adb = "C:\ProgramData\HealthChecker\tools\android\platform-tools\37.0.1\adb.exe"
$launcher = Join-Path $PSScriptRoot "start_healthchecker.ps1"

if (-not (Test-Path -LiteralPath $adb -PathType Leaf)) {
    throw "Managed Android platform tools were not found at: $adb"
}
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    throw "HealthChecker launcher was not found at: $launcher"
}

$deviceLines = & $adb devices | Select-Object -Skip 1 | Where-Object { $_ -match "\sdevice$" }
if (@($deviceLines).Count -ne 1) {
    throw "Exactly one authorized Android device is required for the safe debug connection."
}

& $adb reverse "tcp:$Port" "tcp:$Port"
if ($LASTEXITCODE -ne 0) {
    throw "Could not create the device-to-host loopback tunnel for port $Port."
}

Write-Host "Android debug origin: http://127.0.0.1:$Port"
Write-Host "Traffic is carried through the authorized ADB reverse tunnel; no LAN listener is opened."
try {
    & $launcher -Port $Port
} finally {
    & $adb reverse --remove "tcp:$Port" | Out-Null
}
