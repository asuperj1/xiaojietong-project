"""C15 切片策略可插拔 —— 单元测试（**纯离线**，不需 DB / C++ 扩展 / Ollama）。

覆盖三件事：
1. **不回归**：`backend/tests/fixtures/chunker_golden.json` 由 **C15 之前的实现**生成
   （40 组「文本 × chunk_size × overlap」），断言新实现逐字节一致。
   —— 这是「现有行为不回归」的唯一硬证据，不能靠"看着像"。
2. **新策略契约**：`semantic` 的核心承诺是「不把段落从中间切开（除非该段本身超长）」。
3. **可插拔**：注册表能注册/解析/回退，自定义策略能被 `chunk_text` 真正用上。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from app.services.chunker import (
    ChunkStrategy,
    DEFAULT_STRATEGY,
    FixedLengthStrategy,
    SemanticBoundaryStrategy,
    available_strategies,
    chunk_hash,
    chunk_text,
    get_strategy,
    register_strategy,
    resolve_strategy,
    split_paragraphs,
    split_sentences,
    summarize,
    unregister_strategy,
)

FIXTURE = Path(__file__).parent / "fixtures" / "chunker_golden.json"

# 与生成 fixture 时使用的输入完全一致（fixture 里只存了输出，键为
# "<名字>|size=<n>|overlap=<n>"，输入文本在此处保留以便自解释）
CASES: dict[str, str] = {
    "single_short": "图书馆开放时间为每天 8:00-22:00。",
    "multi_sentence": "图书馆开放时间为每天 8:00-22:00。周末照常开放。法定节假日另行通知。请以官网公告为准。",
    "paragraphs": "第一段：关于一卡通补办。\n\n第二段：关于宿舍报修流程。\n\n第三段：关于奖学金申请条件。",
    "newlines_no_blank": "第一行内容。\n第二行内容。\n第三行内容。",
    "long_single_sentence": "甲" * 1500,
    "mixed_en_cn": "GPA 计算方式如下。CET6 成绩不纳入。详见教务处通知。",
    "empty": "",
    "only_ws": "   \n \t  ",
    "punctuation_only": "。！？；…",
    "semicolon": "选项一；选项二；选项三；选项四；选项五；选项六；",
}

PARAMS: list[dict[str, int]] = [
    {"chunk_size": 600, "overlap": 100},
    {"chunk_size": 30, "overlap": 5},
    {"chunk_size": 50, "overlap": 50},  # 触发 overlap >= chunk_size 的兜底分支
    {"chunk_size": 10, "overlap": 0},
]


@pytest.fixture(scope="module")
def golden() -> dict[str, list[str]]:
    assert FIXTURE.is_file(), f"缺少 golden 基线：{FIXTURE}"
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _key(name: str, chunk_size: int, overlap: int) -> str:
    return f"{name}|size={chunk_size}|overlap={overlap}"


ALL_KEYS = [
    _key(name, p["chunk_size"], p["overlap"]) for p in PARAMS for name in CASES
]


# ============================================================ 1) 不回归 ——


def test_golden_fixture_covers_all_cases(golden: dict[str, list[str]]) -> None:
    """fixture 与用例表必须完全对齐，否则「不回归」会漏测。"""
    assert set(golden) == set(ALL_KEYS), "fixture 与 CASES/PARAMS 不一致"


@pytest.mark.parametrize("key", ALL_KEYS)
def test_default_strategy_matches_golden(key: str, golden: dict[str, list[str]]) -> None:
    """核心断言：默认策略（不传 strategy）输出与 C15 之前的实现逐字节一致。"""
    name, size, overlap = _parse(key)
    got = chunk_text(CASES[name], size, overlap)
    assert got == golden[key], f"{key}\n期望 {golden[key]!r}\n实际 {got!r}"


@pytest.mark.parametrize("key", ALL_KEYS)
def test_explicit_fixed_matches_golden(key: str, golden: dict[str, list[str]]) -> None:
    """显式传 strategy="fixed" 也必须与默认一致（避免两条路径漂移）。"""
    name, size, overlap = _parse(key)
    assert chunk_text(CASES[name], size, overlap, strategy="fixed") == golden[key]


@pytest.mark.parametrize("key", ALL_KEYS)
def test_strategy_instance_matches_golden(key: str, golden: dict[str, list[str]]) -> None:
    """传策略**实例**（而非名字）也要一致。"""
    name, size, overlap = _parse(key)
    got = chunk_text(
        CASES[name], size, overlap, strategy=FixedLengthStrategy()
    )
    assert got == golden[key]


def _parse(key: str) -> tuple[str, int, int]:
    name, rest = key.split("|", 1)
    size = int(rest.split("|")[0].split("=")[1])
    overlap = int(rest.split("|")[1].split("=")[1])
    return name, size, overlap


@pytest.mark.parametrize("chunk_size", [600, 30, 50, 10])
def test_default_strategy_is_fixed(chunk_size: int) -> None:
    """默认策略必须是 fixed（否则上面的 golden 断言会变成空跑）。"""
    assert DEFAULT_STRATEGY == "fixed"
    assert resolve_strategy(None).name == "fixed"
    assert get_strategy(None).name == "fixed"


# ============================================ 2) semantic 语义边界契约 ——


def test_semantic_keeps_paragraph_whole() -> None:
    """核心契约：段落长度 <= chunk_size 时，必须整段出现在**同一个**块里。"""
    text = "\n\n".join(
        [
            "第一段：关于一卡通补办的流程与所需材料说明。",
            "第二段：关于宿舍报修的报修入口与处理时限。",
            "第三段：关于奖学金申请的资格条件与截止日期。",
        ]
    )
    chunks = chunk_text(text, chunk_size=600, overlap=0, strategy="semantic")

    for para in split_paragraphs(text):
        holders = [c for c in chunks if para in c]
        assert len(holders) == 1, f"段落被切开或重复：{para!r} 命中 {len(holders)} 块"


def test_semantic_does_not_split_paragraph_at_boundary() -> None:
    """段落边界不能被切开：每个块都必须是若干**完整段落**的拼接。"""
    paras = [
        "甲" * 20 + "。",
        "乙" * 20 + "。",
        "丙" * 20 + "。",
        "丁" * 20 + "。",
    ]
    text = "\n\n".join(paras)
    chunks = chunk_text(text, chunk_size=50, overlap=0, strategy="semantic")
    # 每块的每一行都必须是一个完整段落（不允许出现半个段落的碎片）
    for c in chunks:
        for line in c.split("\n"):
            assert line in paras, f"出现半个段落：{line!r}"


def test_semantic_keeps_content() -> None:
    """不应丢内容：每个段落都出现在某个块中（两种策略都应满足）。"""
    text = "\n\n".join(f"第{i}段：内容说明文字。" for i in range(1, 8))
    for strategy in ("fixed", "semantic"):
        chunks = chunk_text(text, chunk_size=40, overlap=5, strategy=strategy)
        joined = "\n".join(chunks)
        for para in split_paragraphs(text):
            assert para in joined, f"{strategy} 丢了段落：{para!r}"


def test_semantic_handles_oversized_paragraph() -> None:
    """超长段落是唯一允许被切开的情况，且必须被切开而不是整个丢进一个块。"""
    long_para = "丙" * 900 + "。"  # 单段远超 chunk_size
    chunks = chunk_text(long_para, chunk_size=100, overlap=10, strategy="semantic")
    assert len(chunks) > 1, "超长段落应被切成多块"
    assert all(len(c) <= 100 + 1 + 10 for c in chunks), "块长不应远超 chunk_size"


def test_semantic_differs_from_fixed_on_paragraph_text() -> None:
    """反向对照：在「多短段落」输入上，两种策略**应当**给出不同结果。

    若两者永远相同，说明策略没有真正生效（本用例就是防这种情况）。
    """
    text = "\n\n".join(f"第{i}段：这是一段说明文字。" for i in range(1, 10))
    fixed = chunk_text(text, chunk_size=60, overlap=0, strategy="fixed")
    semantic = chunk_text(text, chunk_size=60, overlap=0, strategy="semantic")
    assert fixed != semantic


@pytest.mark.parametrize("strategy", ["fixed", "semantic"])
def test_empty_input_returns_empty(strategy: str) -> None:
    assert chunk_text("", 600, 100, strategy=strategy) == []
    assert chunk_text("   \n \t ", 600, 100, strategy=strategy) == []


@pytest.mark.parametrize("strategy", ["fixed", "semantic"])
def test_zero_or_negative_chunk_size_raises(strategy: str) -> None:
    """加固回归：C15 之前 chunk_size<=0 会在 `while len(sent) > chunk_size` 里**死循环**。"""
    for bad in (0, -1):
        with pytest.raises(ValueError):
            chunk_text("有内容。", bad, 0, strategy=strategy)


# ================= 2b) C15 评审整改：参数契约 + 重叠语义差异 ——
#
# 评审 P3-1：`semantic` 在段落边界不重叠（`overlap` 只在段内退回 fixed 时生效），
#           原 docstring 没写 → 现补文档 + 用**正反两条**用例锁定行为。
# 评审 P3-2：`chunk_size<=0` 抛错、`overlap>=chunk_size` 静默修正 —— 同类参数两种处理
#           → 现统一到 `_normalize_params()`，并新增**告警**（不再静默）。


@pytest.mark.parametrize("strategy", ["fixed", "semantic"])
def test_overlap_not_less_than_chunk_size_is_auto_corrected(strategy: str) -> None:
    """`overlap >= chunk_size` 是**自动修正**为 `chunk_size // 5`：不抛错、也不原样使用。

    两种策略必须给出**完全相同**的修正口径（评审 P3-2 的「统一」要求）。
    """
    text = "甲。乙。丙。丁。" * 20  # 180 字符 → 必然多于一块
    auto = chunk_text(text, 100, 200, strategy=strategy)      # 200 >= 100 → 修正为 20
    explicit = chunk_text(text, 100, 20, strategy=strategy)   # 100 // 5 == 20
    assert auto == explicit
    # 反向对照：换一个**合法**的 overlap，结果必须不同 ——
    # 否则说明"相等"只是因为参数根本没生效（恒真空断言族）
    assert explicit != chunk_text(text, 100, 60, strategy=strategy)


@pytest.mark.parametrize("strategy", ["fixed", "semantic"])
def test_overlap_auto_correction_logs_warning(strategy: str, caplog) -> None:
    """修正**必须留日志**：否则就是「静默降级」（与「未知策略回退 + 告警」同一原则）。"""
    with caplog.at_level(logging.WARNING, logger="app.services.chunker"):
        chunk_text("甲。乙。丙。丁。" * 20, 100, 200, strategy=strategy)
    assert any("自动修正" in r.message for r in caplog.records)


def _two_paragraphs() -> tuple[str, str]:
    p1 = "第一段开头。" + "甲" * 30 + "第一段结尾标记Z"
    p2 = "第二段开头。" + "乙" * 30 + "第二段结尾标记W"
    return p1, p2


def test_semantic_does_not_overlap_across_paragraph_boundary() -> None:
    """评审 P3-1：`semantic` 在**段落边界不重叠**（有意设计，非缺陷）。

    段落已是完整语义单元；再叠上一段尾部只会让同一句话在两个块里各出现一次
    → 向量空间浪费 + 检索结果重复。
    """
    p1, p2 = _two_paragraphs()
    chunk_size = len(p1) + 5  # 容得下单段、容不下两段
    chunks = chunk_text(f"{p1}\n\n{p2}", chunk_size=chunk_size, overlap=20,
                        strategy="semantic")
    assert chunks == [p1, p2]                       # 整段进出
    assert not chunks[1].startswith(chunks[0][-20:])  # 块间零重叠


def test_fixed_does_overlap_at_chunk_boundary() -> None:
    """**反向对照**：同一输入、同一参数下 `fixed` **确实有**块间重叠。

    没有这条对照，上面「semantic 不重叠」可能只是参数压根没触发重叠 —— 那种断言是空的。
    """
    p1, p2 = _two_paragraphs()
    chunk_size = len(p1) + 5
    chunks = chunk_text(f"{p1}\n\n{p2}", chunk_size=chunk_size, overlap=20,
                        strategy="fixed")
    assert len(chunks) >= 2
    assert chunks[1].startswith(chunks[0][-20:])  # 上一块尾部 20 字符被带进下一块


# ================================================== 3) split_paragraphs ——


def test_split_paragraphs_blank_line() -> None:
    assert split_paragraphs("甲。\n\n乙。") == ["甲。", "乙。"]


def test_split_paragraphs_single_newline_fallback() -> None:
    assert split_paragraphs("甲。\n乙。") == ["甲。", "乙。"]


def test_split_paragraphs_no_newline() -> None:
    assert split_paragraphs("甲。乙。") == ["甲。乙。"]


def test_split_paragraphs_blank_lines_with_spaces() -> None:
    assert split_paragraphs("甲。\n   \n乙。") == ["甲。", "乙。"]


def test_split_paragraphs_crlf() -> None:
    assert split_paragraphs("甲。\r\n\r\n乙。") == ["甲。", "乙。"]


def test_split_paragraphs_empty() -> None:
    assert split_paragraphs("") == []
    assert split_paragraphs("  \n\n ") == []


def test_split_sentences_unchanged() -> None:
    """`split_sentences` 是既有公开函数，行为不应变化。"""
    assert split_sentences("甲。乙！丙？") == ["甲。", "乙！", "丙？"]
    assert split_sentences("") == []


# ==================================================== 4) 注册表 / 可插拔 ——


class _UpperStrategy(ChunkStrategy):
    """测试用策略：整篇作为一个大写块。"""

    name = "test-upper"
    label = "测试用：整篇一个大写块"

    def split(self, text: str, chunk_size: int, overlap: int) -> list[str]:
        return [text.upper()] if text and text.strip() else []


@pytest.fixture
def upper_strategy():
    register_strategy(_UpperStrategy())
    try:
        yield _UpperStrategy.name
    finally:
        unregister_strategy(_UpperStrategy.name)


def test_builtin_strategies_registered() -> None:
    names = available_strategies()
    assert names[0] == DEFAULT_STRATEGY, "默认策略应排首位"
    assert "fixed" in names and "semantic" in names


def test_register_and_use_custom_strategy(upper_strategy: str) -> None:
    assert upper_strategy in available_strategies()
    assert chunk_text("abc", 600, 0, strategy=upper_strategy) == ["ABC"]
    assert get_strategy(upper_strategy).name == upper_strategy


def test_register_duplicate_raises(upper_strategy: str) -> None:
    with pytest.raises(ValueError):
        register_strategy(_UpperStrategy())


def test_register_duplicate_with_replace_ok(upper_strategy: str) -> None:
    class Other(_UpperStrategy):
        label = "替换版"

    register_strategy(Other(), replace=True)
    assert get_strategy(upper_strategy).label == "替换版"


def test_register_requires_strategy_instance() -> None:
    with pytest.raises(TypeError):
        register_strategy(_UpperStrategy)  # type: ignore[arg-type]


def test_register_requires_name() -> None:
    class NoName(ChunkStrategy):
        def split(self, text: str, chunk_size: int, overlap: int) -> list[str]:
            return []

    with pytest.raises(ValueError):
        register_strategy(NoName())


def test_unregister_default_forbidden() -> None:
    with pytest.raises(ValueError):
        unregister_strategy(DEFAULT_STRATEGY)


def test_unregister_unknown_returns_false() -> None:
    assert unregister_strategy("no-such-strategy") is False


def test_unknown_strategy_falls_back_with_warning(caplog) -> None:
    """未知策略名**不抛错**，回退默认并留 WARNING（避免静默降级）。"""
    with caplog.at_level(logging.WARNING, logger="app.services.chunker"):
        got = chunk_text("甲。乙。", 600, 100, strategy="no-such-strategy")
    assert got == chunk_text("甲。乙。", 600, 100)
    assert any("no-such-strategy" in r.message or "回退" in r.message for r in caplog.records)


def test_get_strategy_unknown_raises_keyerror() -> None:
    with pytest.raises(KeyError):
        get_strategy("no-such-strategy")


# ============================================ 5) 既有工具函数不回归 ——


def test_chunk_hash_stable() -> None:
    assert chunk_hash("abc") == chunk_hash("abc")
    assert chunk_hash("abc") != chunk_hash("abd")
    assert len(chunk_hash("中国")) == 32
    assert chunk_hash("") == chunk_hash(None)  # type: ignore[arg-type]


def test_summarize_unchanged() -> None:
    """注意：`summarize` 只把**连续空白压缩为一个空格**，并**不去除首尾空白**
    （`_WS_RE.sub` 用 " " 替换空白串，不做 strip）。此处按既有行为固定下来。"""
    assert summarize("  甲\n\n乙\t丙  ") == " 甲 乙 丙 "
    assert len(summarize("甲" * 500, limit=200)) == 200
    assert summarize("") == ""
    assert summarize(None) == ""  # type: ignore[arg-type]
