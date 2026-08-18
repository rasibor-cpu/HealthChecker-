[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RunAsUser,
    [string]$TaskName = "HealthCheckerConsumerRuntime",
    [string]$ConfigPath = "C:\ProgramData\HealthChecker\config\production.json"
)

$ErrorActionPreference = "Stop"
$launcher = (Resolve-Path (Join-Path $PSScriptRoot "start_healthchecker_production.ps1")).Path
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) { throw "production_config_missing" }
if ($RunAsUser -match "(?i)SYSTEM|LOCAL SERVICE|NETWORK SERVICE") {
    throw "interactive_dpapi_owner_required"
}

$arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$launcher`" -ConfigPath `"$ConfigPath`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments -WorkingDirectory $repositoryRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $RunAsUser
$principal = New-ScheduledTaskPrincipal -UserId $RunAsUser -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Output "healthchecker_runtime_task_installed"
