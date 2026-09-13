"""文本分块：把长文档切成适合向量化的片段（500~800 字符，块间重叠）。

任务 C15：**切片策略可插拔**。

- `ChunkStrategy`：抽象接口（只负责「文本 → 片段列表」，与向量化/入库解耦）。
- 内置两种策略：
  · `fixed`（**默认**）——句子聚合 + 超长句硬切 + 块间重叠。
    即 C15 之前的原实现（`_split_fixed_length`），**逐行保留以保不回归**。
  · `semantic` —— 优先按段落切，**不把段落从中间切开**（除非该段本身超长）。
- 注册表：`register_strategy()` / `get_strategy()` / `available_strategies()`，
  后续阶段（三阶段 `C34` 动态切片）可直接插入新策略。
- 默认策略名与 C15 前一致，因此**现有调用方无需改动、行为不变**；
  如需切换，`chunk_text(..., strategy="semantic")`，或配 `XJT_RAG_CHUNK_STRATEGY`。

不回归判据：`backend/tests/fixtures/chunker_golden.json` 由**旧实现**生成（40 组
「文本×尺寸×重叠」），`backend/tests/test_chunker.py` 断言新实现逐字节一致。
"""

from __future__ import annotations

import hashlib
import logging
import re
from abc import ABC, abstractmethod
from typing import ClassVar

logger = logging.getLogger(__name__)

__all__ = [
    "ChunkStrategy",
    "DEFAULT_STRATEGY",
    "FixedLengthStrategy",
    "SemanticBoundaryStrategy",
    "available_strategies",
    "chunk_hash",
    "chunk_text",
    "get_strategy",
    "register_strategy",
    "resolve_strategy",
    "split_paragraphs",
    "split_sentences",
    "summarize",
    "unregister_strategy",
]

_SENT_SPLIT_RE = re.compile(r"(?<=[。！？；…])|(?<=\n)|(?<=\r\n)")
_WS_RE = re.compile(r"\s+")
# 段落分隔：一个或多个空行（允许空行里只有空白）
_PARA_SPLIT_RE = re.compile(r"\n[ \t]*\n+")


def split_sentences(text: str) -> list[str]:
    """按中文句末标点/换行切句，返回非空句子列表。"""
    parts = _SENT_SPLIT_RE.split(text or "")
    return [p.strip() for p in parts if p and p.strip()]


def split_paragraphs(text: str) -> list[str]:
    """按段落切分（供 `semantic` 策略使用），返回非空段落列表。

    三级回退：
    1. 按**空行**切（真正的段落，Markdown/公众号导出常见）；
    2. 没切出多个段落时，退回按**单换行**切（很多 CMS 富文本只给单换行）；
    3. 仍切不出来则整篇视为一段。

    例：`"甲。\n\n乙。"` → `['甲。', '乙。']`；`"甲。\n乙。"` → `['甲。', '乙。']`；
    `"甲。乙。"` → `['甲。乙。']`。
    """
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not raw.strip():
        return []
    blocks = [b.strip() for b in _PARA_SPLIT_RE.split(raw) if b and b.strip()]
    if len(blocks) <= 1:
        blocks = [b.strip() for b in raw.split("\n") if b and b.strip()]
    if not blocks:
        blocks = [raw.strip()]
    return blocks


def _split_fixed_length(
    text: str,
    chunk_size: int = 600,
    overlap: int = 100,
) -> list[str]:
    """固定长度切片 —— **C15 之前 `chunk_text` 的原实现，逐行保留**。

    保留原样是刻意的：`backend/tests/fixtures/chunker_golden.json` 是用**旧实现**
    生成的 40 组期望输出，新实现必须逐字节一致，才能证明「现有行为不回归」。

    策略：按句子聚合并保留语义完整；逐句聚合到目标 chunk_size；
    超出目标时以 chunk_size 硬切（处理超长句）；相邻块间保留 overlap 字符重叠。

    Args:
        text: 原始文本。
        chunk_size: 目标块字符数（必须 > 0）。
        overlap: 相邻块重叠字符数（必须小于 chunk_size）。

    Returns:
        片段列表（已去除首尾空白）。
    """
    if not text or not text.strip():
        return []
    # 加固（C15 附带）：原实现下 chunk_size<=0 会在下面的 while 里**死循环**
    # （sent[:0] 恒为空串、sent[0:] 恒为原串）。放在空文本检查之后，
    # 保证「空输入恒返回 []」这一既有语义不变。
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须为正整数")
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


# ==================== C15：可插拔切片策略 ====================
#
# 设计要点：
# - 策略实现必须是**纯函数式**的（同输入同输出），否则 `chunk_hash` 去重会失效。
# - 默认策略 `fixed` 指向上面的 `_split_fixed_length`，因此不改变现有行为。
# - 未知策略名**不抛错而是回退默认 + 告警**：切片只发生在离线索引阶段，
#   「能用」优先于「严格失败」；但必须留日志，避免静默降级。


class ChunkStrategy(ABC):
    """切片策略接口：把文本切成适合向量化的片段列表。"""

    name: ClassVar[str] = ""
    label: ClassVar[str] = ""

    @abstractmethod
    def split(self, text: str, chunk_size: int, overlap: int) -> list[str]:
        """返回片段列表（已去除首尾空白）。"""
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover - 便于调试输出
        return f"<{type(self).__name__} name={self.name!r}>"


