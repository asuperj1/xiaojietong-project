# ============================================================
# 校捷通 B16 / B17 / B18 回归脚本
#
#   B16 文档解析器（Markdown / HTML / PDF 三格式经 /admin/knowledge/ingest 入库）
#   B17 批量导入管道（python -m app.cli.kb_import：预演 / 入库 / 续传 / 回滚）
#   B18 分层推送调度（D-7 触达、幂等、私密行不泄漏、时间基准回归）
#
# 前置：
#   1) MySQL 已启动且已导入建表脚本；后端已启动：
#        cd backend; & ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --port 8000
#   2) 在仓库根目录或任意目录执行（脚本会自动定位仓库与 backend）：
#        pwsh backend/tests/verify_b16_b18.ps1
#      （Windows PowerShell 5.1 亦可：文件上传会自动退回 curl.exe）
#
# 退出码：0 = 全部通过；1 = 有断言失败
# ============================================================
$ErrorActionPreference = "Stop"
$base = "http://127.0.0.1:8000/api/v1"
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$backend = Join-Path $repoRoot "backend"
$samples = Join-Path $repoRoot "docs\kb_samples"
$python = Join-Path $backend ".venv\Scripts\python.exe"

$script:failed = 0
function Check([string]$name, [bool]$ok, [string]$detail = "") {
    if ($ok) {
        Write-Host ("  [PASS] {0} {1}" -f $name, $detail)
    } else {
        Write-Host ("  [FAIL] {0} {1}" -f $name, $detail) -ForegroundColor Red
        $script:failed++
    }
}

function Invoke-Json([string]$method, [string]$uri, $body = $null) {
    $p = @{ Method = $method; Uri = $uri; Headers = $script:H }
    if ($body -ne $null) {
        $p.ContentType = "application/json"
        $p.Body = ($body | ConvertTo-Json -Depth 6 -Compress)
    }
    return Invoke-RestMethod @p
}

function Send-KbFile([string]$path, [hashtable]$fields = @{}) {
    # 显式传 filename：PowerShell 7.x 的 -Form 上传**中文文件名**时 multipart 头里
    # 的 filename 会变成空（实测 7.6），服务端虽有内容兜底，但显式传更稳。
    $leaf = Split-Path $path -Leaf
    if ($PSVersionTable.PSVersion.Major -ge 7) {
        $form = @{ file = Get-Item $path; filename = $leaf }
        foreach ($k in $fields.Keys) { $form[$k] = [string]$fields[$k] }
        return Invoke-RestMethod -Method Post -Uri "$base/admin/knowledge/ingest" -Headers $script:H -Form $form
    }
    $curlArgs = @("-s", "-X", "POST", "$base/admin/knowledge/ingest",
                  "-H", "Authorization: Bearer $($script:token)",
                  "-F", "file=@$path", "-F", "filename=$leaf")
    foreach ($k in $fields.Keys) { $curlArgs += @("-F", "$k=$($fields[$k])") }
    return (& curl.exe @curlArgs | ConvertFrom-Json)
}

Write-Host "== 0) health + login =="
$h = Invoke-RestMethod -Uri "$base/health"
Check "health" ($h.db -eq "ok") ("db=" + $h.db + " cpp_ext=" + $h.cpp_ext)

$login = Invoke-RestMethod -Method Post -Uri "$base/auth/wechat-login" -ContentType "application/json" -Body '{"code":"test1"}'
$script:token = $login.data.token
$script:H = @{ Authorization = "Bearer $($script:token)" }
$uid = $login.data.user.id
Check "login(admin)" ($login.data.user.id -gt 0) ("user_id=" + $uid + " role=" + $login.data.user.role)

# ------------------------------------------------------------ B16 ----
Write-Host ""
Write-Host "== B16) 文档解析器：Markdown / HTML / PDF =="
$files = @(
    @{ path = (Join-Path $samples "图书馆\图书馆开馆时间与借阅规则.md"); fmt = "markdown"; key = "b16-1.md" },
    @{ path = (Join-Path $samples "办事流程\校园卡补办流程.html");       fmt = "html";     key = "b16-2.html" },
    @{ path = (Join-Path $samples "校医院\校医院就诊与报销指南.pdf");     fmt = "pdf";      key = "b16-3.pdf" },
    @{ path = (Join-Path $samples "后勤\宿舍报修服务说明.txt");           fmt = "text";     key = "b16-4.txt" }
)

