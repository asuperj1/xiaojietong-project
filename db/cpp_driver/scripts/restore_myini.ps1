# Restore original my.ini from backup and start MySQL803307 (admin)
$log = "C:\Users\asuperj\AppData\Local\Temp\mysql_restore.log"
Set-Content -Path $log -Value "RESTORE START" -Encoding ASCII
function W($m) { Add-Content -Path $log -Value $m -Encoding ASCII }

$myini = "C:\ProgramData\MySQL\MySQL Server 8.0\my.ini"
$bak   = "C:\ProgramData\MySQL\MySQL Server 8.0\my.ini.bak_20260824_191958"

try {
  if (Test-Path $bak) {
    Copy-Item $bak $myini -Force
    W "my.ini restored from backup"
  } else {
    W "NO BACKUP FOUND (my.ini left as-is)"
  }

  Start-Service MySQL803307 -ErrorAction Stop
  Start-Sleep -Seconds 8
  $s = Get-Service MySQL803307
  W ("service state=" + $s.Status)

  $c = Test-NetConnection 127.0.0.1 -Port 3307 -WarningAction SilentlyContinue
  W ("3307 open=" + $c.TcpTestSucceeded)
} catch {
  W ("ERROR: " + $_.Exception.Message)
}
W "RESTORE DONE"
