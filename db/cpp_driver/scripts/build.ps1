# ============================================================
# 校捷通 C++ 数据访问层 · Windows 一键构建（环境自适应）
# 自动检测：MySQL Server 目录 / 生成器
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts/build.ps1
#   powershell -ExecutionPolicy Bypass -File scripts/build.ps1 -Config Debug
# 参数：
#   -Config    构建配置（Release/Debug），默认 Release
#   -MYSQL_DIR 手动指定 MySQL 安装目录（跳过自动检测）
# ============================================================
param(
    [string]$Config = "Release",
    [string]$MYSQL_DIR = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# ---- 1. 自动检测 MySQL（环境自适应）----
if (-not $MYSQL_DIR) {
    $mysql = Get-ChildItem "C:\Program Files\MySQL\MySQL Server *" -Directory `
        | Sort-Object Name -Descending | Select-Object -First 1
    if ($mysql) {
        $MYSQL_DIR = $mysql.FullName
        Write-Host "[MySQL] 自动检测到: $MYSQL_DIR"
    } else {
        Write-Error "未找到 MySQL Server。请安装，或用 -MYSQL_DIR 指定目录。"
        exit 1
    }
}

# ---- 2. 配置（首次或缓存失效时）----
$cacheFile = Join-Path $root "build\CMakeCache.txt"
$cacheMysqlOk = $false
if (Test-Path $cacheFile) {
    $cacheMysqlOk = (Select-String -Path $cacheFile -Pattern "MYSQL_DIR:PATH=$([regex]::Escape($MYSQL_DIR))" -Quiet)
}
if (-not (Test-Path $cacheFile) -or -not $cacheMysqlOk) {
    Write-Host "[CMake] 配置中..."
    cmake -B build -S $root -A x64 "-DMYSQL_DIR=$MYSQL_DIR"
    if ($LASTEXITCODE -ne 0) { Write-Error "CMake 配置失败"; exit 1 }
} else {
    Write-Host "[CMake] 缓存有效，跳过配置"
}

# ---- 3. 构建 ----
Write-Host "[Build] 构建 $Config ..."
cmake --build build --config $Config --target jt_db
if ($LASTEXITCODE -ne 0) { Write-Error "构建失败"; exit 1 }

Write-Host ""
Write-Host "=============================================="
Write-Host "  构建成功！"
Write-Host "  产物: backend/app/db/native/jt_db.pyd"
Write-Host "  测试: cd test && XJT_DB_PASSWORD=密码 python test_py.py"
Write-Host "=============================================="
