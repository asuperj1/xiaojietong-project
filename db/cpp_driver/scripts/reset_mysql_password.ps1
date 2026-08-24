# ============================================================
# Reset MySQL803307 root password via --init-file (MySQL official method)
#  - fixes: space in paths requires quoting each arg for Start-Process
# New password: jhq000000
# Log: C:\Users\asuperj\AppData\Local\Temp\mysql_reset2.log
# ============================================================
$log = "C:\Users\asuperj\AppData\Local\Temp\mysql_reset2.log"
Set-Content -Path $log -Value "START $(Get-Date -Format s)" -Encoding ASCII
function W($m) { Add-Content -Path $log -Value $m -Encoding ASCII }

$bin     = "C:\Program Files\MySQL\MySQL Server 8.0\bin"
$myini   = "C:\ProgramData\MySQL\MySQL Server 8.0\my.ini"
$initSql = "C:\ProgramData\MySQL\MySQL Server 8.0\reset_root_password.sql"
$errLog  = "C:\ProgramData\MySQL\MySQL Server 8.0\Data\DESKTOP-KF11EV8.err"
$newpass = "jhq000000"

try {
  # 1. write init sql + tighten ACL
  $sql = "ALTER USER 'root'@'localhost' IDENTIFIED WITH caching_sha2_password BY '$newpass';"
  Set-Content -Path $initSql -Value $sql -Encoding ASCII
  icacls $initSql /inheritance:r /grant:r "SYSTEM:F" "Administrators:F" | Out-Null
  W "init sql written"

  # 2. stop service
  Stop-Service MySQL803307 -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 4
  W "service stopped"

  # 3. start temp mysqld (each arg with spaces wrapped in quotes)
  $argList = @(
    '"--defaults-file=C:\ProgramData\MySQL\MySQL Server 8.0\my.ini"',
    '"--init-file=C:\ProgramData\MySQL\MySQL Server 8.0\reset_root_password.sql"',
    '--console'
  )
  $p = Start-Process -FilePath "$bin\mysqld.exe" -ArgumentList $argList -PassThru -WindowStyle Hidden
  W "temp mysqld pid=$($p.Id)"
  Start-Sleep -Seconds 18
  $alive = [bool](Get-Process -Id $p.Id -ErrorAction SilentlyContinue)
  W "temp alive after 18s = $alive"

  # 4. if alive, test new password then shutdown gracefully
  if ($alive) {
    $t = & "$bin\mysql.exe" -h 127.0.0.1 -P 3307 -u root --password=$newpass -e "SELECT 'NEWPASS_OK' AS s;" 2>&1 | Out-String
    W "newpass test: $t"
    & "$bin\mysqladmin.exe" -h 127.0.0.1 -P 3307 -u root --password=$newpass shutdown 2>&1 | Out-Null
    Start-Sleep -Seconds 6
  } else {
    # temp instance failed: capture err log tail for diagnosis
    W "--- err log tail (temp instance) ---"
    if (Test-Path $errLog) { Get-Content $errLog -Tail 20 | ForEach-Object { W $_ } }
  }

  # 5. cleanup init sql
  Remove-Item $initSql -Force -ErrorAction SilentlyContinue
  W "init sql removed"

  # 6. start service
  Start-Service MySQL803307
  Start-Sleep -Seconds 8
  $s = Get-Service MySQL803307
  W "service state = $($s.Status)"

  # 7. verify new password
  $vout = & "$bin\mysql.exe" -h 127.0.0.1 -P 3307 -u root --password=$newpass -e "SELECT 'FINAL_OK' AS status;" 2>&1 | Out-String
  W "final verify: $vout"
} catch {
  W ("ERROR: " + $_.Exception.Message)
}
W "DONE"
