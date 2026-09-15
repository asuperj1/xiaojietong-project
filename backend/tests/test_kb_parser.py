"""B16 文档解析器单元测试（纯解析，不依赖数据库与外部服务）。

覆盖：Markdown / HTML / PDF（含 FlateDecode 压缩流、ToUnicode 中文映射、
换行重排、页脚剔除）/ 纯文本编码探测 / 类型探测与体积护栏。
"""

from __future__ import annotations

import zlib

import pytest

from app.services.parser import ParseError, detect_kind, parse_bytes, supported_formats
from app.services.parser import html_parser, markdown_parser, pdf_parser, text_parser
from app.services.parser.base import decode_bytes, normalize_text


# ------------------------------------------------------------ PDF 构造器 ----

def _minimal_pdf(
    content: bytes,
    *,
    tounicode: str | None = None,
    compress: bool = True,
    info_title: str | None = None,
    info_title_utf16: str | None = None,
) -> bytes:
    """构造一个结构合法的最小 PDF（1 页），用于验证解析器各条路径。"""
    objs: dict[int, bytes] = {}
    objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[2] = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
    if tounicode is not None:
        objs[5] = (
            b"<< /Type /Font /Subtype /Type0 /BaseFont /SimSun /Encoding /Identity-H "
            b"/DescendantFonts [6 0 R] /ToUnicode 7 0 R >>"
        )
        objs[6] = (
            b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /SimSun /DW 1000 "
            b"/FontDescriptor 8 0 R >>"
        )
        raw = tounicode.encode("latin-1")
        objs[7] = b"<< /Length %d >>\nstream\n%s\nendstream" % (len(raw), raw)
        objs[8] = (
            b"<< /Type /FontDescriptor /FontName /SimSun /Flags 4 "
            b"/FontBBox [0 -200 1000 900] /ItalicAngle 0 /Ascent 800 /Descent -200 "
            b"/CapHeight 700 /StemV 80 >>"
        )
    else:
        objs[5] = (
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
            b"/Encoding /WinAnsiEncoding >>"
        )
    payload = zlib.compress(content) if compress else content
    flt = b" /Filter /FlateDecode" if compress else b""
    objs[3] = (
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
    )
    objs[4] = b"<< /Length %d%s >>\nstream\n%s\nendstream" % (len(payload), flt, payload)
    info_num = 0
    if info_title or info_title_utf16:
        info_num = max(objs) + 1                     # 保持对象号连续，xref 合法
        if info_title_utf16 is not None:
            hexed = "FEFF" + "".join(f"{ord(ch):04X}" for ch in info_title_utf16)
            token = b"<" + hexed.encode("ascii") + b">"
        else:
            token = b"(%s)" % (info_title or "").encode("latin-1")
        objs[info_num] = b"<< /Title " + token + b" /Producer (pytest) >>"

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += b"%d 0 obj\n" % num + objs[num] + b"\nendobj\n"
    xref_pos = len(out)
    size = max(objs) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size
    for i in range(1, size):
        out += b"%010d 00000 n \n" % offsets.get(i, 0)
    trailer = b"<< /Size %d /Root 1 0 R" % size
    if info_num:
        trailer += b" /Info %d 0 R" % info_num
    trailer += b" >>"
    out += b"trailer\n" + trailer + b"\nstartxref\n%d\n%%%%EOF\n" % xref_pos
    return bytes(out)


_CJK_CMAP = """\
/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def
/CMapName /Adobe-Identity-UCS def
/CMapType 2 def
1 begincodespacerange
<0000> <FFFF>
endcodespacerange
3 beginbfchar
<0001> <56FE>
<0002> <4E66>
<0003> <9986>
endbfchar
endcmap
CMapName currentdict /CMap defineresource pop
end
end
"""


# ---------------------------------------------------------------- Markdown ----

def test_markdown_front_matter_and_title():
    text = (
        "---\n"
        "title: 图书馆开放时间\n"
        "category: 图书馆\n"
        "---\n"
        "# 备用标题\n\n"
        "正文第一段。\n\n## 小节\n正文第二段。\n"
    )
    doc = markdown_parser.parse(text, "rules.md")
    assert doc.fmt == "markdown"
    assert doc.title == "图书馆开放时间"
    assert doc.meta["front_matter"]["category"] == "图书馆"
    assert "正文第一段。" in doc.content
    assert "##" not in doc.content and "#" not in doc.content


def test_markdown_strips_links_tables_and_emphasis():
    text = (
        "# 借阅规则\n\n"
        "详见[图书馆官网](https://lib.example.edu)说明。\n\n"
        "| 读者 | 册数 |\n|---|---|\n| 本科生 | 10 册 |\n\n"
        "**重点**：逾期每天 0.1 元，见 `README`。\n"
    )
    doc = markdown_parser.parse(text, "t.md")
    assert "详见图书馆官网说明。" in doc.content
    assert "本科生 10 册" in doc.content
    assert "**" not in doc.content and "`" not in doc.content
    assert "重点：逾期每天 0.1 元" in doc.content


