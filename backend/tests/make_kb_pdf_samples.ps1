# ============================================================
# 校捷通 B16 文档解析器 · PDF 验收夹具生成器
#
# 产出：docs/kb_samples/校医院/校医院就诊与报销指南.pdf
#
# 为什么用脚本生成而不是直接提交 PDF：
#   PDF 是二进制，评审无法审阅 diff；脚本生成则结构透明、可复现、可改内容。
#
# 夹具包含真实的 PDF 结构（对象表 / xref / 页面树 / Type0-Identity-H 字体 /
# /ToUnicode CMap / Tj 与 T* 文本算子），用于验证「PDF 解析 -> 入库 -> 检索」全链路。
# 刻意不嵌入中文字体子集（避免几 MB 二进制进仓库）：
#   · 部分阅读器打开时中文可能显示为方框；
#   · 但文本抽取路径（本项目关心的一环）完全可用。
#
# 运行：pwsh backend/tests/make_kb_pdf_samples.ps1
# ============================================================
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$outDir = Join-Path $repoRoot "docs\kb_samples\校医院"
$target = Join-Path $outDir "校医院就诊与报销指南.pdf"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

# ---------------------------------------------------------------- 正文内容 ----
# 第 1 页（中文，Identity-H + ToUnicode）
$page1 = @(
    "校医院就诊与报销指南",
    "",
    "一、门诊时间",
    "中心校区校医院：周一至周五 08:00-11:30、13:30-17:00。",
    "周六 08:30-11:30 只开设内科与外科急诊。",
    "夜间急诊 17:00 至次日 08:00，请到一楼急诊室。",
    "",
    "二、就诊流程",
    "1. 挂号：一楼自助机或校捷通小程序预约挂号，普通门诊 2 元。",
    "2. 就诊：内科 202 室、外科 205 室、口腔科 301 室。",
    "3. 缴费取药：一楼药房凭处方取药，也可自助缴费。",
    "",
    "三、医保报销",
    "校医院就诊刷校园卡直接结算，报销比例 80%。",
    "校外定点医院需先垫付，凭病历与发票在每月 1-10 日到二楼医保办报销。",
    "未开转诊单自行就医的，报销比例降为 50%。"
)

# 第 2 页（英文，Helvetica 字面量字符串）
$page2 = @(
    "XJT Campus Health Center - Key Info (fixture page 2)",
    "",
    "Clinic hours: Mon-Fri 08:00-11:30 / 13:30-17:00",
    "Night emergency: 17:00-08:00, Room 101",
    "Reimbursement counter: 2F, 1st-10th of each month",
    "Insurance desk phone: 0431-8516xxxx",
    "",
    "This page exists to exercise the WinAnsi literal-string path",
    "of the builtin PDF extractor (Tj with ( ) strings)."
)

# ------------------------------------------------------- 中文 -> 码位映射 ----
$map = @{}
$next = 1
foreach ($line in $page1) {
    foreach ($ch in $line.ToCharArray()) {
        if (-not $map.ContainsKey($ch)) {
            $map[$ch] = $next
            $next++
        }
    }
}

function ConvertTo-CidHex([string]$text, [hashtable]$map) {
    $sb = New-Object System.Text.StringBuilder
    foreach ($ch in $text.ToCharArray()) {
        [void]$sb.Append(("{0:X4}" -f [int]$map[$ch]))
    }
    return "<" + $sb.ToString() + ">"
}

