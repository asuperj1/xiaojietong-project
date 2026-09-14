# B16 / B17 验收夹具语料（kb_samples）

本目录是 **B16 文档解析器** 与 **B17 批量导入管道** 的验收夹具（fixture），
用于演示与回归测试「Markdown / HTML / PDF 三种格式都能经
`POST /admin/knowledge/ingest` 入库」以及「≥120 篇可批量导入、失败可续传」。

## 目录结构（子树名会被 CLI 自动识别为分类）

```
kb_samples/
├── 图书馆/图书馆开馆时间与借阅规则.md     Markdown（含 front-matter、表格）
├── 校历/2026-2027学年秋季学期校历.md      Markdown（含表格、有序列表）
├── 办事流程/校园卡补办流程.html            HTML（含 script/style 噪声 + 页脚样板）
├── 校医院/校医院就诊与报销指南.html        HTML（含 div/nav 结构）
├── 校医院/校医院就诊与报销指南.pdf         PDF（夹具，见下方说明）
└── 后勤/宿舍报修服务说明.txt               纯文本
```

## 用法

```powershell
cd backend

# 1) 预演：只解析不写库（不需要数据库，先看解析质量与告警）
& ".\.venv\Scripts\python.exe" -m app.cli.kb_import ..\docs\kb_samples --dry-run

# 2) 正式入库（不向量化，秒级完成；分类自动取子目录名）
& ".\.venv\Scripts\python.exe" -m app.cli.kb_import ..\docs\kb_samples --source-prefix samples/

# 3) 统一补建向量索引（可选；也可用 POST /admin/knowledge/index）
& ".\.venv\Scripts\python.exe" -m app.cli.kb_import --index-only

# 4) 回滚（按来源前缀删除）
& ".\.venv\Scripts\python.exe" -m app.cli.kb_import --purge-source-prefix samples/
```

## PDF 夹具说明（重要）

`校医院就诊与报销指南.pdf` 是由 `backend/tests/make_kb_pdf_samples.ps1`
**脚本生成**的解析器夹具：

- 它包含**真实的 PDF 结构**（对象表、xref、页面树、Type0/Identity-H 字体、
  `/ToUnicode` CMap、`Tj`/`T*` 文本算子），可用于验证「PDF 解析 -> 入库 -> 检索」全链路；
- 但**未嵌入中文字体子集**，在部分阅读器里中文可能显示为方框——这是夹具的
  刻意取舍（避免把几 MB 字体文件塞进仓库），**不影响文本抽取**；
- 生产语料请使用学校官网导出的真实 PDF（含嵌入字体，解析质量更好）。
- 重新生成：`pwsh backend/tests/make_kb_pdf_samples.ps1`
