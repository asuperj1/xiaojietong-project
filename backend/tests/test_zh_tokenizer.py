"""`CAC-25` 中文关键词兜底切词单元测试（**纯离线**，不需 DB / C++ 扩展 / Ollama）。

覆盖：
- `normalize()` 全角标点剥离
- `terms()` 中文 2-gram、拉丁整词、去噪、去重、上限
- 与 `rag.py` 拼装 SQL 的**占位符-参数数量一致性**（真正会导致线上报错的约束）
- 回归锁定：旧实现会把中文整句当一个词 → 必然 0 命中
"""

from __future__ import annotations

import re

import pytest

from app.services.zh_tokenizer import (
    build_like_params,
    like_condition,
    match_score_expr,
    normalize,
    terms,
)

# --------------------------------------------------------------- normalize --


@pytest.mark.parametrize(
    ("raw", "expect"),
    [
        ("  图书馆几点关门？ ", "图书馆几点关门"),
        ("GPA 怎么算？（急）", "GPA 怎么算 急"),
        ("一卡通、补办;流程", "一卡通 补办 流程"),
        ("", ""),
        ("？？？", ""),
        ("ＧＰＡ", "GPA"),  # 全角字母 → NFKC 转半角
    ],
)
def test_normalize(raw: str, expect: str) -> None:
    assert normalize(raw) == expect


# ------------------------------------------------------------------- terms --


def test_terms_chinese_bigram() -> None:
    """中文长句切 2-gram：短词元才能被 LIKE 命中。"""
    got = terms("图书馆几点关门")
    assert got == ["图书", "书馆", "馆几", "几点", "点关", "关门"]
    assert all(len(t) == 2 for t in got)


def test_terms_short_chinese_kept_whole() -> None:
    """汉字串长度 ≤ 3 时保留原串（整串比 bigram 更精准）。"""
    assert terms("怎么算") == ["怎么算"]


def test_terms_mixed_latin_and_chinese() -> None:
    """双桶：干净词元 `GPA` 非空 → 丢弃含虚词的 `怎么算`。"""
    assert terms("GPA怎么算") == ["GPA"]
    # clean 为空时才回退 noisy
    assert terms("怎么算") == ["怎么算"]


def test_terms_drops_punctuation_and_stopwords() -> None:
    """虚词 bigram（「的门」「么开」）不应进入检索条件。"""
    got = terms("图书馆的门怎么开的")
    assert got == ["图书", "书馆", "门怎"], got
    assert not any("的" in t for t in got), got
    assert not any("么" in t for t in got), got


def test_terms_drops_single_cjk_char() -> None:
    """单字汉字会 LIKE 命中全表，必须丢弃。"""
    assert terms("我") == []
    assert terms("的了") == []   # 长度 2 但**全虚词** → 零信息量


def test_terms_all_stopwords_empty() -> None:
    assert terms("的的的") == []
    assert terms("了吗呢") == []


def test_terms_empty_and_none_like() -> None:
    assert terms("") == []
    assert terms("   ") == []
    assert terms("？？！。") == []


def test_terms_dedup_and_order() -> None:
    """重复词元去重，且保持出现顺序。"""
    got = terms("GPA GPA 校历")
    assert got == ["GPA", "校历"]


def test_terms_max_terms_cap() -> None:
    long_q = "一卡通补办流程与图书馆开放时间及宿舍报修方式说明"
    got = terms(long_q, max_terms=5)
    assert len(got) == 5
    assert got == terms(long_q)[:5]


def test_terms_keeps_pure_digits() -> None:
    """混排串必须分段：`2026校历` 不能整体当一个词。"""
    assert terms("2026校历") == ["2026", "校历"]
    assert terms("3号楼在哪") == ["3", "号楼", "楼在", "在哪"]


# ------------------------------------------------------- SQL 拼装一致性 ----


def test_like_condition_matches_param_count() -> None:
    """`like_condition` 的 `?` 数量必须等于 `build_like_params` 的长度。

    这是真正会让线上报 `Parameter count mismatch` 的约束。
    """
    for q in ["图书馆几点关门", "GPA怎么算", "一卡通补办流程", "我", ""]:
        ts = terms(q)
        cond = like_condition(ts)
        assert cond.count("?") == len(build_like_params(ts)), q

def test_match_score_expr_matches_param_count() -> None:
    for q in ["图书馆几点关门", "GPA怎么算", "一卡通补办流程"]:
        ts = terms(q)
        expr = match_score_expr(ts)
        assert expr.count("?") == len(build_like_params(ts)), q


def test_full_sql_placeholder_equals_params() -> None:
    """复刻 `rag.py::_keyword_retrieve` 的 SQL 拼装，校验占位符与参数 1:1。

    注意：WHERE 与 ORDER BY 各出现一遍 LIKE，故参数需拼两遍。
    """
    question = "图书馆几点关门？"
    top_k = 5
    term_list = terms(question)
    assert term_list, "中文问句必须能切出词元"

    cond = like_condition(term_list)
    sql = (
        f"SELECT title, category, LEFT(content, 200) AS content, source_url "
        f"FROM knowledge_doc WHERE status != 2 AND ({cond}) "
        f"ORDER BY ({match_score_expr(term_list)}) DESC, updated_at DESC "
        f"LIMIT ?"
    )
    params = build_like_params(term_list) * 2 + [top_k]
    assert sql.count("?") == len(params), (
        f"占位符 {sql.count('?')} != 参数 {len(params)}"
    )


# ------------------------------------------------------------- 回归锁定 ----


_OLD_SPLIT_RE = re.compile(r"[\s,，、;；/]+")


def test_regression_old_impl_fails_on_chinese() -> None:
    """回归锁定 `CAC-25`：**旧**实现把中文整句当一个词。

    旧行为 → `LIKE '%图书馆几点关门？%'`（还带着全角问号）→ 必然 0 命中。
    新行为 → 多个短词元，且**不含任何标点**。
    """
    q = "图书馆几点关门？"

    old_terms = [t for t in _OLD_SPLIT_RE.split(q.strip()) if t][:5]
    assert old_terms == [q]                                  # 整句一个词
    assert "？" in old_terms[0]                              # 标点污染匹配串
    assert len(old_terms[0]) > 20 or len(old_terms[0]) == len(q)

    new_terms = terms(q)
    assert len(new_terms) > 1                                # 已切开
    assert not any(re.search(r"[\s\W_]", t) for t in new_terms)  # 无标点
    assert all(len(t) <= 3 for t in new_terms)               # 都是短词元