function ConvertTo-Literal([string]$text) {
    # 转义 PDF 字面量字符串中的 \ ( )
    $escaped = $text.Replace("\", "\\").Replace("(", "\(").Replace(")", "\)")
    return "(" + $escaped + ")"
}

# ------------------------------------------------------------ ToUnicode CMap ----
$cmapLines = New-Object System.Collections.Generic.List[string]
$cmapLines.Add("/CIDInit /ProcSet findresource begin")
$cmapLines.Add("12 dict begin")
$cmapLines.Add("begincmap")
$cmapLines.Add("/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def")
$cmapLines.Add("/CMapName /Adobe-Identity-UCS def")
$cmapLines.Add("/CMapType 2 def")
$cmapLines.Add("1 begincodespacerange")
$cmapLines.Add("<0000> <FFFF>")
$cmapLines.Add("endcodespacerange")
$cmapLines.Add("$($map.Count) beginbfchar")
foreach ($ch in ($map.Keys | Sort-Object { $map[$_] })) {
    $code = $map[$ch]
    $utf16 = ("{0:X4}" -f [int][char]$ch)
    $cmapLines.Add(("<{0:X4}> <{1}>" -f $code, $utf16))
}
$cmapLines.Add("endbfchar")
$cmapLines.Add("endcmap")
$cmapLines.Add("CMapName currentdict /CMap defineresource pop")
$cmapLines.Add("end")
$cmapLines.Add("end")
$cmapText = ($cmapLines -join "`n")

# --------------------------------------------------------------- 内容流 ----
$c1 = New-Object System.Text.StringBuilder
[void]$c1.Append("BT`n/F2 13 Tf`n18 TL`n1 0 0 1 60 780 Tm`n")
foreach ($line in $page1) {
    if ($line -eq "") { [void]$c1.Append("T*`n"); continue }
    [void]$c1.Append((ConvertTo-CidHex $line $map) + " Tj`nT*`n")
}
[void]$c1.Append("ET")
$content1 = $c1.ToString()

$c2 = New-Object System.Text.StringBuilder
[void]$c2.Append("BT`n/F1 12 Tf`n16 TL`n1 0 0 1 60 780 Tm`n")
foreach ($line in $page2) {
    if ($line -eq "") { [void]$c2.Append("T*`n"); continue }
    [void]$c2.Append((ConvertTo-Literal $line) + " Tj`nT*`n")
}
[void]$c2.Append("ET")
$content2 = $c2.ToString()

# ---------------------------------------------------------------- 对象表 ----
# 文档标题以 UTF-16BE 十六进制写进 /Info /Title（Word/Chrome 导出的常见形式），
# 验证解析器的中文标题还原路径；括号字面量无法直接承载中文，故用 <FEFF....>
$pdfTitle = "校医院就诊与报销指南"
$titleHex = "FEFF" + (($pdfTitle.ToCharArray() | ForEach-Object { "{0:X4}" -f [int][char]$_ }) -join "")

$objects = @{}
$objects[1] = "<< /Type /Catalog /Pages 2 0 R >>"
$objects[2] = "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>"
$objects[3] = "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] " +
              "/Resources << /Font << /F2 6 0 R >> >> /Contents 10 0 R >>"
$objects[4] = "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] " +
              "/Resources << /Font << /F1 5 0 R >> >> /Contents 11 0 R >>"
$objects[5] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
$objects[6] = "<< /Type /Font /Subtype /Type0 /BaseFont /SimSun /Encoding /Identity-H " +
              "/DescendantFonts [7 0 R] /ToUnicode 8 0 R >>"
$objects[7] = "<< /Type /Font /Subtype /CIDFontType2 /BaseFont /SimSun /DW 1000 " +
              "/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> " +
              "/FontDescriptor 9 0 R >>"
$objects[8] = "<< /Length $($cmapText.Length) >>`nstream`n$cmapText`nendstream"
$objects[9] = "<< /Type /FontDescriptor /FontName /SimSun /Flags 4 " +
              "/FontBBox [0 -200 1000 900] /ItalicAngle 0 /Ascent 800 /Descent -200 " +
              "/CapHeight 700 /StemV 80 >>"
$objects[10] = "<< /Length $($content1.Length) >>`nstream`n$content1`nendstream"
$objects[11] = "<< /Length $($content2.Length) >>`nstream`n$content2`nendstream"
$objects[12] = "<< /Title <$titleHex> /Producer (make_kb_pdf_samples.ps1) " +
               "/Creator (XJT B16 test asset) >>"

$count = 12
$sb = New-Object System.Text.StringBuilder
[void]$sb.Append("%PDF-1.4`n%XJT-Fixture`n")

$offset = @{}
for ($i = 1; $i -le $count; $i++) {
    $offset[$i] = $sb.Length
    [void]$sb.Append("$i 0 obj`n$($objects[$i])`nendobj`n")
}

$xrefPos = $sb.Length
[void]$sb.Append("xref`n0 $($count + 1)`n")
[void]$sb.Append("0000000000 65535 f `n")
for ($i = 1; $i -le $count; $i++) {
    [void]$sb.Append(("{0:D10} 00000 n `n" -f $offset[$i]))
}
[void]$sb.Append("trailer`n<< /Size $($count + 1) /Root 1 0 R /Info 12 0 R >>`n")
[void]$sb.Append("startxref`n$xrefPos`n%%EOF`n")

# 全部内容均为 ASCII（中文已转为 hex 码），故字符数 == 字节数，偏移量一致
Set-Content -Path $target -Value $sb.ToString() -Encoding ascii -NoNewline

$size = (Get-Item $target).Length
Write-Host ("已生成：{0}" -f $target)
Write-Host ("大小：{0} 字节；中文字形 {1} 个；xref 偏移 {2}" -f $size, $map.Count, $xrefPos)
Write-Host "自检："
$raw = Get-Content $target -Raw
foreach ($token in @("%PDF-1.4", "/ToUnicode", "beginbfchar", "endobj", "startxref", "%%EOF")) {
    $hit = if ($raw.Contains($token)) { "OK " } else { "缺失" }
    Write-Host ("  [{0}] {1}" -f $hit, $token)
}
