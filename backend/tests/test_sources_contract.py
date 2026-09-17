"""`C29` sources 结构化契约 —— 字段齐全 + 向后兼容 + 可解释性。

背景：`B28` 定义的是「`sources` 事件契约扩展，且**向后兼容（旧字段保留）**」。
本文件把 C29 的实现**锁死**到该契约上：既断言**新字段齐全**，
也断言**旧字段一个不少**（向后兼容不能只写在描述里）。

为什么这些断言是"有牙齿"的：仅断言"新字段存在"无法发现"旧字段被删"，
仅断言"能把 sources 发出去"也无法发现 `score` 语义混用 —— 故两侧都测。

全程**离线**：`_attach_chunk_ids` 的查库用 monkeypatch 替掉，不依赖 MySQL。
"""

from __future__ import annotations

from app.services.rag import (
    _SNIPPET_LIMIT,
    _attach_chunk_ids,
    _format_hits,
    _format_keyword_rows,
    _keyword_score,
    _make_snippet,
)

# 形状与 `vector_store.search()` 的返回保持一致
_VECTOR_HIT = {
    "id": "12:3",
    "doc_id": 12,
    "seq": 3,
    "title": "图书馆借阅规则",
    "category": "图书馆",
    "content": "本科生可借 10 册，借期 30 天。",
    "score": 0.8123,
}

# `_keyword_retrieve` 里 SQL 取出的行
_KEYWORD_ROW = {
    "doc_id": 7,
    "title": "校园卡补办流程",
    "category": "办事流程",
    "content": "请携带学生证到行政楼 108 办理。",
    "source_url": "https://example.edu/card",
    "match_hits": 3,
}

REQUIRED_KEYS = {
    "title", "category", "content", "snippet", "source_url",
    "score", "doc_id", "seq", "chunk_id", "retrieval",
}
LEGACY_KEYS = {"title", "category", "content", "source_url", "score"}


# ---------------------------------------------------------------- 契约齐全

def test_vector_rows_match_c29_contract() -> None:
    row = _format_hits([_VECTOR_HIT])[0]
    assert set(row) == REQUIRED_KEYS, f"字段集与 C29 契约不一致：{sorted(row)}"


def test_keyword_rows_match_same_contract() -> None:
    """降级路径必须与向量路径**同契约**，否则前端要为两条链路写两套渲染。"""
    row = _format_keyword_rows([_KEYWORD_ROW], total_terms=5)[0]
    assert set(row) == REQUIRED_KEYS, f"字段集与 C29 契约不一致：{sorted(row)}"


def test_legacy_fields_all_preserved() -> None:
    """B28 的"向后兼容"：旧五个字段在**两条链路**上都不能少。"""
    for row in (_format_hits([_VECTOR_HIT])[0], _format_keyword_rows([_KEYWORD_ROW], 5)[0]):
        assert LEGACY_KEYS <= set(row), f"旧字段丢失：{sorted(LEGACY_KEYS - set(row))}"


# ---------------------------------------------------------------- snippet

def test_snippet_truncates_with_ellipsis() -> None:
    long_text = "甲" * (_SNIPPET_LIMIT + 50)
    snippet = _make_snippet(long_text)
    assert len(snippet) <= _SNIPPET_LIMIT + 1        # +1 是省略号本身
    assert snippet.endswith("…"), "被截断时必须补省略号，否则前端无法判断还有更多"


def test_snippet_keeps_short_text_intact() -> None:
    assert _make_snippet("图书馆 8:00 开门") == "图书馆 8:00 开门"


def test_snippet_is_not_longer_than_content() -> None:
    """卡片摘要必须不比正文更长，否则 `snippet` 就没有存在意义。"""
    long_text = "乙" * 500
    row = _format_hits([{**_VECTOR_HIT, "content": long_text}])[0]
    assert len(row["snippet"]) < len(row["content"])


# ---------------------------------------------------------------- score

def test_keyword_score_is_capped_ratio() -> None:
    assert _keyword_score(2, 4) == 0.5
    assert _keyword_score(4, 4) == 1.0
    assert _keyword_score(99, 4) == 1.0          # 封顶，不得超过 1


def test_keyword_score_tolerates_bad_input() -> None:
    """SQL 返回值可能被 jt_db 字符串化 / 为 NULL，不得因此抛异常。"""
    assert _keyword_score(None, 4) == 0.0
    assert _keyword_score("abc", 4) == 0.0
    assert _keyword_score(1, 0) == 0.0
    assert _keyword_score("2", 4) == 0.5         # jt_db 会把数字变成字符串


def test_retrieval_marks_the_scale_of_score() -> None:
    """`retrieval` 必须标出来源 —— 否则前端会把"命中词元占比"当成"向量相似度"。"""
    assert _format_hits([_VECTOR_HIT])[0]["retrieval"] == "vector"
    assert _format_keyword_rows([_KEYWORD_ROW], 5)[0]["retrieval"] == "keyword"


# ---------------------------------------------------------------- chunk_id

def test_attach_chunk_ids_fills_real_id(monkeypatch) -> None:
    rows = _format_hits([_VECTOR_HIT])
    monkeypatch.setattr(
        "app.services.rag.cpp_bridge.query",
        lambda sql, params: [{"id": 99, "doc_id": 12, "seq": 3}],
    )
    assert _attach_chunk_ids(rows)[0]["chunk_id"] == 99


def test_attach_chunk_ids_does_not_invent_id(monkeypatch) -> None:
    """库里查不到就保持 None —— **不臆造 id**（假锚点比没有锚点更糟）。"""
    rows = _format_hits([_VECTOR_HIT])
    monkeypatch.setattr("app.services.rag.cpp_bridge.query", lambda sql, params: [])
    assert _attach_chunk_ids(rows)[0]["chunk_id"] is None


def test_attach_chunk_ids_skips_db_when_nothing_to_look_up(monkeypatch) -> None:
    """关键词路径（没有 seq）不得白打一次 DB —— 降级链路本就该更省。"""
    calls: list[int] = []

    def _boom(*args, **kwargs):
        calls.append(1)
        raise AssertionError("不应查库")

    monkeypatch.setattr("app.services.rag.cpp_bridge.query", _boom)
    rows = _format_keyword_rows([_KEYWORD_ROW], 5)
    assert _attach_chunk_ids(rows) == rows
    assert calls == []


def test_keyword_rows_have_no_chunk_level_fields() -> None:
    """文档级命中的 `seq` / `chunk_id` 必须是 None，不能填 0 冒充分块。"""
    row = _format_keyword_rows([_KEYWORD_ROW], 5)[0]
    assert row["seq"] is None
    assert row["chunk_id"] is None
    assert row["doc_id"] == 7
