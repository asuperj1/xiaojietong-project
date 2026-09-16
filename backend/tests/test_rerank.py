#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`C16` 检索重排模块测试。

    pytest backend/tests/test_rerank.py

作者：成员3 · C16
"""
from __future__ import annotations

import logging

import pytest

from app.core.config import settings
from app.services.rerank import (
    DEFAULT_RERANK,
    LexicalStrategy,
    NoopStrategy,
    RerankStrategy,
    available_rerankers,
    get_reranker,
    register_reranker,
    resolve_reranker,
    unregister_reranker,
)

LIB = {"title": "图书馆开放时间", "content": "图书馆开放时间为每天 8:00-22:00。"}
BORROW = {"title": "图书馆借阅规则", "content": "本科生一次最多可借 10 本书，借期 30 天。"}
UNRELATED = {"title": "体育馆预约", "content": "羽毛球场地可在小程序预约，每小时 15 元。"}


# ------------------------------------------------------------ NoopStrategy ----


def test_noop_preserves_order() -> None:
    docs = [UNRELATED, BORROW, LIB]
    assert NoopStrategy().rerank("图书馆几点关门", docs) == docs


def test_noop_does_not_copy_but_truncates() -> None:
    docs = [UNRELATED, BORROW, LIB]
    assert NoopStrategy().rerank("x", docs, top_k=2) == docs[:2]


def test_noop_score_is_always_zero() -> None:
    assert NoopStrategy().score("图书馆", LIB) == 0.0


# -------------------------------------------------------- LexicalStrategy ----


def test_lexical_promotes_title_match() -> None:
    """问题是「图书馆几点关门」，明显该把《图书馆开放时间》排第一。"""
    docs = [UNRELATED, BORROW, LIB]
    assert LexicalStrategy().rerank("图书馆几点关门？", docs)[0] is LIB


def test_lexical_fixes_a_wrongly_ordered_candidate_set() -> None:
    """反向对照：Noop 保持错序，lexical 把它纠正过来 —— 证明模块真在起作用。"""
    docs = [UNRELATED, BORROW, LIB]  # 正确项被排到了最后
    assert NoopStrategy().rerank("图书馆开放时间是什么", docs)[0] is UNRELATED
    assert LexicalStrategy().rerank("图书馆开放时间是什么", docs)[0] is LIB


def test_lexical_title_beats_body() -> None:
    """同样只命中一个词元时，命中**标题**的应该赢（TITLE_BOOST）。"""
    in_title = {"title": "校园网连接", "content": "详情见正文说明。"}
    in_body = {"title": "上网指南", "content": "校园网连接方式见下。"}
    assert LexicalStrategy().rerank("校园网连接", [in_body, in_title])[0] is in_title


def test_lexical_empty_question_keeps_order() -> None:
    docs = [UNRELATED, BORROW, LIB]
    assert LexicalStrategy().rerank("", docs) == docs


def test_lexical_empty_docs_returns_empty() -> None:
    assert LexicalStrategy().rerank("图书馆", []) == []


def test_lexical_is_deterministic_and_stable() -> None:
    """分数相同时保持原顺序：否则检索缓存（CAC-04）会返回看起来随机的结果。"""
    a = {"title": "同一标题", "content": "同样的正文内容。"}
    b = {"title": "同一标题", "content": "同样的正文内容。"}
    c = {"title": "另一篇", "content": "完全无关的内容。"}
    docs = [a, b, c]
    out = LexicalStrategy().rerank("同一标题", docs)
    assert out[0] is a and out[1] is b  # 同分 → 保持 a 在 b 前
    assert [id(d) for d in out] == [id(d) for d in LexicalStrategy().rerank("同一标题", docs)]


def test_lexical_respects_top_k() -> None:
    docs = [UNRELATED, BORROW, LIB]
    out = LexicalStrategy().rerank("图书馆几点关门", docs, top_k=2)
    assert len(out) == 2 and out[0] is LIB


def test_lexical_scores_are_non_negative() -> None:
    docs = [UNRELATED, BORROW, LIB]
    assert all(s >= 0 for s in LexicalStrategy().score_all("图书馆几点关门", docs))


def test_lexical_idf_downweights_common_terms() -> None:
    """同一个词元在候选里越常见，权重越低：`df = 全集` 时 IDF → 0。

    「图书」在全部 4 篇出现（df=4）→ IDF 极小；「研讨」只在 1 篇出现 → IDF 大。
    """
    docs = [
        {"title": "图书馆规则", "content": "图书馆借阅规则。"},
        {"title": "图书馆地图", "content": "图书馆楼层分布。"},
        {"title": "图书馆时间", "content": "图书馆开放时间。"},
        {"title": "场馆预约", "content": "研讨间可以预约，图书馆也。"},
    ]
    s_common = LexicalStrategy().score_all("图书馆", docs)
    s_rare = LexicalStrategy().score_all("研讨", docs)
    assert max(s_common) < max(s_rare), (s_common, s_rare)


def test_tokens_are_granularity_consistent_across_context() -> None:
    """`CAC-28` 回归锁：同一段文字在不同上下文里必须切出同样的词元。

    `zh_tokenizer.terms()` 做不到（≤3 字整词、>3 字切 2-gram），
    所以「按词元打分」的算法**不能**用它 —— 否则问句与正文永不相交、分数全 0。
    """
    from app.services.rerank import _tokens
    from app.services.zh_tokenizer import terms

    assert _tokens("图书馆") == ["图书", "书馆"]
    assert set(_tokens("图书馆")) <= set(_tokens("图书馆的规则"))
    # 反证：terms() 两侧交集为空 → 用它打分必然全 0
    assert not (set(terms("图书馆")) & set(terms("图书馆的规则")))


def test_lexical_single_doc_score_matches_score_all() -> None:
    docs = [UNRELATED, BORROW, LIB]
    s_all = LexicalStrategy().score_all("图书馆几点关门", docs)
    s_one = LexicalStrategy().score("图书馆几点关门", docs[2])
    # 单文档语料下 IDF 恒定，数值不等于批量；但必须**同号同序**（不产生反向结论）
    assert (s_one > 0) == (s_all[2] > 0)


# ---------------------------------------------------------------- 注册表 ----


def test_registry_has_builtin_strategies() -> None:
    assert available_rerankers() == ["lexical", "none"]
    assert get_reranker("none").name == "none"
    assert isinstance(get_reranker("lexical"), LexicalStrategy)


def test_default_strategy_is_noop() -> None:
    """默认必须是 none —— 否则「加了个可选模块」变成「悄悄改变线上检索结果」。"""
    assert DEFAULT_RERANK == "none"
    assert settings.rag_rerank == "none"


def test_resolve_none_and_empty_yield_default() -> None:
    assert isinstance(resolve_reranker(None), NoopStrategy)
    assert isinstance(resolve_reranker(""), NoopStrategy)


def test_resolve_unknown_falls_back_with_warning(caplog) -> None:
    """配置写错不能把对话打挂，但必须留 warning（否则「以为开了其实没开」）。"""
    with caplog.at_level(logging.WARNING):
        strat = resolve_reranker("cross-encoder")
    assert isinstance(strat, NoopStrategy)
    assert "未知的重排策略" in caplog.text


def test_get_unknown_raises_keyerror() -> None:
    with pytest.raises(KeyError):
        get_reranker("nope")


def test_register_duplicate_raises() -> None:
    with pytest.raises(ValueError):
        register_reranker(NoopStrategy())


def test_register_without_name_raises() -> None:
    class Nameless(RerankStrategy):
        name = ""

        def score(self, question: str, doc) -> float:
            return 0.0

    with pytest.raises(ValueError):
        register_reranker(Nameless())


def test_register_and_unregister_custom_strategy() -> None:
    class Reverse(RerankStrategy):
        name = "reverse"
        label = "倒序（测试用）"

        def score(self, question: str, doc) -> float:
            return 0.0

        def rerank(self, question: str, docs: list, top_k=None) -> list:
            out = list(reversed(docs))
            return out[:top_k] if top_k else out

    register_reranker(Reverse())
    try:
        assert "reverse" in available_rerankers()
        assert resolve_reranker("reverse").rerank("x", [1, 2, 3]) == [3, 2, 1]
    finally:
        unregister_reranker("reverse")
    assert "reverse" not in available_rerankers()


def test_cannot_unregister_default() -> None:
    with pytest.raises(ValueError):
        unregister_reranker(DEFAULT_RERANK)


def test_register_rejects_non_strategy() -> None:
    with pytest.raises(TypeError):
        register_reranker("lexical")  # type: ignore[arg-type]
