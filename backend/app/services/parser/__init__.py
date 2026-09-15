"""文档解析器包（B16）：HTML / PDF / Markdown（含纯文本兜底）。

对外只暴露三个入口：

.. code-block:: python

    from app.services.parser import parse_bytes, parse_file, SUPPORTED_EXT

    doc = parse_bytes("通知.pdf", data)      # bytes -> ParsedDoc
    doc = parse_file(Path("docs/规则.md"))   # 路径 -> ParsedDoc
    assert doc.fmt in {"pdf", "markdown", "html", "text"}

**可插拔注册表**：``register(kind, parser, exts)`` 可注册新格式（与二阶段
``C15 切片策略可插拔`` 同一设计思路），解析器签名统一为
``fn(text_or_bytes, name) -> ParsedDoc``。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Optional

from .base import (
    EXT_KINDS,
    MAX_CONTENT_CHARS,
    ParsedDoc,
    ParseError,
    decode_bytes,
    detect_charset,
    finalize,
    guess_title,
    normalize_text,
    title_from_filename,
)

__all__ = [
    "ParsedDoc",
    "ParseError",
    "MAX_CONTENT_CHARS",
    "SUPPORTED_EXT",
    "KIND_PARSERS",
    "parse_bytes",
    "parse_file",
    "detect_kind",
    "register",
    "supported_formats",
]

MAX_FILE_BYTES = 32 * 1024 * 1024      # 单文件上限（32MB）：挡住误传的大体积文件
_BYTES_PARSERS: dict[str, Callable[[bytes, str], ParsedDoc]] = {}


def register(kind: str, bytes_parser: Callable[[bytes, str], ParsedDoc], exts: list[str]) -> None:
    """注册/覆盖一种格式的解析器。"""
    _BYTES_PARSERS[kind] = bytes_parser
    for ext in exts:
        EXT_KINDS[ext.lower()] = kind


def _init_registry() -> None:
    """内置注册（延迟导入，避免 pdf 模块在未用到时也加载）。"""
    from . import html_parser, markdown_parser, pdf_parser, text_parser

    register("markdown", lambda data, name: markdown_parser.parse(decode_bytes(data)[0], name),
             [".md", ".markdown", ".mdown"])
    register("html", html_parser.parse_bytes, [".html", ".htm", ".xhtml"])
    register("pdf", pdf_parser.parse_bytes, [".pdf"])
    register("text", text_parser.parse_bytes, [".txt", ".text"])


_init_registry()

SUPPORTED_EXT: tuple[str, ...] = tuple(sorted(EXT_KINDS))


def KIND_PARSERS() -> dict[str, Callable[[bytes, str], ParsedDoc]]:
    """当前已注册的解析器映射（只读副本）。"""
    return dict(_BYTES_PARSERS)


def supported_formats() -> list[str]:
    """可读的格式说明（错误提示与管理端展示用）。"""
    return [f"{k}({','.join(e for e, v in EXT_KINDS.items() if v == k)})" for k in _BYTES_PARSERS]


_OFFICE_EXTS = (".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".wps", ".rtf")


def _looks_like_zip(data: bytes) -> bool:
    """ZIP 容器（Office 文档本质）：用于给出可读的拒绝原因。"""
    return data[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


def _is_plain_text(data: bytes) -> bool:
    """粗判纯文本（用于"客户端没带文件名"时的内容兜底）。"""
    sample = data[:4096]
    if b"\x00" in sample:
        return False
    ctrl = sum(1 for b in sample if b < 9 or 13 < b < 32)
    return ctrl / max(1, len(sample)) < 0.02


def detect_kind(name: str = "", data: Optional[bytes] = None) -> str:
    """判定文档类型。

    判定顺序（先内容后扩展名对可识别性更稳）：
    1. ``%PDF`` / ``<!doctype html`` 等**强特征**优先（改错扩展名也能正确处理）；
    2. 扩展名命中支持列表；
    3. 有扩展名但不支持 → **明确拒绝**并给建议（如 .docx 提示另存为 PDF）；
    4. 无扩展名（部分客户端上传不带文件名，如 PowerShell `-Form` 上传中文名文件）→
       按内容兜底：ZIP 容器拒绝、纯文本按 markdown/text 接收。
    """
    ext = Path(name or "").suffix.lower()
    if data:
        head = data[:1024].lstrip()
        low = head.lower()
        if head.startswith(b"%PDF"):
            return "pdf"
        if low.startswith(b"<!doctype html") or b"<html" in low[:512]:
            return "html"
    if ext in EXT_KINDS:
        return EXT_KINDS[ext]
    if ext:
        hint = "；Office 文档请先另存为 PDF / HTML / Markdown 再入库" if ext in _OFFICE_EXTS else ""
        raise ParseError(
            f"不支持的文件类型（{ext}）。当前支持：{', '.join(SUPPORTED_EXT)}{hint}"
        )
    if data:
        if _looks_like_zip(data):
            raise ParseError(
                "无法识别的内容格式（疑似 DOCX/XLSX/PPTX 等 ZIP 容器），"
                "请另存为 PDF / HTML / Markdown 后重试"
            )
        if _is_plain_text(data):
            text = decode_bytes(data)[0]
            if re.match(r"^\s{0,3}#{1,6}\s+\S", text) or text.startswith("---\n"):
                return "markdown"
            return "text"
    raise ParseError(
        f"不支持的文件类型（无扩展名）。当前支持：{', '.join(SUPPORTED_EXT)}"
    )


def parse_bytes(
    name: str,
    data: bytes,
    *,
    max_bytes: int = MAX_FILE_BYTES,
    max_chars: int = MAX_CONTENT_CHARS,
) -> ParsedDoc:
    """解析字节串为 :class:`ParsedDoc`。

    Args:
        name: 文件名（决定解析器与标题回退）。
        data: 原始字节。
        max_bytes: 文件体积上限，超过抛 :class:`ParseError`。
        max_chars: 正文字符上限，超出截断（记 warning）。
    """
    if not data:
        raise ParseError(f"{name or '文件'} 内容为空（0 字节）")
    if len(data) > max_bytes:
        raise ParseError(
            f"{name or '文件'} 体积 {len(data) / 1048576:.1f}MB 超过上限 "
            f"{max_bytes / 1048576:.0f}MB"
        )
    kind = detect_kind(name, data)
    parser = _BYTES_PARSERS.get(kind)
    if parser is None:
        raise ParseError(f"未注册的解析器类型：{kind}")
    doc = parser(data, name)
    if max_chars and len(doc.content) > max_chars:
        doc.content = doc.content[:max_chars]
        doc.meta["truncated"] = True
        doc.meta["max_chars"] = max_chars
        doc.warnings.append(f"正文超过 {max_chars} 字上限，已截断")
    doc.meta.setdefault("kind", kind)
    doc.meta.setdefault("file_size", len(data))
    return doc


def parse_file(path: str | Path, **kwargs) -> ParsedDoc:
    """解析本地文件。"""
    p = Path(path)
    if not p.is_file():
        raise ParseError(f"文件不存在：{p}")
    return parse_bytes(p.name, p.read_bytes(), **kwargs)
