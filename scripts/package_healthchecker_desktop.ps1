[CmdletBinding()]
param([Parameter(Mandatory)][string]$OutputDirectory)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$output = [IO.Path]::GetFullPath($OutputDirectory)
if ($output.StartsWith($root + [IO.Path]::DirectorySeparatorChar) -or $output -eq $root) {
    throw "Release output must be outside the source tree."
}

$releaseMetaPath = Join-Path $root "config\healthchecker.release.json"
$releaseMeta = Get-Content -LiteralPath $releaseMetaPath -Raw | ConvertFrom-Json
$version = [string]$releaseMeta.version
if (-not $version) { throw "release_version_missing" }

$stage = Join-Path $output "HealthChecker-$version"
if (Test-Path -LiteralPath $stage) { throw "Release stage already exists: $stage" }
New-Item -ItemType Directory -Path $stage -Force | Out-Null

$requiredFiles = @(
    "index.html", "mobile.html", "style.css", "app.js",
    "service-worker.js",
    "config/healthchecker.release.json",
    "config/healthchecker.production.example.json",
    "scripts/start_healthchecker.ps1",
    "scripts/start_healthchecker_production.ps1",
    "scripts/start_healthchecker_cloudflare_tunnel.ps1",
    "scripts/configure_healthchecker_cloudflare_tunnel.ps1",
    "scripts/install_healthchecker_runtime_task.ps1",
    "scripts/Resolve-HealthCheckerInstallRoot.ps1",
    "scripts/Assert-HealthCheckerManagedRuntime.ps1",
    "scripts/install_healthchecker_desktop.ps1",
    "scripts/uninstall_healthchecker_desktop.ps1",
    "scripts/healthchecker_recovery.py",
    "docs/ops/HC321_B2_DESKTOP_RUNTIME_PREREQUISITE.md",
    "docs/ops/HC321_B2_DESKTOP_INSTALL_UNINSTALL.md"
)
$optionalFiles = @(
    "manifest.json", "requirements.txt", "requirements-production.lock"
)
$trees = @("backend", "js", "css", "assets", "icons")
$allowed = @(".py", ".json", ".html", ".css", ".js", ".png", ".ico", ".svg", ".txt", ".md", ".lock")
$excludeDirPattern = "[\\/](tests?|__pycache__|scratch|evidence|vault_storage|hc_intake|\.git|\.pytest_cache|node_modules)[\\/]"

foreach ($relative in $requiredFiles) {
    $source = Join-Path $root $relative
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "package_required_file_missing: $relative"
    }
    $dest = Join-Path $stage $relative
    New-Item -ItemType Directory -Path (Split-Path $dest) -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination $dest
}
foreach ($relative in $optionalFiles) {
    $source = Join-Path $root $relative
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { continue }
    $dest = Join-Path $stage $relative
    New-Item -ItemType Directory -Path (Split-Path $dest) -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination $dest
}
foreach ($tree in $trees) {
    $sourceRoot = Join-Path $root $tree
    if (-not (Test-Path -LiteralPath $sourceRoot -PathType Container)) { continue }
    Get-ChildItem -LiteralPath $sourceRoot -Recurse -File | Where-Object {
        $allowed -contains $_.Extension.ToLowerInvariant() -and
        $_.FullName -notmatch $excludeDirPattern
    } | ForEach-Object {
        $relative = $_.FullName.Substring($root.Length + 1)
        $dest = Join-Path $stage $relative
        New-Item -ItemType Directory -Path (Split-Path $dest) -Force | Out-Null
        Copy-Item -LiteralPath $_.FullName -Destination $dest
    }
}

$manifest = Get-ChildItem -LiteralPath $stage -Recurse -File | Sort-Object FullName | ForEach-Object {
    @{
        path = $_.FullName.Substring($stage.Length + 1).Replace("\", "/")
        sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}
$manifestJson = @{ format = "hc.release.manifest.v1"; version = $version; files = @($manifest) } |
    ConvertTo-Json -Depth 4
$manifestPath = Join-Path $stage "release-manifest.json"
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($manifestPath, $manifestJson, $utf8NoBom)
Write-Output $stage
