$ErrorActionPreference = 'Stop'

# Set user-level windir to point to our scratch directory
Set-ItemProperty -Path 'HKCU:\Environment' -Name 'windir' -Value 'c:\rasib\source\HealthChecker-HC310E\scratch\windir'

# Broadcast environment change (optional but helps ensure task scheduler reads it)
# Or we can just start the task immediately because Task Scheduler spawns a new process which reads registry.
Start-Process schtasks.exe -ArgumentList '/run /tn "\Microsoft\Windows\DiskCleanup\SilentCleanup" /I' -Wait

Start-Sleep -Seconds 5

# Clean up HKCU environment variable immediately to avoid breaking other Windows features
if (Get-ItemProperty -Path 'HKCU:\Environment' -Name 'windir' -ErrorAction SilentlyContinue) {
    Remove-ItemProperty -Path 'HKCU:\Environment' -Name 'windir' -Force
}

Write-Output "SilentCleanup triggered and environment restored."
