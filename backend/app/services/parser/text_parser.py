"""纯文本解析器（B16 兜底）：.txt / .text / 无扩展名文本文件。"""

from __future__ import annotations

from .base import ParsedDoc, decode_bytes, finalize, guess_title, title_from_filename


def to_text(text: str) -> str:
    """纯文本直通（归一化由 ``base.finalize`` 统一处理）。"""
    return text or ""


def parse(text: str, name: str = "") -> ParsedDoc:
    """解析纯文本（已解码）。"""
    body = to_text(text)
    first = next((ln.strip() for ln in body.split("\n") if ln.strip()), "")
    # 首行较短（≤60 字）时视为标题，否则用文件名——避免把正文首段整段当标题
    title = first[:80] if 2 <= len(first) <= 60 else title_from_filename(name)
    return finalize(
        body,
        title=title or guess_title(body, title_from_filename(name)),
        fmt="text",
        fallback_title=title_from_filename(name),
        meta={"engine": "plain", "source_name": name or ""},
        keep_blank=False,
    )


def parse_bytes(data: bytes, name: str = "") -> ParsedDoc:
    """解析纯文本字节串（自动探测编码）。"""
    text, encoding, warns = decode_bytes(data)
    doc = parse(text, name)
    doc.meta["encoding"] = encoding
    doc.meta["bytes"] = len(data)
    doc.warnings.extend(warns)
    return doc


__all__ = ["parse", "parse_bytes", "to_text"]
