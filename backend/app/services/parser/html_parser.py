"""HTML 解析器（B16）：HTML → 纯文本（标准库 HTMLParser，零依赖）。

要点：
- **去样板**：``script/style/noscript/svg/iframe/form/button`` 直接丢弃；
  ``nav/footer/aside`` 与站点页脚固定文案（版权所有、备案号…）按行过滤，
  避免"导航噪声"污染检索结果；
- **保结构**：块级标签（p/div/li/tr/h1~h6…）转换为换行，列表项保留文本；
- **编码**：``<meta charset=gbk>`` 等声明由 ``base.decode_bytes`` 处理；
- **标题**：``<h1>`` 优先，其次 ``<title>``（去掉站点后缀），再回退文件名。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from .base import ParsedDoc, decode_bytes, finalize, guess_title, normalize_text, title_from_filename

_SKIP_TAGS = {
    "script", "style", "noscript", "svg", "canvas", "iframe", "template",
    "form", "button", "select", "option", "input", "textarea",
}
_BOILERPLATE_TAGS = {"nav", "footer", "aside", "header"}
_BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "td", "th", "table", "ul", "ol", "dl", "dt", "dd",
    "section", "article", "main", "blockquote", "pre", "figure", "figcaption",
    "h1", "h2", "h3", "h4", "h5", "h6", "hr",
}

# 页脚/导航固定文案（行内命中即丢弃）
_BOILERPLATE_LINE_RE = re.compile(
    r"(版权所有|保留所有权利|Copyright\s*©?|备案号|ICP备|京ICP|沪ICP|吉ICP|"
    r"关注我们|扫码关注|分享到|上一篇|下一篇|返回顶部|返回首页|打印本页|"
    r"字体[:：]|分享[:：]|点击次数|阅读全文\s*$|首页\s*$|联系我们\s*$|"
    r"网站地图|技术支持[:：]|All Rights Reserved)",
    re.IGNORECASE,
)


class _Extractor(HTMLParser):
    """HTML 文本抽取器：块级标签换行 + 跳过脚本样式 + 抬头信息收集。"""

    def __init__(self, strip_boilerplate: bool = True) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0
        self.boiler_depth = 0
        self.boiler_blocks = 0          # 被整体丢弃的样板块数（nav/footer/aside/header）
        self.strip_boilerplate = strip_boilerplate
        self.title = ""
        self.h1 = ""
        self.description = ""
        self.keywords = ""
        self.lang = ""
        self._in_title = False
        self._in_h1 = False
        self._heading: list[str] = []

    # ---- 标签 ----
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_d = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html" and attrs_d.get("lang"):
            self.lang = attrs_d["lang"]
        if tag == "meta":
            name = attrs_d.get("name", "").lower() or attrs_d.get("property", "").lower()
            content = attrs_d.get("content", "").strip()
            if content and name in ("description", "og:description") and not self.description:
                self.description = content
            if content and name in ("keywords", "og:keywords") and not self.keywords:
                self.keywords = content
        if tag in _SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.strip_boilerplate and tag in _BOILERPLATE_TAGS:
            if self.boiler_depth == 0:
                self.boiler_blocks += 1
            self.boiler_depth += 1
            return
        if tag == "title":
            self._in_title = True
        elif tag == "h1":
            self._in_h1 = True
            self._heading = []
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag in _BOILERPLATE_TAGS:
            self.boiler_depth = max(0, self.boiler_depth - 1)
            if tag in _BLOCK_TAGS:
                self.parts.append("\n")
            return
        if tag == "title":
            self._in_title = False
        elif tag == "h1":
            self._in_h1 = False
            if not self.h1:
                self.h1 = "".join(self._heading).strip()
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth or self.boiler_depth:
            return
        if self._in_title:
            # <title> 是 head 元信息，只用于取标题，不进正文
            if not self.title:
                self.title = data.strip()
            return
        if self._in_h1:
            self._heading.append(data)
        text = data.strip()
        if text:
            self.parts.append(text + " ")

    # ---- 输出 ----
    def text(self) -> str:
        """拼接为原始文本（含空行，交 normalize_text 收尾）。"""
        return "".join(self.parts)


def _clean_site_title(title: str) -> str:
    """去掉 ``<title>`` 里的站点后缀：`吉林大学教务处 - 通知公告` / `通知_吉林大学`。"""
    title = re.sub(r"\s+", " ", (title or "")).strip()
    if not title:
        return ""
    for sep in ("_", "|", " - ", "—", "－", ">>"):
        if sep in title:
            parts = [p.strip() for p in title.split(sep) if p.strip()]
            if len(parts) >= 2:
                # 取"最短且不像站名"的一段作为标题
                site_like = re.compile(r"(大学|学院|学校|教务处|官网|首页|门户|中心|研究生院)")
                cands = [p for p in parts if not site_like.search(p)]
                if cands:
                    return max(cands, key=len) if len(cands) == 1 else cands[0]
                return parts[0]
    return title


def strip_boilerplate_lines(text: str) -> tuple[str, int]:
    """按行过滤页脚/导航固定文案，返回 (文本, 丢弃行数)。"""
    kept: list[str] = []
    dropped = 0
    for line in text.split("\n"):
        s = line.strip()
        if s and len(s) <= 60 and _BOILERPLATE_LINE_RE.search(s):
            dropped += 1
            continue
        kept.append(line)
    return "\n".join(kept), dropped


def to_text(html: str, *, strip_boilerplate: bool = True) -> tuple[str, dict, list[str]]:
    """HTML 转纯文本，返回 (文本, meta, warnings)。"""
    warnings: list[str] = []
    parser = _Extractor(strip_boilerplate=strip_boilerplate)
    try:
        parser.feed(html or "")
        parser.close()
    except Exception as exc:  # noqa: BLE001 - 容错：HTMLParser 对畸形标签较宽容，仍兜底
        warnings.append(f"HTML 解析异常（已尽力抽取）：{exc}")
    text = parser.text()
    meta = {
        "engine": "html.parser(stdlib)",
        "html_h1": parser.h1,
        "html_title": _clean_site_title(parser.title),
        "lang": parser.lang,
    }
    if parser.description:
        meta["description"] = parser.description
    if parser.keywords:
        meta["keywords"] = parser.keywords
    if strip_boilerplate:
        text, dropped = strip_boilerplate_lines(text)
        meta["boilerplate_lines_dropped"] = dropped
        meta["boilerplate_tags_dropped"] = parser.boiler_blocks
    return text, meta, warnings


def parse(html: str, name: str = "", *, strip_boilerplate: bool = True) -> ParsedDoc:
    """解析 HTML 文本（已解码）。

    标题优先级：``<h1>`` > 站点后缀清理后的 ``<title>`` > 正文首行 > 文件名。
    （页面 h1 才是"这篇文档叫什么"，title 常被站点名污染）
    """
    body, meta, warnings = to_text(html, strip_boilerplate=strip_boilerplate)
    title = meta.get("html_h1") or meta.get("html_title") or ""
    if not title:
        title = guess_title(body, title_from_filename(name))
    meta["source_name"] = name or ""
    return finalize(
        body,
        title=title,
        fmt="html",
        fallback_title=title_from_filename(name),
        meta=meta,
        warnings=warnings,
        keep_blank=True,
    )


def parse_bytes(data: bytes, name: str = "", *, strip_boilerplate: bool = True) -> ParsedDoc:
    """解析 HTML 字节串（自动探测编码）。"""
    text, encoding, warns = decode_bytes(data)
    doc = parse(text, name, strip_boilerplate=strip_boilerplate)
    doc.meta["encoding"] = encoding
    doc.meta["bytes"] = len(data)
    if not strip_boilerplate:
        doc.warnings.append("已关闭样板行过滤（strip_boilerplate=False）")
    doc.warnings.extend(warns)
    return doc


def looks_like_html(data: bytes) -> bool:
    """魔数/特征判断（B17 扫描无扩展名文件时使用）。"""
    head = data[:1024].lstrip().lower()
    return head.startswith(b"<!doctype html") or b"<html" in head or b"<body" in head[:512]


__all__ = ["parse", "parse_bytes", "to_text", "strip_boilerplate_lines", "looks_like_html", "normalize_text"]
