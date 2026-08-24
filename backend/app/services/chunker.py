"""文本分块：把长文档切成适合向量化的片段（500~800 字符，块间重叠）。

策略：
- 优先按段落/句子切分（。！？；…\n），保留语义完整。
- 逐句聚合到目标 chunk_size；超出目标时以 chunk_size 硬切（处理超长句）。
- 相邻块间保留 overlap 字符重叠，避免跨块语义被割裂。
"""

from __future__ import annotations

import hashlib
import re

_SENT_SPLIT_RE = re.compile(r"(?<=[。！？；…])|(?<=\n)|(?<=\r\n)")
_WS_RE = re.compile(r"\s+")


def split_sentences(text: str) -> list[str]:
    """按中文句末标点/换行切句，返回非空句子列表。"""
    parts = _SENT_SPLIT_RE.split(text or "")
    return [p.strip() for p in parts if p and p.strip()]


def chunk_text(
    text: str,
    chunk_size: int = 600,
    overlap: int = 100,
) -> list[str]:
    """把文本切分为片段列表。

    Args:
        text: 原始文本。
        chunk_size: 目标块字符数。
        overlap: 相邻块重叠字符数（必须小于 chunk_size）。

    Returns:
        片段列表（已去除首尾空白）。
    """
    if not text or not text.strip():
        return []
    if overlap >= chunk_size:
        overlap = chunk_size // 5

    sentences = split_sentences(text)
    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())

    for sent in sentences:
        # 超长单句：先按 chunk_size 硬切，再继续
        while len(sent) > chunk_size:
            if current:
                flush()
                current = ""
            chunks.append(sent[:chunk_size].strip())
            sent = sent[chunk_size:]
        if not sent:
            continue
        if len(current) + len(sent) + 1 <= chunk_size:
            current = f"{current}\n{sent}" if current else sent
        else:
            flush()
            # 重叠：从上一块尾部取 overlap 字符作为下一块开头
            current = (current[-overlap:] + "\n" + sent) if current else sent
    flush()
    return chunks


def chunk_hash(content: str) -> str:
    """内容哈希（去重用，knowledge_chunk.chunk_hash）。"""
    return hashlib.md5((content or "").encode("utf-8")).hexdigest()


def summarize(text: str, limit: int = 200) -> str:
    """截取摘要（供检索片段展示，避免超长）。"""
    return _WS_RE.sub(" ", text or "")[:limit]