class FixedLengthStrategy(ChunkStrategy):
    """固定长度：句子聚合 + 超长句硬切 + 块间重叠。

    **默认策略，与 C15 之前的行为完全一致。**
    """

    name = "fixed"
    label = "固定长度（句子聚合 / 硬切 / 重叠）"

    def split(self, text: str, chunk_size: int, overlap: int) -> list[str]:
        return _split_fixed_length(text, chunk_size, overlap)


class SemanticBoundaryStrategy(ChunkStrategy):
    """语义边界：优先按段落切，**不把段落从中间切开**。

    与 `fixed` 的差别：
    - `fixed` 按句子贪婪聚合，**段落边界不参与决策** → 同一段落可能被拆到两个块。
    - `semantic` 先按空行切段，**整段**放入块中；只有当某段本身超过 `chunk_size`
      时，才在**该段内部**退回固定长度切法。
    - 因此「检索命中半句话」的情况减少，代价是块数可能略多、块长更不均匀。
    """

    name = "semantic"
    label = "语义边界（优先段落 / 不切断段落）"

    def split(self, text: str, chunk_size: int, overlap: int) -> list[str]:
        if not text or not text.strip():
            return []
        if chunk_size <= 0:
            raise ValueError("chunk_size 必须为正整数")
        if overlap >= chunk_size:
            overlap = chunk_size // 5

        chunks: list[str] = []
        current = ""

        def flush() -> None:
            nonlocal current
            if current.strip():
                chunks.append(current.strip())
            current = ""

        for para in split_paragraphs(text):
            if len(para) > chunk_size:
                # 超长段落：段内退回固定长度切法（此时无法再保证「整段不切开」）
                flush()
                chunks.extend(_split_fixed_length(para, chunk_size, overlap))
                continue
            if len(current) + len(para) + 1 <= chunk_size:
                current = f"{current}\n{para}" if current else para
            else:
                flush()
                current = para
        flush()
        return chunks


# 内置策略登记（顺序即 `available_strategies()` 的展示顺序）
DEFAULT_STRATEGY = FixedLengthStrategy.name

_REGISTRY: dict[str, ChunkStrategy] = {
    FixedLengthStrategy.name: FixedLengthStrategy(),
    SemanticBoundaryStrategy.name: SemanticBoundaryStrategy(),
}


def register_strategy(strategy: ChunkStrategy, *, replace: bool = False) -> ChunkStrategy:
    """注册/替换一个切片策略（供后阶段扩展，如三阶段 `C34` 动态切片）。

    Args:
        strategy: `ChunkStrategy` 实例（需有非空 `name`）。
        replace: 同名已存在时是否覆盖；False 且同名已存在则抛 ValueError。

    Returns:
        传入的策略实例（便于链式使用）。
    """
    if not isinstance(strategy, ChunkStrategy):
        raise TypeError("strategy 必须是 ChunkStrategy 实例")
    name = (strategy.name or "").strip()
    if not name:
        raise ValueError("strategy.name 不能为空")
    if name in _REGISTRY and not replace:
        raise ValueError(f"切片策略 {name!r} 已存在；如需覆盖请传 replace=True")
    _REGISTRY[name] = strategy
    logger.info("已注册切片策略：%s（%s）", name, strategy.label or name)
    return strategy


def unregister_strategy(name: str) -> bool:
    """注销一个策略（主要供测试与后阶段替换）。返回是否真的删掉了。"""
    if name == DEFAULT_STRATEGY:
        raise ValueError(f"默认策略 {DEFAULT_STRATEGY!r} 不可注销")
    return _REGISTRY.pop(name, None) is not None


def available_strategies() -> list[str]:
    """已注册的策略名列表（`DEFAULT_STRATEGY` 恒在首位）。"""
    names = [DEFAULT_STRATEGY] + [n for n in _REGISTRY if n != DEFAULT_STRATEGY]
    return [n for n in names if n in _REGISTRY]


def get_strategy(name: str | None) -> ChunkStrategy:
    """按名取策略；未知名字抛 KeyError（调用方自行决定是否回退）。"""
    if not name:
        return _REGISTRY[DEFAULT_STRATEGY]
    if name not in _REGISTRY:
        raise KeyError(
            f"未知切片策略 {name!r}；已注册：{', '.join(available_strategies())}"
        )
    return _REGISTRY[name]


def resolve_strategy(strategy: str | ChunkStrategy | None) -> ChunkStrategy:
    """把「策略名 / 策略实例 / None」统一解析为策略实例。

    与 `get_strategy` 的区别：**未知策略名不抛错，而是回退默认 + 告警**。
    """
    if isinstance(strategy, ChunkStrategy):
        return strategy
    try:
        return get_strategy(strategy)
    except KeyError as exc:
        logger.warning("切片策略解析失败，回退默认 %r：%s", DEFAULT_STRATEGY, exc)
        return _REGISTRY[DEFAULT_STRATEGY]


def chunk_text(
    text: str,
    chunk_size: int = 600,
    overlap: int = 100,
    strategy: str | ChunkStrategy | None = None,
) -> list[str]:
    """把文本切分为片段列表（默认走 `fixed`，行为与 C15 之前一致）。

    Args:
        text: 原始文本。
        chunk_size: 目标块字符数。
        overlap: 相邻块重叠字符数（必须小于 chunk_size）。
        strategy: 策略名或策略实例；None（默认）等价于 `fixed`。

    Returns:
        片段列表（已去除首尾空白）。
    """
    return resolve_strategy(strategy).split(text, chunk_size, overlap)
