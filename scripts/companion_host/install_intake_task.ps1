# HC-312B — Install HealthChecker automatic intake scheduled task (CONCRETE SCRIPT)
#
# Registers HealthCheckerIntake to run python -m backend.health_vault.intake.runner.

param(
    [Parameter(Mandatory = $false)]
    [string]$IntakeRoot = ''
)

$ErrorActionPreference = 'Stop'

# ── Approval gate ───────────────────────────────────────────────────────────

if ($env:HC_312B_ALLOW_INTAKE_TASK -ne 'I_UNDERSTAND') {
    Write-Host 'REFUSING: install_intake_task is inert until HC_312B_ALLOW_INTAKE_TASK=I_UNDERSTAND'
    exit 2
}

Import-Module ScheduledTasks -ErrorAction Stop

# ── Paths ────────────────────────────────────────────────────────────────────

$ToolsPython  = 'C:\ProgramData\HealthChecker\tools\python\3.12.10\python.exe'
$TaskName     = 'HealthCheckerIntake'
$Workspace    = 'C:\rasib\source\HealthChecker-HC310E'

if (-not (Test-Path -LiteralPath $ToolsPython)) {
    Write-Host "hc312b_intake_task:python_missing at $ToolsPython"
    exit 1
}

# ── Build task action ────────────────────────────────────────────────────────

# Execute python directly from the workspace.
$moduleArg = '-m backend.health_vault.intake.runner'
$action = New-ScheduledTaskAction -Execute $ToolsPython -Argument $moduleArg -WorkingDirectory $Workspace

# ── Register task ─────────────────────────────────────────────────────────────

# -Once trigger starts now, repeats every 5 minutes indefinitely.
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)

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
    -LogonType Interactive

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action   $action   `
    -Trigger  $trigger  `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

Write-Host ('hc312b_intake_task:ok task_registered name=' + $TaskName)
exit 0
