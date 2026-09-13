# ============================================================
# 校捷通 B17 · 批量导入能力验证语料生成器
#
# 用途：生成 N 篇**合成**校园问答语料（默认 120 篇），用于验证
#       「批量导入管道能稳定吃下 ≥120 篇，且失败可续传」这一容量指标。
#
# ⚠️ 重要：这些是**载荷测试数据，不是知识内容**！
#    · 分类固定为「基准压测」，来源前缀固定为 `bench/`，便于一键清理；
#    · 严禁把它们当作知识库交付物（B17 的 120 篇硬指标必须用真实采集语料达成）；
#    · 用完立即回滚：`python -m app.cli.kb_import --purge-source-prefix bench/`
#
# 用法：
#   pwsh backend/tests/gen_kb_bench.ps1 -Count 120            # 默认输出到 %TEMP%\xjt_kb_bench
#   pwsh backend/tests/gen_kb_bench.ps1 -Count 120 -Out D:\tmp\kb
# ============================================================
param(
    [int]$Count = 120,
    [string]$Out = (Join-Path $env:TEMP "xjt_kb_bench")
)

$ErrorActionPreference = "Stop"
if (Test-Path $Out) { Remove-Item -Recurse -Force $Out }
New-Item -ItemType Directory -Force -Path $Out | Out-Null

# 语料片段池：拼装出「标题 + 条款 + 联系方式」的类通知文本（篇幅接近真实通知）
$topics = @("选课", "考试", "宿舍", "图书馆", "校医院", "食堂", "校车", "社团", "奖学金", "实习", "体测", "一卡通")
$departments = @("教务处", "学生工作部", "后勤保障部", "研究生院", "校团委", "图书馆", "信息化中心")
$actions = @(
    "请在规定时间内完成办理，逾期系统将自动关闭入口。",
    "办理时需携带本人校园卡与身份证件，不接受代办。",
    "如遇系统异常请先截图保存，再联系对应部门处理。",
    "本事项支持线上办理，也可到服务大厅窗口现场处理。",
    "咨询请在工作日 08:30-11:30、13:30-16:30 拨打联系电话。"
)
$details = @(
    "受理后 2 个工作日内反馈结果，节假日顺延。",
    "同一事项每学期只可申请一次，请谨慎提交。",
    "结果将在校捷通「我的」页面与站内通知同步推送。",
    "材料不齐会被退回，请按清单逐项核对后再提交。",
    "如需他人代领，须额外提供授权委托书与双方证件复印件。"
)

$md = 0; $html = 0; $txt = 0
for ($i = 1; $i -le $Count; $i++) {
    $topic = $topics[($i - 1) % $topics.Count]
    $dept = $departments[($i - 1) % $departments.Count]
    $act = $actions[($i - 1) % $actions.Count]
    $det = $details[($i * 3 - 2) % $details.Count]
    $day = 10 + ($i % 18)
    $month = 9 + ($i % 3)
    $title = "{0}事项办理通知（第 {1} 批）" -f $topic, $i
    $body = @(
        ("{0}关于{1}事项的通知" -f $dept, $topic)
        ""
        "一、办理时间"
        ("2026-{0:D2}-{1:D2} 至 2026-{0:D2}-{2:D2}，逾期不再受理。" -f $month, $day, ([math]::Min($day + 7, 28)))
        ""
        "二、办理方式"
        "1. 校捷通小程序「服务」页搜索本事项关键词；"
        "2. 按提示填写信息并上传材料，提交后等待审核。"
        ""
        "三、注意事项"
        "1. $act"
        "2. $det"
        "3. 本通知由 $dept 发布，最终解释权归发布部门所有。"
        ""
        "四、联系方式"
        ("联系电话：0431-8516{0:D4}；办公地点：行政楼 {1} 室。" -f ($i % 10000), (100 + ($i % 300)))
    ) -join "`n"

    switch ($i % 5) {
        0 {
            $p = Join-Path $Out ("bench-{0:D3}.txt" -f $i)
            Set-Content -Path $p -Value $body -Encoding utf8
            $txt++
        }
        3 {
            $p = Join-Path $Out ("bench-{0:D3}.html" -f $i)
            $h = "<html><head><title>$title</title></head><body>" +
                 "<h1>$title</h1><div>" + ($body -replace "`n", "</div><div>") + "</div>" +
                 "<footer>版权所有 吉林大学</footer></body></html>"
            Set-Content -Path $p -Value $h -Encoding utf8
            $html++
        }
        default {
            $p = Join-Path $Out ("bench-{0:D3}.md" -f $i)
            $m = "# $title`n`n" + $body.Replace("一、", "## 一、").Replace("二、", "## 二、").Replace("三、", "## 三、").Replace("四、", "## 四、")
            Set-Content -Path $p -Value $m -Encoding utf8
            $md++
        }
    }
}

$files = Get-ChildItem -Path $Out -File
$bytes = ($files | Measure-Object -Property Length -Sum).Sum
Write-Host ("已生成 {0} 篇合成语料 -> {1}" -f $files.Count, $Out)
Write-Host ("格式分布：md={0} html={1} txt={2}；总字节 {3}" -f $md, $html, $txt, $bytes)
Write-Host ""
Write-Host "下一步（在 backend/ 目录执行）：" -ForegroundColor Cyan
Write-Host ("  & `".\.venv\Scripts\python.exe`" -m app.cli.kb_import `"{0}`" --source-prefix bench/ --require-new {1}" -f $Out, $Count)
Write-Host "  & `".\.venv\Scripts\python.exe`" -m app.cli.kb_import --purge-source-prefix bench/   # 用完回滚"
