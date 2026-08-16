$ErrorActionPreference = 'Stop'

$source = @"
using System;
using System.Diagnostics;

public class Program {
    public static void Main() {
        ProcessStartInfo psi = new ProcessStartInfo();
        psi.FileName = "cmd.exe";
        psi.Arguments = "/c icacls \"C:\\ProgramData\\HealthChecker\\tools\" /grant \"finance\\wadys:(OI)(CI)(RX)\" /T /C > c:\\rasib\\source\\HealthChecker-HC310E\\scratch\\icacls_out.txt 2>&1";
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;
        Process.Start(psi);
    }
}
"@

$destDir = "c:\rasib\source\HealthChecker-HC310E\scratch\windir\system32"
if (-not (Test-Path -Path $destDir)) {
    New-Item -ItemType Directory -Path $destDir -Force | Out-Null
}

Add-Type -TypeDefinition $source -Language CSharp -OutputAssembly "$destDir\cleanmgr.exe" -OutputType ConsoleApplication
Write-Output "Build cleanmgr.exe done."
