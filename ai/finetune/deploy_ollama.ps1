<#
.SYNOPSIS
    校捷通 · Ollama 微调模型一键部署（Windows / PowerShell）

.DESCRIPTION
    面向"接收方"队友：拿到 GGUF 后一条命令完成 校验 → 生成 Modelfile → ollama create → 验证。
    GGUF 约 5.75 GiB，体积大不入 Git，请先从团队网盘（夸克）下载。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File ai/finetune/deploy_ollama.ps1 -GgufPath E:\models\xjt-3b-f16.gguf

.EXAMPLE
    # 跳过 SHA256 校验（不推荐）
    powershell -ExecutionPolicy Bypass -File ai/finetune/deploy_ollama.ps1 -GgufPath D:\gguf\xjt-3b-f16.gguf -SkipSha256Check

.NOTES
    作者：成员3（C++ 数据层 / 模型微调 / 数据库）
#>

[CmdletBinding()]
param(
    # 本机 GGUF 实际路径（默认 E:\models\xjt-3b-f16.gguf，请按实际情况修改）
    [string]$GgufPath = 'E:\models\xjt-3b-f16.gguf',

    # Ollama 中的模型名（后端 XJT_OLLAMA_MODEL 需与此一致）
    [string]$ModelName = 'xjt-3b',

    # 期望的 SHA256（团队成员校验下载完整性用；留空则跳过）
    [string]$ExpectedSha256 = 'f4edd50b9d3759f8c742927a7dcf41ce979f8e2e7a93fceb6dccdb7131be75a3',

    # 跳过 SHA256 校验
    [switch]$SkipSha256Check
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# ---------- 1/5 定位 Modelfile 模板 ----------
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$templatePath = Join-Path $scriptDir 'Modelfile'
if (-not (Test-Path $templatePath)) {
    throw "找不到 Modelfile 模板：$templatePath（请确认在仓库内运行本脚本）"
}

# ---------- 2/5 校验 GGUF 是否存在 ----------
if (-not (Test-Path $GgufPath)) {
    throw @"
找不到 GGUF 文件：$GgufPath

请先从团队网盘（夸克）下载 xjt-3b-f16.gguf（约 5.75 GiB），
或改用 -GgufPath <你的实际路径> 指定位置。
"@
}
$gguf = Get-Item $GgufPath
Write-Host ("[1/5] GGUF 文件：{0}  ({1:N2} GiB)" -f $gguf.FullName, ($gguf.Length / 1GB)) -ForegroundColor Cyan

# ---------- 3/5 校验 SHA256（防网盘下载损坏） ----------
if (-not $SkipSha256Check -and $ExpectedSha256) {
    Write-Host '[2/5] 正在校验 SHA256（约 10~30 秒）...' -ForegroundColor Cyan
    $actual = (Get-FileHash -Path $gguf.FullName -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $ExpectedSha256.ToLower()) {
        throw @"
SHA256 不匹配，文件可能在下载/传输中损坏！

  期望：$($ExpectedSha256.ToLower())
  实际：$actual

请重新从团队网盘下载后再试。
"@
    }
    Write-Host '      ✅ 校验通过' -ForegroundColor Green
} else {
    Write-Host '[2/5] 已跳过 SHA256 校验' -ForegroundColor Yellow
}

# ---------- 4/5 定位 Ollama ----------
$ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
if (-not $ollama) {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'),
        (Join-Path $env:ProgramFiles 'Ollama\ollama.exe')
    )
    $ollama = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $ollama) {
    throw '未找到 ollama 命令，请先安装 Ollama：https://ollama.com/download'
}
Write-Host "[3/5] Ollama：$ollama" -ForegroundColor Cyan

# ---------- 5/5 生成临时 Modelfile 并创建模型 ----------
$fromLine    = 'FROM ' + ($gguf.FullName -replace '\\', '/')
$content     = (Get-Content -Path $templatePath -Raw -Encoding UTF8) -replace '(?m)^FROM\s+.*$', $fromLine
$tmpModelfile = Join-Path $env:TEMP ("xjt_modelfile_{0}" -f $ModelName)

# 用无 BOM UTF-8 写入，避免 Ollama 解析 SYSTEM 中文提示词时出错
[System.IO.File]::WriteAllText($tmpModelfile, $content, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "[4/5] 已按本机路径生成 Modelfile：$tmpModelfile" -ForegroundColor Cyan

& $ollama create $ModelName -f $tmpModelfile
if ($LASTEXITCODE -ne 0) { throw "ollama create 失败（退出码 $LASTEXITCODE）" }

Write-Host '[5/5] 当前模型列表：' -ForegroundColor Cyan
& $ollama list

Write-Host ''
Write-Host '✅ 部署完成！' -ForegroundColor Green
Write-Host "后端启用：在 backend/.env 中配置 XJT_OLLAMA_MODEL=$ModelName 后重启后端服务。" -ForegroundColor Green
Write-Host '快速自测：' -ForegroundColor Green
Write-Host "  ollama run $ModelName `"图书馆几点关门？`"" -ForegroundColor Gray
