[CmdletBinding()]
param(
    [string]$InstallRoot = "C:\Program Files\HealthChecker",
    [string]$DataRoot = "C:\ProgramData\HealthChecker",
    [string]$ShortcutDirectory = "",
    [string]$RuntimeTaskName = "HealthCheckerConsumerRuntime",
    [switch]$RemoveUserData
)

$ErrorActionPreference = "Stop"

if (-not $ShortcutDirectory) {
    $ShortcutDirectory = [Environment]::GetFolderPath("CommonDesktopDirectory")
}
$shortcut = Join-Path $ShortcutDirectory "HealthChecker.lnk"

if (Test-Path -LiteralPath $shortcut -PathType Leaf) {
    Remove-Item -LiteralPath $shortcut -Force
}

$task = Get-ScheduledTask -TaskName $RuntimeTaskName -ErrorAction SilentlyContinue
if ($task) {
    $actions = @($task.Actions)
    $targetsThisInstall = $false
    foreach ($action in $actions) {
        $blob = "$(($action.Execute)) $(($action.Arguments)) $(($action.WorkingDirectory))"
        if ($blob -like "*$InstallRoot*") {
            $targetsThisInstall = $true
            break
        }
    }
    if ($targetsThisInstall) {
        try {
            Unregister-ScheduledTask -TaskName $RuntimeTaskName -Confirm:$false
        } catch {
            Write-Warning "Scheduled task '$RuntimeTaskName' could not be removed: $($_.Exception.Message)"
        }
    }
}

foreach ($suffix in @("", ".next", ".previous")) {
    $candidate = "$InstallRoot$suffix"
    if (Test-Path -LiteralPath $candidate -PathType Container) {
        $resolved = (Resolve-Path -LiteralPath $candidate).Path
        $expected = [IO.Path]::GetFullPath($candidate)
        if ($resolved -ne $expected) { throw "uninstall_path_identity_failed" }
        Remove-Item -LiteralPath $candidate -Recurse -Force
    }
}

if ($RemoveUserData) {
    # Explicit admin-only destructive path. Never the default. Requires the
    # caller to pass -RemoveUserData deliberately after recovery verification.
    throw (
        "remove_user_data_not_implemented_in_default_uninstaller: " +
        "ProgramData vault/config/secrets deletion is a separate recovery-verified " +
        "operation and is refused here to prevent accidental PHI loss. DataRoot=$DataRoot"
    )
}

# Deliberately preserve the authoritative vault, account state, configuration,
# protected keys, logs and backups. Data deletion is a separate authenticated,
# recovery-verified product operation and is never implied by uninstall.
Write-Output "HealthChecker application removed. User data preserved at $DataRoot."
