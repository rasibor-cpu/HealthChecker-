<#
.SYNOPSIS
  Write non-secret Android release provenance for HC321-B3.

.DESCRIPTION
  Records HC release version, Android versionCode/versionName, Git SHA, build
  timestamp, AAB/APK names and SHA-256 when present, signing verification status,
  certificate fingerprint when safely obtainable, Gradle/tool info, whether
  production signing env appears available, and device-upgrade proof status.

  Never records passwords, private keys, or keystore bytes.
#>
[CmdletBinding()]
param(
    [string]$AndroidProjectRoot = "",
    [string]$OutputDirectory = "",
    [ValidateSet("NOT_RUN", "PASS", "FAIL", "BLOCKED_EXTERNAL_KEY_CUSTODY", "SKIPPED_NO_DEVICE")]
    [string]$DeviceUpgradeProof = "NOT_RUN",
    [string]$Notes = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-Sha256Hex([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Test-EnvPresent([string]$Name) {
    $v = [Environment]::GetEnvironmentVariable($Name)
    return -not [string]::IsNullOrWhiteSpace($v)
}

function Get-RedactedSigningAvailability {
    $names = @(
        "HC_ANDROID_KEYSTORE_FILE",
        "HC_ANDROID_KEYSTORE_PASSWORD",
        "HC_ANDROID_KEY_ALIAS",
        "HC_ANDROID_KEY_PASSWORD"
    )
    $present = @()
    foreach ($n in $names) {
        if (Test-EnvPresent $n) { $present += $n }
    }
    return [pscustomobject]@{
        required_env_names     = $names
        present_env_names      = $present
        present_count          = $present.Count
        production_signing_ready = ($present.Count -eq 4)
        require_production_signing = (Test-EnvPresent "HC_ANDROID_REQUIRE_PRODUCTION_SIGNING")
        # Never echo password values or keystore path contents into provenance beyond presence.
        keystore_file_env_set  = (Test-EnvPresent "HC_ANDROID_KEYSTORE_FILE")
    }
}

if ([string]::IsNullOrWhiteSpace($AndroidProjectRoot)) {
    $AndroidProjectRoot = Join-Path $PSScriptRoot "..\android" | Resolve-Path
}
$AndroidProjectRoot = (Resolve-Path -LiteralPath $AndroidProjectRoot).Path
$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $AndroidProjectRoot "..")).Path

$gradleFile = Join-Path $AndroidProjectRoot "app\build.gradle.kts"
$gradleText = Get-Content -LiteralPath $gradleFile -Raw -Encoding UTF8
if ($gradleText -notmatch 'versionCode\s*=\s*(\d+)') {
    throw "versionCode not found in build.gradle.kts"
}
$versionCode = [int]$Matches[1]
if ($gradleText -notmatch 'versionName\s*=\s*"([^"]+)"') {
    throw "versionName not found in build.gradle.kts"
}
$versionName = $Matches[1]

$releaseJsonPath = Join-Path $RepoRoot "config\healthchecker.release.json"
$hcReleaseVersion = $null
if (Test-Path -LiteralPath $releaseJsonPath) {
    $hcReleaseVersion = (Get-Content -LiteralPath $releaseJsonPath -Raw -Encoding UTF8 | ConvertFrom-Json).version
}

$gitSha = ""
try {
    $gitSha = (& git -C $RepoRoot rev-parse HEAD 2>$null | Select-Object -First 1)
    if (-not $gitSha) { $gitSha = "UNKNOWN" }
} catch {
    $gitSha = "UNKNOWN"
}

$aabPath = Join-Path $AndroidProjectRoot "app\build\outputs\bundle\release\app-release.aab"
$apkPath = Join-Path $AndroidProjectRoot "app\build\outputs\apk\release\app-release.apk"
$aabSha = Get-Sha256Hex $aabPath
$apkSha = Get-Sha256Hex $apkPath

$signingAvail = Get-RedactedSigningAvailability
$signingVerification = "NOT_RUN"
$certFingerprint = $null
$verifyTool = $null

function Invoke-SafeJarVerify([string]$ArtifactPath) {
    $jarsigner = Get-Command jarsigner -ErrorAction SilentlyContinue
    if (-not $jarsigner) {
        return [pscustomobject]@{ status = "TOOL_UNAVAILABLE"; fingerprint = $null; tool = "jarsigner" }
    }
    $out = & jarsigner -verify -verbose -certs $ArtifactPath 2>&1 | Out-String
    # Redact unlikely secret-looking lines; provenance stores status only + fingerprint extract.
    $status = "UNSIGNED_OR_UNVERIFIED"
    if ($out -match "jar verified") { $status = "SIGNED_VERIFIED" }
    elseif ($out -match "jar is unsigned") { $status = "UNSIGNED" }
    elseif ($out -match "is unsigned") { $status = "UNSIGNED" }
    $fp = $null
    if ($out -match "SHA256:([0-9A-F:]+)") {
        $fp = $Matches[1]
    } elseif ($out -match "SHA-256:\s*([0-9A-Fa-f:]+)") {
        $fp = $Matches[1]
    }
    # Ensure we never persist password-like substrings from tool noise.
    if ($out -match "(?i)password|private\s*key|keystore\s*password") {
        # Tool output discarded; only status/fingerprint returned.
    }
    return [pscustomobject]@{ status = $status; fingerprint = $fp; tool = "jarsigner" }
}

$artifactForVerify = $null
if (Test-Path -LiteralPath $aabPath -PathType Leaf) { $artifactForVerify = $aabPath }
elseif (Test-Path -LiteralPath $apkPath -PathType Leaf) { $artifactForVerify = $apkPath }

if ($artifactForVerify) {
    $vr = Invoke-SafeJarVerify $artifactForVerify
    $signingVerification = $vr.status
    $certFingerprint = $vr.fingerprint
    $verifyTool = $vr.tool
}

$gradleVersion = $null
$agpHint = $null
try {
    Push-Location $AndroidProjectRoot
    $gv = & .\gradlew.bat -q --version 2>&1 | Out-String
    if ($gv -match "Gradle\s+([0-9.]+)") { $gradleVersion = $Matches[1] }
} catch {
    $gradleVersion = $null
} finally {
    Pop-Location
}
$rootGradle = Get-Content -LiteralPath (Join-Path $AndroidProjectRoot "build.gradle.kts") -Raw -Encoding UTF8
if ($rootGradle -match 'com\.android\.application"\s+version\s+"([^"]+)"') {
    $agpHint = $Matches[1]
}

$utc = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
$productionSigningStatus = if ($signingAvail.production_signing_ready) {
    if ($signingVerification -eq "SIGNED_VERIFIED") { "AVAILABLE_AND_VERIFIED" } else { "ENV_PRESENT_VERIFY_INCOMPLETE" }
} else {
    "BLOCKED_EXTERNAL_KEY_CUSTODY"
}

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $env:ProgramData "HealthChecker\releases\android-provenance"
}
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null

$stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$baseName = "HC321_B3_android_release_provenance_${versionName}_${stamp}"
$jsonPath = Join-Path $OutputDirectory "$baseName.json"
$txtPath = Join-Path $OutputDirectory "$baseName.txt"

$doc = [ordered]@{
    format                         = "hc.android.release.provenance.v1"
    task                           = "HC321-B3_ANDROID_SIGNED_RELEASE"
    generated_utc                  = $utc
    hc_release_version             = $hcReleaseVersion
    android_version_code           = $versionCode
    android_version_name           = $versionName
    git_sha                        = $gitSha
    aab = [ordered]@{
        produced                   = [bool]$aabSha
        name                       = if ($aabSha) { "app-release.aab" } else { $null }
        path_relative              = if ($aabSha) { "android/app/build/outputs/bundle/release/app-release.aab" } else { $null }
        sha256                     = $aabSha
    }
    apk = [ordered]@{
        produced                   = [bool]$apkSha
        name                       = if ($apkSha) { "app-release.apk" } else { $null }
        path_relative              = if ($apkSha) { "android/app/build/outputs/apk/release/app-release.apk" } else { $null }
        sha256                     = $apkSha
    }
    signing_verification_status    = $signingVerification
    certificate_sha256_fingerprint = $certFingerprint
    verify_tool                    = $verifyTool
    gradle_version                 = $gradleVersion
    android_gradle_plugin_version  = $agpHint
    production_signing_available   = [bool]$signingAvail.production_signing_ready
    production_signing_status      = $productionSigningStatus
    signing_env_present_count      = $signingAvail.present_count
    signing_env_names_present      = @($signingAvail.present_env_names)
    require_production_signing_env = [bool]$signingAvail.require_production_signing
    device_upgrade_proof           = $DeviceUpgradeProof
    notes                          = $Notes
    secrets_recorded               = $false
}

($doc | ConvertTo-Json -Depth 6) | Set-Content -LiteralPath $jsonPath -Encoding utf8

@(
    "HC321-B3 Android release provenance (non-secret)"
    "generated_utc=$utc"
    "hc_release_version=$hcReleaseVersion"
    "android_version_code=$versionCode"
    "android_version_name=$versionName"
    "git_sha=$gitSha"
    "aab_sha256=$aabSha"
    "apk_sha256=$apkSha"
    "signing_verification_status=$signingVerification"
    "certificate_sha256_fingerprint=$certFingerprint"
    "gradle_version=$gradleVersion"
    "agp_version=$agpHint"
    "production_signing_status=$productionSigningStatus"
    "device_upgrade_proof=$DeviceUpgradeProof"
    "secrets_recorded=false"
    "notes=$Notes"
) | Set-Content -LiteralPath $txtPath -Encoding utf8

Write-Output $jsonPath
Write-Output $txtPath
