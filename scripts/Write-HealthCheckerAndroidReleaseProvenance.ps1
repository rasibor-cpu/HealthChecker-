<#
.SYNOPSIS
  Write non-secret HealthChecker Android release provenance.

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

function Test-EnvEnabled([string]$Name) {
    $v = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($v)) { return $false }
    return @("1", "true", "yes", "on") -contains $v.Trim().ToLowerInvariant()
}

function Normalize-CertFingerprint([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) { return $null }
    $normalized = [regex]::Replace($Value, "[^0-9A-Fa-f]", "").ToLowerInvariant()
    if ($normalized -notmatch "^[0-9a-f]{64}$") { return $null }
    return $normalized
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
        require_production_signing = (Test-EnvEnabled "HC_ANDROID_REQUIRE_PRODUCTION_SIGNING")
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

function Invoke-SafeAabVerify([string]$ArtifactPath) {
    $jarsigner = Get-Command jarsigner -ErrorAction SilentlyContinue
    if (-not $jarsigner) {
        return [pscustomobject]@{ status = "TOOL_UNAVAILABLE"; fingerprint = $null; tool = "jarsigner" }
    }
    $out = & $jarsigner.Source -verify -verbose -certs $ArtifactPath 2>&1 | Out-String
    $exitCode = $LASTEXITCODE
    $status = "UNSIGNED_OR_UNVERIFIED"
    if ($exitCode -eq 0 -and $out -match "jar verified") { $status = "SIGNED_VERIFIED" }
    elseif ($out -match "(?i)jar is unsigned|is unsigned") { $status = "UNSIGNED" }
    elseif ($exitCode -ne 0) { $status = "VERIFY_FAILED" }
    $fp = $null
    if ($out -match "SHA256:([0-9A-F:]+)") {
        $fp = $Matches[1]
    } elseif ($out -match "SHA-256:\s*([0-9A-Fa-f:]+)") {
        $fp = $Matches[1]
    }
    return [pscustomobject]@{ status = $status; fingerprint = $fp; tool = "jarsigner" }
}

function Invoke-SafeApkVerify([string]$ArtifactPath) {
    $apksigner = Get-Command apksigner -ErrorAction SilentlyContinue
    if (-not $apksigner) {
        return [pscustomobject]@{ status = "TOOL_UNAVAILABLE"; fingerprint = $null; tool = "apksigner" }
    }
    $out = & $apksigner.Source verify --verbose --print-certs $ArtifactPath 2>&1 | Out-String
    $exitCode = $LASTEXITCODE
    $status = if ($exitCode -eq 0) { "SIGNED_VERIFIED" } else { "VERIFY_FAILED" }
    if ($out -match "(?i)does not verify|not signed|no signers") {
        $status = "UNSIGNED_OR_UNVERIFIED"
    }
    $fp = $null
    if ($out -match "(?im)certificate SHA-256 digest:\s*([0-9A-Fa-f:]+)") {
        $fp = $Matches[1]
    }
    return [pscustomobject]@{ status = $status; fingerprint = $fp; tool = "apksigner" }
}

$aabVerification = [pscustomobject]@{ status = "NOT_PRODUCED"; fingerprint = $null; tool = $null }
$apkVerification = [pscustomobject]@{ status = "NOT_PRODUCED"; fingerprint = $null; tool = $null }
if (Test-Path -LiteralPath $aabPath -PathType Leaf) {
    $aabVerification = Invoke-SafeAabVerify $aabPath
}
if (Test-Path -LiteralPath $apkPath -PathType Leaf) {
    # APK installation evidence requires Android signing-scheme verification.
    # jarsigner-only AAB verification must never stand in for APK verification.
    $apkVerification = Invoke-SafeApkVerify $apkPath
}

$expectedCertRaw = [Environment]::GetEnvironmentVariable("HC_ANDROID_EXPECTED_CERT_SHA256")
$expectedCertConfigured = -not [string]::IsNullOrWhiteSpace($expectedCertRaw)
$expectedCertFingerprint = Normalize-CertFingerprint $expectedCertRaw
if ($expectedCertConfigured -and -not $expectedCertFingerprint) {
    throw "android_expected_signer_fingerprint_invalid"
}
$apkCertFingerprint = Normalize-CertFingerprint $apkVerification.fingerprint
$signerContinuity = "NOT_CONFIGURED"
if ($expectedCertConfigured) {
    if (-not $apkSha) {
        $signerContinuity = "APK_NOT_PRODUCED"
    } elseif ($apkVerification.status -ne "SIGNED_VERIFIED") {
        $signerContinuity = "APK_NOT_VERIFIED"
    } elseif (-not $apkCertFingerprint) {
        $signerContinuity = "FINGERPRINT_UNAVAILABLE"
    } elseif ($apkCertFingerprint -eq $expectedCertFingerprint) {
        $signerContinuity = "MATCH"
    } else {
        $signerContinuity = "MISMATCH"
    }
}
if ($signingAvail.require_production_signing) {
    if (-not $expectedCertConfigured) {
        throw "android_expected_signer_fingerprint_required"
    }
    if ($signerContinuity -ne "MATCH") {
        throw "android_signer_continuity_failed:$signerContinuity"
    }
}

$signingVerification = if ($apkSha) { $apkVerification.status } elseif ($aabSha) { $aabVerification.status } else { "NOT_RUN" }
$certFingerprint = if ($apkSha) { $apkVerification.fingerprint } elseif ($aabSha) { $aabVerification.fingerprint } else { $null }
$verifyTool = if ($apkSha) { $apkVerification.tool } elseif ($aabSha) { $aabVerification.tool } else { $null }

if (
    $DeviceUpgradeProof -eq "PASS" -and
    (-not $apkSha -or $apkVerification.status -ne "SIGNED_VERIFIED" -or $signerContinuity -ne "MATCH")
) {
    throw "device_upgrade_proof_invalid: PASS requires an apksigner-verified APK with approved signer continuity in this provenance run"
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
    if ($signingVerification -eq "SIGNED_VERIFIED" -and $signerContinuity -eq "MATCH") {
        "AVAILABLE_VERIFIED_SIGNER_MATCH"
    } elseif ($signingVerification -eq "SIGNED_VERIFIED") {
        "ENV_PRESENT_SIGNER_CONTINUITY_UNPROVEN"
    } else {
        "ENV_PRESENT_VERIFY_INCOMPLETE"
    }
} else {
    "BLOCKED_EXTERNAL_KEY_CUSTODY"
}

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $env:ProgramData "HealthChecker\releases\android-provenance"
}
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null

$stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$baseName = "HC_android_release_provenance_${versionName}_${stamp}"
$jsonPath = Join-Path $OutputDirectory "$baseName.json"
$txtPath = Join-Path $OutputDirectory "$baseName.txt"

$doc = [ordered]@{
    format                         = "hc.android.release.provenance.v1"
    task                           = "HEALTHCHECKER_ANDROID_SIGNED_RELEASE"
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
        signing_verification_status = $aabVerification.status
        certificate_sha256_fingerprint = $aabVerification.fingerprint
        verify_tool                = $aabVerification.tool
    }
    apk = [ordered]@{
        produced                   = [bool]$apkSha
        name                       = if ($apkSha) { "app-release.apk" } else { $null }
        path_relative              = if ($apkSha) { "android/app/build/outputs/apk/release/app-release.apk" } else { $null }
        sha256                     = $apkSha
        signing_verification_status = $apkVerification.status
        certificate_sha256_fingerprint = $apkVerification.fingerprint
        verify_tool                = $apkVerification.tool
    }
    signing_verification_status    = $signingVerification
    certificate_sha256_fingerprint = $certFingerprint
    signer_continuity_status       = $signerContinuity
    expected_cert_sha256_configured = [bool]$expectedCertConfigured
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
    "HealthChecker Android release provenance (non-secret)"
    "generated_utc=$utc"
    "hc_release_version=$hcReleaseVersion"
    "android_version_code=$versionCode"
    "android_version_name=$versionName"
    "git_sha=$gitSha"
    "aab_sha256=$aabSha"
    "apk_sha256=$apkSha"
    "signing_verification_status=$signingVerification"
    "certificate_sha256_fingerprint=$certFingerprint"
    "signer_continuity_status=$signerContinuity"
    "expected_cert_sha256_configured=$expectedCertConfigured"
    "gradle_version=$gradleVersion"
    "agp_version=$agpHint"
    "production_signing_status=$productionSigningStatus"
    "device_upgrade_proof=$DeviceUpgradeProof"
    "secrets_recorded=false"
    "notes=$Notes"
) | Set-Content -LiteralPath $txtPath -Encoding utf8

Write-Output $jsonPath
Write-Output $txtPath
