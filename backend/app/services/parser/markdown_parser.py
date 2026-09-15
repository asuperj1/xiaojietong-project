"""Markdown 解析器（B16）：Markdown → 纯文本（保留标题与段落语义）。

不引入 `markdown` / `mistune` 等第三方库，纯标准库实现，理由：
- 知识库入库只需要**可读文本**（后续由 ``services/chunker.py`` 分块 + bge-m3 向量化），
  不需要渲染 HTML；
- 离线/生产环境零额外依赖，避免 requirements 膨胀。

保留策略：标题、列表项文本、表格单元格、代码块内容全部保留（只是去掉标记符号），
因为对 RAG 来说这些文字本身就是知识点。
"""

from __future__ import annotations

import re

from .base import ParsedDoc, finalize, guess_title, title_from_filename

# 围栏代码块 ```lang ... ``` 或 ~~~lang ... ~~~
_FENCE_RE = re.compile(r"^\s*(```+|~~~+)\s*([\w+-]*)\s*$")
# 行内代码 / 强调 / 删除线
_INLINE_CODE_RE = re.compile(r"`([^`]*)`")
_EMPHASIS_RE = re.compile(r"(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1", re.DOTALL)
# 图片 ![alt](url) → alt；链接 [text](url) → text；引用式 [text][ref]
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((?:[^)]*)\)")
_REF_LINK_RE = re.compile(r"\[([^\]]+)\]\[[^\]]*\]")
_FOOTNOTE_RE = re.compile(r"\[\^[^\]]+\]")
# 表格分隔行 |---|---|
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:\-|]+\|[\s:\-|]*$")
_HR_RE = re.compile(r"^\s*([-*_]\s*){3,}$")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")


def _strip_front_matter(text: str) -> tuple[dict, str]:
    """剥离 YAML front-matter（``---`` 包裹的 key: value 块），返回 (meta, 正文)。"""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    block = text[3:end]
    meta: dict = {}
    for line in block.split("\n"):
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip().strip("\"'")
        if key and value:
            meta[key] = value
    return meta, text[end + 4 :]


def _convert_inline(line: str) -> str:
    """去行内标记（图片/链接/强调/行内代码/脚注）。"""
    line = _IMAGE_RE.sub(r"\1", line)
    line = _LINK_RE.sub(r"\1", line)
    line = _REF_LINK_RE.sub(r"\1", line)
    line = _FOOTNOTE_RE.sub("", line)
    line = _INLINE_CODE_RE.sub(r"\1", line)
    line = _EMPHASIS_RE.sub(r"\2", line)
    line = re.sub(r"\\([\\`*_{}\[\]()#+\-.!])", r"\1", line)  # 反转义
    line = _HTML_TAG_RE.sub("", line)                          # 内嵌 HTML
    return line


def to_text(markdown: str) -> tuple[str, list[str]]:
    """Markdown 转纯文本，返回 (文本, warnings)。"""
    warnings: list[str] = []
    text = _HTML_COMMENT_RE.sub("", markdown or "")
    meta, text = _strip_front_matter(text)

    out: list[str] = []
    in_fence = False
    fence_hits = 0
    for raw in text.split("\n"):
        line = raw.rstrip()
        fence = _FENCE_RE.match(line)
        if fence:
            in_fence = not in_fence
            fence_hits += 1
            if in_fence:
                lang = fence.group(2)
                if lang and lang.lower() not in ("text", "plain", "txt"):
                    out.append(f"[代码块 {lang}]")
            continue
        if in_fence:
            out.append(line)          # 代码块内容原样保留
            continue
        if _HR_RE.match(line):
            continue
        if _TABLE_SEP_RE.match(line) and "|" in line:
            continue
        stripped = line.strip()
        if not stripped:
            out.append("")
            continue
        stripped = re.sub(r"^\s*>\s?", "", stripped)          # 引用
        stripped = re.sub(r"^\s*[-*+]\s+", "", stripped)       # 无序列表
        stripped = re.sub(r"^\s*\d+[.)]\s+", "", stripped)     # 有序列表
        stripped = re.sub(r"^\s*#{1,6}\s*", "", stripped)      # 标题
        stripped = stripped.replace("|", " ")                  # 表格竖线
        stripped = _convert_inline(stripped)
        out.append(stripped)

    if in_fence:
        warnings.append("检测到未闭合的代码围栏（```），已按普通文本处理")
    if fence_hits and fence_hits % 2:
        warnings.append("代码围栏数量为奇数，可能原文格式不完整")
    return "\n".join(out), warnings


def parse(text: str, name: str = "") -> ParsedDoc:
    """解析 Markdown 文本。

    Args:
        text: Markdown 原文。
        name: 文件名（用于标题回退与元信息）。
    """
    front, _ = _strip_front_matter(text or "")
    body, warnings = to_text(text)
    title = front.get("title") or ""
    if not title:
        # 优先取第一个一级/任意级标题
        for line in (text or "").split("\n"):
            m = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
            if m:
                title = _convert_inline(m.group(1)).strip()
                break
    if not title:
        title = guess_title(body, title_from_filename(name))
    meta = {"engine": "builtin-markdown", "source_name": name or ""}
    if front:
        meta["front_matter"] = front
    return finalize(
        body,
        title=title,
        fmt="markdown",
        fallback_title=title_from_filename(name),
        meta=meta,
        warnings=warnings,
        keep_blank=True,
    )
