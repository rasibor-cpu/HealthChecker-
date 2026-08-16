# HC-314A — Install HealthChecker unattended Gmail acquisition scheduled task (CONCRETE SCRIPT)
#
# Registers HealthCheckerGmailAcquisition to run python -m backend.health_vault.acquisition.runner.

param()

$ErrorActionPreference = 'Stop'

# ── Approval gate ───────────────────────────────────────────────────────────

if ($env:HC_314A_ALLOW_ACQUISITION_TASK -ne 'I_UNDERSTAND') {
    Write-Host 'REFUSING: install_acquisition_task is inert until HC_314A_ALLOW_ACQUISITION_TASK=I_UNDERSTAND'
    exit 2
}

Import-Module ScheduledTasks -ErrorAction Stop

# ── Paths ────────────────────────────────────────────────────────────────────

$ToolsPython  = 'C:\ProgramData\HealthChecker\tools\python\3.12.10\python.exe'
$TaskName     = 'HealthCheckerGmailAcquisition'
$Workspace    = 'C:\rasib\source\HealthChecker-HC310E'

if (-not (Test-Path -LiteralPath $ToolsPython)) {
    Write-Host "hc314a_acquisition_task:python_missing at $ToolsPython"
    exit 1
}

# ── Build task action ────────────────────────────────────────────────────────

# Execute python directly from the workspace.
$moduleArg = '-m backend.health_vault.acquisition.runner'
$action = New-ScheduledTaskAction -Execute $ToolsPython -Argument $moduleArg -WorkingDirectory $Workspace

# ── Register task ─────────────────────────────────────────────────────────────

# -AtStartup trigger runs on machine boot.
$bootTrigger = New-ScheduledTaskTrigger -AtStartup
# -Once trigger starts now, repeats every 5 minutes indefinitely.
$intervalTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)

# IgnoreNew = single instance policy; bounded restart; NEVER reboot on failure.
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 2) `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable

$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType S4U

try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action   $action   `
        -Trigger  @($bootTrigger, $intervalTrigger) `
        -Settings $settings `
        -Principal $principal `
        -Force | Out-Null
} catch {
    Write-Host "hc314b_provisioning_gate: S4U task registration failed. You may need 'Log on as a batch job' rights or must provision the password manually."
    exit 3
}

Write-Host ('hc314a_acquisition_task:ok task_registered name=' + $TaskName)
exit 0