# -------------------------------------------------------------------- HTML ----

def test_html_title_prefers_h1_and_drops_scripts():
    raw = (
        b"<html><head><title>\xe9\x80\x9a\xe7\x9f\xa5 - \xe5\x90\x89\xe6\x9e\x97\xe5\xa4\xa7\xe5\xad\xa6</title>"
        b"<style>body{color:red}</style><script>var a=1;</script></head>"
        b"<body><h1>\xe6\xa0\xa1\xe5\x9b\xad\xe5\x8d\xa1\xe8\xa1\xa5\xe5\x8a\x9e\xe6\xb5\x81\xe7\xa8\x8b</h1>"
        b"<p>\xe5\x85\x88\xe6\x8c\x82\xe5\xa4\xb1\xe5\x86\x8d\xe8\xa1\xa5\xe5\x8a\x9e\xe3\x80\x82</p></body></html>"
    )
    doc = html_parser.parse_bytes(raw, "card.html")
    assert doc.fmt == "html"
    assert "校园卡补办流程" in doc.title          # h1 优先于 title
    assert "var a=1" not in doc.content and "color:red" not in doc.content
    assert "先挂失再补办。" in doc.content


def test_html_title_suffix_and_boilerplate_filtered():
    html = (
        "<html><head><title>选课通知_吉林大学教务处</title></head><body>"
        "<nav>首页 | 通知公告</nav>"
        "<p>第一轮选课自愿填报。</p>"
        "<footer>版权所有 吉林大学 吉ICP备00000000号</footer>"
        "</body></html>"
    )
    doc = html_parser.parse(html, "notice.html")
    assert doc.title == "选课通知"
    assert "第一轮选课自愿填报。" in doc.content
    assert "版权所有" not in doc.content
    assert "通知公告" not in doc.content          # nav 被丢弃
    assert doc.meta["boilerplate_tags_dropped"] >= 1     # nav + footer 整块剔除
    assert "boilerplate_lines_dropped" in doc.meta


def test_html_entities_and_gbk_encoding():
    gbk = "<html><head><title>校医院</title></head><body><p>挂号费 2 元 &amp; 医保报销 80%</p></body></html>".encode("gb18030")
    doc = html_parser.parse_bytes(gbk, "hospital.html")
    assert "挂号费 2 元 & 医保报销 80%" in doc.content
    assert doc.meta["encoding"].lower().startswith("gb")


# ---------------------------------------------------------------- 纯文本 ----

def test_text_parser_title_and_encoding():
    doc = text_parser.parse_bytes("宿舍报修说明\n1. 小程序提交\n".encode("utf-8"), "fix.txt")
    assert doc.fmt == "text" and doc.title == "宿舍报修说明"
    assert "小程序提交" in doc.content
    long_first = ("这是一段很长的正文" * 10) + "\n第二行"
    doc2 = text_parser.parse(long_first, "长文档说明.txt")
    assert doc2.title == "长文档说明"          # 首行过长时回退文件名


def test_decode_bytes_fallback_chain():
    text, enc, warns = decode_bytes("中文内容".encode("gb18030"))
    assert text == "中文内容" and enc.lower().startswith("gb")
    text2, enc2, warns2 = decode_bytes(b"\xef\xbb\xbfhello")
    assert text2 == "hello" and enc2 == "utf-8-sig"


# --------------------------------------------------------------------- PDF ----

def test_pdf_builtin_extracts_latin_flate():
    body = (
        b"BT /F1 12 Tf 1 0 0 1 72 700 Tm "
        b"(Library opening hours: 07:30 to 22:30) Tj T* "
        b"(Borrow limit is 10 books for undergraduates.) Tj ET"
    )
    pdf = _minimal_pdf(body, compress=True)
    text, meta, warns = pdf_parser._extract_with_builtin(pdf)
    assert "Library opening hours: 07:30 to 22:30" in text
    assert "Borrow limit is 10 books" in text
    assert meta["engine"] == "builtin(stdlib)"
    assert meta["pages"] == 1


def test_pdf_builtin_extracts_cjk_via_tounicode():
    body = b"BT /F1 14 Tf 1 0 0 1 72 700 Tm <000100020003> Tj T* <00010002> Tj ET"
    pdf = _minimal_pdf(body, tounicode=_CJK_CMAP)
    text, meta, warns = pdf_parser._extract_with_builtin(pdf)
    assert "图书馆" in text
    assert "图书" in text
    assert meta["to_unicode_cmaps"] >= 1


