[CmdletBinding()]
param([Parameter(Mandatory)][string]$OutputDirectory)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$output = [IO.Path]::GetFullPath($OutputDirectory)
if ($output.StartsWith($root + [IO.Path]::DirectorySeparatorChar)) {
    throw "Release output must be outside the source tree."
}
$stage = Join-Path $output "HealthChecker-0.320.0"
if (Test-Path -LiteralPath $stage) { throw "Release stage already exists: $stage" }
New-Item -ItemType Directory -Path $stage -Force | Out-Null

$files = @(
    "index.html", "mobile.html", "style.css", "app.js", "manifest.json",
    "service-worker.js", "requirements.txt", "requirements-production.lock",
    "config/healthchecker.release.json", "scripts/start_healthchecker.ps1",
    "scripts/install_healthchecker_desktop.ps1", "scripts/uninstall_healthchecker_desktop.ps1",
    "scripts/healthchecker_recovery.py"
)
$trees = @("backend", "js", "css", "assets", "icons")
$allowed = @(".py", ".json", ".html", ".css", ".js", ".png", ".ico", ".svg", ".txt")

foreach ($relative in $files) {
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
        $_.FullName -notmatch "[\\/](tests?|__pycache__|scratch|evidence|vault_storage|hc_intake)[\\/]"
    } | ForEach-Object {
        $relative = $_.FullName.Substring($root.Length + 1)
        $dest = Join-Path $stage $relative
        New-Item -ItemType Directory -Path (Split-Path $dest) -Force | Out-Null
        Copy-Item -LiteralPath $_.FullName -Destination $dest
    }
}

$manifest = Get-ChildItem -LiteralPath $stage -Recurse -File | Sort-Object FullName | ForEach-Object {
    @{ path = $_.FullName.Substring($stage.Length + 1).Replace("\", "/"); sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
}
@{ format = "hc.release.manifest.v1"; version = "0.320.0"; files = @($manifest) } |
    ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $stage "release-manifest.json") -Encoding utf8
Write-Output $stage
