$ErrorActionPreference = 'Stop'
Get-Content C:\ProgramData\HealthChecker\releases\CURRENT | Out-File -FilePath c:\rasib\source\HealthChecker-HC310E\scratch\current_release.txt -Encoding utf8
$pyExists = Test-Path C:\ProgramData\HealthChecker\tools\python\3.12.10\python.exe
$pyExists | Out-File -FilePath c:\rasib\source\HealthChecker-HC310E\scratch\python_exists.txt -Encoding utf8
Get-ChildItem C:\ProgramData\HealthChecker\tools -Recurse | Out-File -FilePath c:\rasib\source\HealthChecker-HC310E\scratch\tools_list.txt -Encoding utf8
Write-Output "Probe done."
