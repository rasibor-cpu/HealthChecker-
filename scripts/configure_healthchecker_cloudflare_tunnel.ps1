[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-fA-F-]{36}$')][string]$TunnelId,
    [Parameter(Mandatory)][string]$CredentialsFile,
    [string]$OutputPath = "C:\ProgramData\HealthChecker\config\cloudflared-healthchecker.yml"
)
$ErrorActionPreference = "Stop"
# Fail-closed configurator: credentials must already exist under ProgramData.
# Never invent tunnel UUIDs, never write credentials into the source tree, and
# never require git. Tunnel failure recovery must not stop the loopback API.
$credentials = [IO.Path]::GetFullPath($CredentialsFile)
if (-not (Test-Path -LiteralPath $credentials -PathType Leaf)) { throw "tunnel_credentials_missing" }
if ([IO.Path]::GetExtension($credentials) -ne ".json") { throw "tunnel_credentials_invalid" }
if (-not $credentials.StartsWith("C:\ProgramData\HealthChecker\secrets\cloudflare\", [StringComparison]::OrdinalIgnoreCase)) { throw "tunnel_credentials_path_invalid" }
$output = [IO.Path]::GetFullPath($OutputPath)
if (-not $output.StartsWith("C:\ProgramData\HealthChecker\config\", [StringComparison]::OrdinalIgnoreCase)) { throw "tunnel_config_path_invalid" }
New-Item -ItemType Directory -Path (Split-Path $output) -Force | Out-Null
$yaml = @"
tunnel: $TunnelId
credentials-file: $($credentials.Replace('\', '/'))
ingress:
  - hostname: health.capitalstratasystems.com
    service: http://127.0.0.1:8766
    originRequest:
      httpHostHeader: health.capitalstratasystems.com
      connectTimeout: 10s
  - service: http_status:404
"@
$temporary = "$output.tmp.$PID"
$yaml | Set-Content -LiteralPath $temporary -Encoding utf8
Move-Item -LiteralPath $temporary -Destination $output -Force
Write-Output "healthchecker_tunnel_configured"