foreach ($f in $files) {
    $leaf = Split-Path $f.path -Leaf
    if (-not (Test-Path $f.path)) { Check "fixture $leaf" $false "文件缺失"; continue }
    $src = "verify/" + $f.key        # ASCII 来源键，跨终端编码更稳

    try {
        # 1) dry_run 预览（不写库）
        $dry = Send-KbFile $f.path @{ dry_run = "true"; index = "false" }
        Check "dry_run $($f.fmt)" ($dry.data.fmt -eq $f.fmt -and $dry.data.chars -gt 0) `
            ("fmt=" + $dry.data.fmt + " chars=" + $dry.data.chars + " title=" + $dry.data.title)

        # 2) 正式入库（index=false：只入库，向量化稍后统一做）
        # overwrite=true：夹具内容更新时允许覆盖同来源文档；内容未变仍走 skipped（幂等）
        $res = Send-KbFile $f.path @{ index = "false"; source_url = $src; overwrite = "true" }
        $ok = ($res.data.action -eq "created" -or $res.data.action -eq "skipped" -or $res.data.action -eq "updated") -and $res.data.doc_id -gt 0
        Check "ingest $($f.fmt)" $ok ("action=" + $res.data.action + " status=" + $res.data.status + " doc_id=" + $res.data.doc_id + " chars=" + $res.data.chars)

        # 3) 幂等：同文件再传一次 → skipped（内容未变）
        $again = Send-KbFile $f.path @{ index = "false"; source_url = $src }
        Check "idempotent $($f.fmt)" ($again.data.action -eq "skipped") ("action=" + $again.data.action)
    } catch {
        # 单个格式失败不中断整轮（否则后面的格式与告警都看不到）
        Check "$($f.fmt) 全流程" $false ([string]$_.ErrorDetails.Message)
    }
}

# 4) 非支持格式 → 契约错误 1001（不是 422/500）
$badFile = Join-Path $env:TEMP "xjt_verify_unsupported.docx"
Set-Content -Path $badFile -Value "not a real docx" -Encoding ascii
$rejected = $false
$detail = ""
try {
    $r = Send-KbFile $badFile @{}
    $rejected = ($r.code -ne 0)          # curl 路径下 4xx 也返回 JSON
    $detail = "code=" + $r.code
} catch {
    $detail = [string]$_.ErrorDetails.Message
    $rejected = ($detail -match '"code"\s*:\s*1001')
}
Check "unsupported ext rejected" $rejected $detail
Remove-Item $badFile -ErrorAction SilentlyContinue

# 5) 知识库列表与规模
$docs = Invoke-Json "Get" "$base/admin/knowledge/docs?size=20"
$stats = Invoke-Json "Get" "$base/admin/knowledge/stats"
Check "docs list total" ($docs.data.total -ge 1) ("total=" + $docs.data.total)
Check "stats shape" ($stats.data.total -gt 0) ("total=" + $stats.data.total + " ready=" + $stats.data.ready + " pending=" + $stats.data.pending)

# ------------------------------------------------------------ B17 ----
Write-Host ""
Write-Host "== B17) 批量导入管道（命令行） =="
if (-not (Test-Path $python)) {
    Check "venv python" $false $python
} else {
    Push-Location $backend      # `python -m app.cli.kb_import` 需要以 backend/ 为工作目录
    try {
        # 1) 预演（无需数据库）
        $out1 = & $python -m app.cli.kb_import $samples --dry-run --no-state --json 2>&1
        $code1 = $LASTEXITCODE
        $text1 = ($out1 | Out-String)
        $rep1 = $null
        $head1 = ($text1 -split "(?m)^\{", 2)[1]
        if ($head1) { $rep1 = ("{" + $head1) | ConvertFrom-Json }
        Check "dry-run exit" ($code1 -eq 0) ("code=" + $code1)
        Check "dry-run parsed all" ($rep1 -and $rep1.files -ge 4) ("files=" + $(if ($rep1) { $rep1.files } else { "?" }))

        # 2) 正式导入（分类按子目录识别；source-prefix 便于回滚）
        $out2 = & $python -m app.cli.kb_import $samples --source-prefix samples/ 2>&1
        $code2 = $LASTEXITCODE
        Check "import exit" ($code2 -eq 0) ("code=" + $code2)

        # 3) 再跑一次：断点续传应全部 skip
        $out3 = & $python -m app.cli.kb_import $samples --source-prefix samples/ 2>&1
        $code3 = $LASTEXITCODE
        $text3 = ($out3 | Out-String)
        Check "resume skips unchanged" ($code3 -eq 0 -and $text3 -match "skip\(unchanged\)") "第二次运行命中续传快路径"
    } finally {
        Pop-Location
    }

    $stats2 = Invoke-Json "Get" "$base/admin/knowledge/stats"
    Check "kb grew" ($stats2.data.total -ge $stats.data.total) `
        ("total=" + $stats2.data.total + "（导入前 " + $stats.data.total + "）")
}

# ------------------------------------------------------------ B18 ----
Write-Host ""
Write-Host "== B18) 分层推送调度（D-7 / D-2） =="
$remindAt = (Get-Date).AddDays(7).ToString("yyyy-MM-dd 18:00:00")

# 反向对照基线：先记录**调度前**的公共通知 id 集合。
# 私密推送若真被挡住，公共列表在调度前后应当**完全一致**（集合相同）；
# 若列表恒为空，下面的"未泄漏"断言就会假通过，因此必须同时断言非空 + 集合稳定。
$pubBefore = @((Invoke-Json "Get" "$base/life/notices?page=1&size=100").data.items | ForEach-Object { $_.id })

$mk = Invoke-Json "Post" "$base/agent/reminders" @{ content = "考研报名截止（verify 脚本）"; remind_at = $remindAt }
$rid = $mk.data.reminder_id
Check "create reminder" ($rid -gt 0) ("reminder_id=" + $rid + " remind_at=" + $remindAt)

# 1) 预演
$dry = Invoke-Json "Post" "$base/admin/notices/dispatch" @{ dry_run = $true; async = $false; user_id = $uid }
$mine = @($dry.data.pushed | Where-Object { $_.ref_id -eq $rid -and $_.kind -eq "reminder" })
Check "dry dispatch hits D7" ($mine.Count -ge 1 -and $mine[0].stage -eq "D7") ("stage=" + $(if ($mine.Count) { $mine[0].stage } else { "-" }))

# 2) 正式调度
$real = Invoke-Json "Post" "$base/admin/notices/dispatch" @{ async = $false; user_id = $uid }
$hit = @($real.data.pushed | Where-Object { $_.ref_id -eq $rid -and $_.kind -eq "reminder" })
Check "dispatch pushes delivery" ($hit.Count -ge 1 -and $hit[0].delivery_id -gt 0) `
    ("notice_id=" + $(if ($hit.Count) { $hit[0].notice_id } else { "-" }) + " delivery_id=" + $(if ($hit.Count) { $hit[0].delivery_id } else { "-" }))
$pushNoticeId = if ($hit.Count) { $hit[0].notice_id } else { 0 }

# 3) 本人可见：未读列表含该通知 id（正向）
$unread = Invoke-Json "Get" "$base/life/notices/unread?page=1&size=50"
$uc = Invoke-Json "Get" "$base/life/notices/unread-count"
$inUnread = @($unread.data.items | Where-Object { $_.id -eq $pushNoticeId })
Check "appears in unread" ($inUnread.Count -ge 1) ("未读数=" + $uc.data.count)

# 4) 私密行不泄漏到公共列表（按 id 判定 + 双重反向对照）
$pubAfter = @((Invoke-Json "Get" "$base/life/notices?page=1&size=100").data.items | ForEach-Object { $_.id })
Check "no private leak in /life/notices" ($pushNoticeId -gt 0 -and ($pubAfter -notcontains $pushNoticeId)) `
    ("私密 id=" + $pushNoticeId + " 在公共列表中=" + ($pubAfter -contains $pushNoticeId))
Check "public list non-empty (反真空)" ($pubAfter.Count -ge 1) ("items=" + $pubAfter.Count)
$diff = @(Compare-Object $pubBefore $pubAfter)
Check "public list unchanged after push (反向对照)" ($diff.Count -eq 0) `
    ("调度前 " + $pubBefore.Count + " 条 → 调度后 " + $pubAfter.Count + " 条，差异 " + $diff.Count + " 条")

# 5) 幂等：重复调度不重复推送
$again = Invoke-Json "Post" "$base/admin/notices/dispatch" @{ async = $false; user_id = $uid }
$dup = @($again.data.pushed | Where-Object { $_.ref_id -eq $rid })
Check "idempotent re-dispatch" ($dup.Count -eq 0) ("skipped.already_pushed=" + $again.data.skipped.already_pushed)

# 6) 时间基准：再过 5 天 → 命中 D-2
$later = (Get-Date).AddDays(5).ToString("s")
$d2 = Invoke-Json "Post" "$base/admin/notices/dispatch" @{ async = $false; now = $later; user_id = $uid }
$hit2 = @($d2.data.pushed | Where-Object { $_.ref_id -eq $rid -and $_.stage -eq "D2" })
Check "D-2 stage fires with time override" ($hit2.Count -ge 1) ("now=" + $later)

# 7) 到期一览
$pending = Invoke-Json "Get" "$base/admin/notices/pending?limit=200"
$minePending = @($pending.data.items | Where-Object { $_.ref_id -eq $rid -and $_.kind -eq "reminder" })
Check "pending list marks pushed" ($minePending.Count -ge 1 -and $minePending[0].pushed -eq $true) `
    ("days_left=" + $(if ($minePending.Count) { $minePending[0].days_left } else { "-" }))

# 8) 回收测试数据
$purge = Invoke-Json "Post" "$base/admin/notices/purge-private" @{ kind = "reminder"; ref_id = $rid }
Check "purge private pushes" ($purge.data.notices -ge 1) ("notices=" + $purge.data.notices + " deliveries=" + $purge.data.deliveries)
$null = Invoke-Json "Put" "$base/agent/reminders/$rid/done"

Write-Host ""
if ($script:failed -eq 0) {
    Write-Host "B16 / B17 / B18 全部断言通过 (ALL PASS)" -ForegroundColor Green
    exit 0
} else {
    Write-Host ("存在 {0} 项断言失败 (FAILED)" -f $script:failed) -ForegroundColor Red
    exit 1
}
