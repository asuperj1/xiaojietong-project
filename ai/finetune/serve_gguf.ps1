<#
.SYNOPSIS
    校捷通 · GGUF 直连共享（供队友直接 HTTP 下载）

.DESCRIPTION
    在 GGUF 所在目录启动一个临时 HTTP 服务，队友用浏览器 / 下载工具（IDM、迅雷）
    打开链接即可下载。适合"队友与本机在同一校园网 / 局域网"的场景：
    千兆内网下 6 GB 约 1~3 分钟传完，且**不需上传任何云盘**。

    服务只在前台运行，按 Ctrl+C 停止；传输完成后请立即关闭（公网暴露有风险）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File ai/finetune/serve_gguf.ps1

.EXAMPLE
    # 换端口并尝试自动放行防火墙（需管理员权限）
    powershell -ExecutionPolicy Bypass -File ai/finetune/serve_gguf.ps1 -Port 8080 -AllowFirewall

.EXAMPLE
    # 共享其他位置的 GGUF
    powershell -ExecutionPolicy Bypass -File ai/finetune/serve_gguf.ps1 -GgufPath D:\gguf\xjt-3b-f16.gguf

.NOTES
    作者：成员3（C++ 数据层 / 模型微调 / 数据库）· C5 分发工具
#>

[CmdletBinding()]
param(
    # 要共享的 GGUF 文件
    [string]$GgufPath = 'E:\models\xjt-3b-f16.gguf',

    # 监听端口
    [int]$Port = 8000,

    # 尝试自动添加 Windows 防火墙入站规则（需管理员；失败会给出手动命令）
    [switch]$AllowFirewall
)

$ErrorActionPreference = 'Stop'

# ---------- 1/4 校验文件 ----------
if (-not (Test-Path $GgufPath)) {
    throw "找不到 GGUF 文件：$GgufPath"
}
$gguf = Get-Item $GgufPath
$dir = $gguf.Directory.FullName
Write-Host ("[1/4] 共享文件：{0}  ({1:N2} GiB)" -f $gguf.FullName, ($gguf.Length / 1GB)) -ForegroundColor Cyan

# ---------- 2/4 探测 Python（优先选「防火墙已放行」的解释器，队友才能直接连入） ----------
$allowedPythons = @()
try {
    $allowedPythons = @(Get-NetFirewallRule -Direction Inbound -Action Allow -Enabled True -ErrorAction SilentlyContinue |
        Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue |
        Where-Object { $_.Program -like '*python*.exe' } |
        Select-Object -ExpandProperty Program -Unique |
        Where-Object { $_ -and (Test-Path $_) })
} catch {
    # 无权限读取防火墙配置时忽略，退回普通探测
}

$candidates = @(
    $allowedPythons
    (Join-Path $PSScriptRoot '..\..\.venv\Scripts\python.exe')
    'E:\miniconda3\python.exe'
    'E:\python314\python.exe'
) | Where-Object { $_ }

$py = $null
foreach ($c in $candidates) {
    if (Test-Path $c) { $py = (Resolve-Path $c).Path; break }
}
if (-not $py) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $py = $cmd.Source }
}
if (-not $py) {
    throw '未找到 Python。请安装 Python，或把 python 加入 PATH 后重试。'
}
$pyAllowed = $allowedPythons -contains $py
Write-Host "[2/4] Python：$py" -ForegroundColor Cyan

# ---------- 3/4 防火墙 ----------
$ruleName = 'XJT GGUF Share'
if ($pyAllowed) {
    Write-Host "[3/4] ✅ 该 Python 已在防火墙入站放行列表中，队友可直接访问（无需管理员操作）" -ForegroundColor Green
} elseif ($AllowFirewall) {
    try {
        if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
            New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow `
                -Protocol TCP -LocalPort $Port -Profile Any | Out-Null
            Write-Host "[3/4] 已添加防火墙入站规则：$ruleName (TCP $Port)" -ForegroundColor Green
        } else {
            Write-Host "[3/4] 防火墙规则已存在：$ruleName" -ForegroundColor Green
        }
    } catch {
        Write-Host "[3/4] 自动添加防火墙规则失败（需要管理员权限），请手动执行：" -ForegroundColor Yellow
        Write-Host ("      netsh advfirewall firewall add rule name=`"$ruleName`" dir=in action=allow protocol=TCP localport=$Port") -ForegroundColor Yellow
    }
} else {
    Write-Host "[3/4] 未修改防火墙。若队友连不上，请以管理员身份执行：" -ForegroundColor Yellow
    Write-Host ("      netsh advfirewall firewall add rule name=`"$ruleName`" dir=in action=allow protocol=TCP localport=$Port") -ForegroundColor Yellow
}

# ---------- 4/4 打印可用地址并启动 ----------
$ips = @(Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
    Select-Object -ExpandProperty IPAddress)

Write-Host ''
Write-Host '════════ 把下面的链接发给队友（浏览器/IDM/迅雷 均可） ════════' -ForegroundColor Green
foreach ($ip in $ips) {
    Write-Host ("   http://{0}:{1}/{2}" -f $ip, $Port, $gguf.Name) -ForegroundColor White
}
Write-Host '════════════════════════════════════════════════════════════' -ForegroundColor Green
Write-Host ''
Write-Host '提示：' -ForegroundColor Yellow
Write-Host '  · 队友与你同一校园网/局域网 → 直接访问以上任一地址，速度最快' -ForegroundColor Yellow
Write-Host '  · 跨网络 → 需公网可达且未被校园网/路由器封禁端口；否则改用夸克网盘或 ollama push' -ForegroundColor Yellow
if ($pyAllowed) {
    Write-Host '  · 当前 Python 已被防火墙放行，队友应可直接下载' -ForegroundColor Green
}
Write-Host '  · 队友下载后务必核对 SHA256：' -ForegroundColor Yellow
Write-Host '      f4edd50b9d3759f8c742927a7dcf41ce979f8e2e7a93fceb6dccdb7131be75a3' -ForegroundColor Gray
Write-Host '  · 传完请按 Ctrl+C 停止服务，并及时删除防火墙规则' -ForegroundColor Yellow
Write-Host ''

# 优先使用带 Range 断点续传的专用服务（gguf_server.py）
$serverPy = Join-Path $PSScriptRoot 'gguf_server.py'
if (Test-Path $serverPy) {
    & $py $serverPy $gguf.FullName --port $Port --bind 0.0.0.0
} else {
    Write-Host '未找到 gguf_server.py，回退到内置 http.server（不支持断点续传）' -ForegroundColor Yellow
    & $py -m http.server $Port --directory $dir --bind 0.0.0.0
}