def test_pdf_parse_bytes_contract_and_info_title():
    body = b"BT /F1 12 Tf 1 0 0 1 72 700 Tm (Campus map guide) Tj ET"
    doc = parse_bytes("map.pdf", _minimal_pdf(body, info_title="Campus Map Guide"))
    assert doc.fmt == "pdf"
    assert "Campus map guide" in doc.content
    assert doc.title == "Campus Map Guide"      # 优先用 PDF /Info /Title


def test_pdf_uncompressed_stream_supported():
    body = b"BT /F1 12 Tf 1 0 0 1 72 700 Tm (Plain uncompressed stream) Tj ET"
    text, _meta, _warns = pdf_parser._extract_with_builtin(_minimal_pdf(body, compress=False))
    assert "Plain uncompressed stream" in text


def test_pdf_reflow_joins_wrapped_lines_and_drops_footers():
    raw = "这是一段被硬换行\n切断的中文句子。\n第 1 页\n下一段开始\n"
    out = pdf_parser._reflow(raw)
    assert "这是一段被硬换行切断的中文句子。" in out
    assert "第 1 页" not in out


def test_pdf_reflow_keeps_headings_on_own_line():
    """小节标题不得被并进正文（否则破坏分块语义边界）。"""
    raw = "一、门诊时间\n中心校区校医院：周一至周五 08:00-11:30。\n1. 挂号\n一楼自助机或小程序预约。\n"
    lines = [ln for ln in pdf_parser._reflow(raw).split("\n") if ln.strip()]
    assert lines[0] == "一、门诊时间"
    assert lines[1].startswith("中心校区校医院")
    assert lines[2] == "1. 挂号"
    assert lines[3].startswith("一楼自助机")


def test_pdf_info_title_utf16_hex_without_bom():
    """中文 /Info /Title 以 UTF-16BE 十六进制存储（Word/Chrome 导出的常见形式）。"""
    body = b"BT /F1 12 Tf 1 0 0 1 72 700 Tm (body text goes here) Tj ET"
    pdf = _minimal_pdf(body, info_title_utf16="校医院就诊与报销指南")
    doc = parse_bytes("guide.pdf", pdf)
    assert doc.title == "校医院就诊与报销指南"
    assert "\ufeff" not in doc.title


def test_pdf_without_text_reports_scanned_hint():
    pdf = _minimal_pdf(b"BT ET")
    with pytest.raises(ParseError) as err:
        parse_bytes("scan.pdf", pdf)
    assert "扫描件" in str(err.value)


def test_pdf_invalid_header_rejected():
    with pytest.raises(ParseError):
        parse_bytes("broken.pdf", b"not a pdf at all")


# --------------------------------------------------------------- 类型探测 ----

def test_detect_kind_by_ext_and_magic():
    assert detect_kind("a.md") == "markdown"
    assert detect_kind("a.htm") == "html"
    assert detect_kind("unknown", b"%PDF-1.7\n") == "pdf"
    assert detect_kind("unknown", b"<!DOCTYPE html><html>") == "html"
    with pytest.raises(ParseError):
        detect_kind("a.docx")


def test_detect_kind_extensionless_falls_back_to_content():
    """无扩展名（客户端上传丢文件名，如 PowerShell -Form 传中文名）：按内容兜底。"""
    assert detect_kind("upload", "# 图书馆规则\n正文".encode("utf-8")) == "markdown"
    assert detect_kind("", "宿舍报修说明\n正文".encode("utf-8")) == "text"
    assert detect_kind("upload", "%PDF-1.4\n".encode()) == "pdf"
    with pytest.raises(ParseError) as err:
        detect_kind("upload", b"PK\x03\x04fake-zip-container")
    assert "ZIP" in str(err.value)
    with pytest.raises(ParseError):
        detect_kind("upload", b"\x00\x01\x02binary")


def test_content_magic_wins_over_wrong_extension():
    """强特征优先：把 PDF 内容命名成 .txt 也能被正确识别。"""
    pdf = _minimal_pdf(b"BT /F1 12 Tf 1 0 0 1 72 700 Tm (Wrong extension) Tj ET")
    doc = parse_bytes("notice.txt", pdf)
    assert doc.fmt == "pdf" and "Wrong extension" in doc.content


def test_size_and_empty_guards():
    with pytest.raises(ParseError):
        parse_bytes("empty.md", b"")
    with pytest.raises(ParseError):
        parse_bytes("big.md", b"x" * 1024, max_bytes=100)
    doc = parse_bytes("long.md", ("句子。" * 100).encode("utf-8"), max_chars=50)
    assert doc.meta["truncated"] is True and len(doc.content) <= 50


def test_normalize_text_and_supported_formats():
    assert normalize_text("a\r\n\r\n\r\n\r\nb") == "a\nb"
    assert normalize_text("　全角　空格  ") == "全角 空格"
    assert any(".pdf" in f for f in supported_formats())
