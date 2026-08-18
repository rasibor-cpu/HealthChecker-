[CmdletBinding()]
param(
    [string]$Origin = "https://health.capitalstratasystems.com",
    [string]$UserId = "00000",
    [string]$AdbPath = "C:\ProgramData\HealthChecker\tools\android\platform-tools\37.0.1\adb.exe"
)

$ErrorActionPreference = "Stop"
[Console]::Title = "HealthChecker Secure S24 Pairing"
$Host.UI.RawUI.WindowTitle = "HealthChecker Secure S24 Pairing"

if ($Origin -ne "https://health.capitalstratasystems.com") { throw "approved_production_origin_required" }
if (-not (Test-Path -LiteralPath $AdbPath -PathType Leaf)) { throw "managed_adb_missing" }

Clear-Host
Write-Host "HealthChecker Production S24 Pairing" -ForegroundColor Cyan
Write-Host "The password is accepted only by this local masked prompt and remains in process memory."
$secure = Read-Host "Enter the current HealthChecker password for user $UserId" -AsSecureString
$pointer = [IntPtr]::Zero
$password = $null
$login = $null
$pair = $null
try {
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    $loginBody = @{ user_id = $UserId; password = $password } | ConvertTo-Json -Compress
    $login = Invoke-RestMethod -Method Post -Uri "$Origin/api/auth/login" -ContentType "application/json" -Body $loginBody
    $loginBody = $null
    $password = $null
    if (-not $login.token) { throw "authentication_response_invalid" }
    if ($login.must_change_password -or $login.scope -ne "full") { throw "password_state_requires_user_action" }

    $headers = @{ Authorization = "Bearer $($login.token)" }
    $pair = Invoke-RestMethod -Method Post -Uri "$Origin/api/companion/pair/start" -Headers $headers -ContentType "application/json" -Body "{}"
    $headers = $null
    $login = $null
    if (-not $pair.ok -or -not $pair.pair_code) { throw "pairing_response_invalid" }

    Write-Host ""
    Write-Host "Keep the S24 on the native HealthChecker SETTINGS pairing screen." -ForegroundColor Green
    Write-Host "Server: $Origin"
    Write-Host "One-time pairing code: $($pair.pair_code)" -ForegroundColor Yellow
    Write-Host "Enter the server and code on the phone, then tap CONFIRM PAIRING."
    Read-Host "Press Enter only after the phone reports pairing success"
} catch {
    Write-Host "Secure pairing stopped: $($_.Exception.Message)" -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
} finally {
    $password = $null
    $secure = $null
    $login = $null
    $pair = $null
    if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}
