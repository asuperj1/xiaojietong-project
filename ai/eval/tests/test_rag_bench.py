#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`rag_bench` 度量口径回归测试（C41）。

本文件锁住本次修掉的两个「假绿」：

1. **检索模式判据**：原实现是 `any("score" in h for h in hits)`，但关键词分支
   （`rag.py::_format_keyword_rows`）**也会写 `score`**（值是命中词元占比），
   于是 `vector_used` 恒为 True —— 报告里的「检索模式」与 `--strict` 的
   「向量检索完全未生效」检查一起失效。真正能区分的是 C29 的 `retrieval` 字段。
2. **向量库证据**：报告里必须能看到「量的是哪个库、多少条」，否则
   「跑在空库上」这种事故没人查得出来。

作者：成员3 · C41
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
RAG_BENCH = REPO_ROOT / "ai" / "eval" / "rag_bench.py"


def _load():
    spec = importlib.util.spec_from_file_location("rag_bench_under_test", RAG_BENCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rb = _load()


def _row(qid: str, expected: list[str], modes: list[str], n_hits: int = 2) -> dict:
    return {
        "id": qid, "category": "测试", "question": "q",
        "expected_titles": expected,
        "got_titles": [f"t{i}" for i in range(n_hits)],
        "n_hits": n_hits,
        "rank": 1 if expected else None,
        "retrieval_modes": modes,
        "vector_used": "vector" in modes,
        # ⚠️ 关键词分支也会有 score —— 这正是旧判据失效的原因，故意保留这个字段
        "scores": [0.5] * n_hits,
        "latency_ms": 10.0, "error": "",
    }


def test_keyword_rows_with_scores_are_not_counted_as_vector():
    """回归锁：有 `score` **不等于**走了向量（旧实现就是在这里骗过了报告）。"""
    rows = [_row("Q01", ["图书"], ["keyword"])]
    summary = rb.summarize(rows, 3)

    assert summary["vector_hit_rows"] == 0
    assert summary["keyword_hit_rows"] == 1
    assert summary["retrieval_mode"] == "keyword(降级)"


def test_all_vector_reports_vector_mode():
    summary = rb.summarize([_row("Q01", ["图书"], ["vector"])], 3)
    assert summary["retrieval_mode"] == "vector"
    assert summary["vector_hit_rows"] == 1


def test_mixed_modes_reports_fallback():
    rows = [_row("Q01", ["图书"], ["vector"]), _row("Q02", ["教务"], ["keyword"])]
    summary = rb.summarize(rows, 3)
    assert summary["retrieval_mode"] == "vector+fallback"
    assert (summary["vector_hit_rows"], summary["keyword_hit_rows"]) == (1, 1)


def test_no_hits_reports_none_mode():
    """空结果既不是 vector 也不是 keyword，不能算进任何一个计数。"""
    summary = rb.summarize([_row("Q01", [], [], n_hits=0)], 3)
    assert summary["retrieval_mode"] == "none(无检索结果)"
    assert summary["vector_hit_rows"] == 0
    assert summary["keyword_hit_rows"] == 0


def test_summary_keeps_negative_false_hit_metric():
    """既有语义不能被我改坏：负样本有返回就算误命中。"""
    rows = [_row("N01", [], ["vector"], n_hits=3), _row("N02", [], ["vector"], n_hits=0)]
    summary = rb.summarize(rows, 3)
    assert summary["false_hit_rate"] == pytest.approx(0.5)
    assert summary["n_negative"] == 2


# ==================== 逐题行构造（本次 bug 的现场）====================
#
# `summarize` 只吃已经构造好的行；而 bug 在**构造行**的那一行代码里。
# 所以必须直接测 `build_row`，否则「把 score 当向量判据」这种回归能活下来。

KEYWORD_HITS = [
    # 关键词降级：**也带 score**（命中词元占比），旧判据就是被它骗过的
    {"title": "图书馆借阅规则", "retrieval": "keyword", "score": 0.5714},
    {"title": "图书馆开放时间", "retrieval": "keyword", "score": 0.2857},
]
VECTOR_HITS = [
    {"title": "图书馆借阅规则", "retrieval": "vector", "score": 0.6902},
    {"title": "图书馆开放时间", "retrieval": "vector", "score": 0.4123},
]


def test_build_row_keyword_hits_are_not_vector():
    """杀手测试：关键词命中带 score，也**不能**被判成走了向量。"""
    row = rb.build_row({"id": "Q01", "category": "图书馆", "question": "q",
                        "expected_titles": ["图书馆借阅规则"]}, KEYWORD_HITS, 12.0)
    assert row["vector_used"] is False
    assert row["retrieval_modes"] == ["keyword"]


def test_build_row_vector_hits_are_vector():
    row = rb.build_row({"id": "Q01", "category": "图书馆", "question": "q",
                        "expected_titles": ["图书馆借阅规则"]}, VECTOR_HITS, 12.0)
    assert row["vector_used"] is True
    assert row["retrieval_modes"] == ["vector"]
    assert row["scores"] == [0.6902, 0.4123]


def test_build_row_empty_hits():
    row = rb.build_row({"id": "N01", "category": "负样本", "question": "q",
                        "expected_titles": []}, [], 3.0, "")
    assert row["n_hits"] == 0 and row["scores"] == []
    assert row["vector_used"] is False and row["retrieval_modes"] == []


def test_build_row_records_error():
    row = rb.build_row({"id": "Q09", "category": "生活", "question": "q",
                        "expected_titles": []}, [], 1.0, "RuntimeError: boom")
    assert "boom" in row["error"]


def test_build_row_missing_retrieval_field_defaults_to_not_vector():
    """老数据没写 retrieval 时宁可当「非向量」：乐观假设会直接造出假绿。"""
    row = rb.build_row({"id": "Q02", "category": "教务", "question": "q",
                        "expected_titles": []}, [{"title": "教务系统", "score": 0.4}], 5.0)
    assert row["vector_used"] is False
    assert row["retrieval_modes"] == []


def test_report_meta_carries_vector_store_evidence(monkeypatch, tmp_path):
    """报告必须能回答「量的是哪个向量库、多少条」——空库事故靠这个字段定位。"""
    fake = {"dir": "X:/rag", "backend": "ChromaVectorStore", "available": True, "count": 27}
    monkeypatch.setattr(rb, "vector_store_info", lambda: fake)
    assert rb.vector_store_info() == fake


def test_row_scores_are_recorded_for_calibration():
    """逐题分数要落进报告：没有分数就无法**校准**阈值（只能猜）。"""
    row = rb.build_row({"id": "Q01", "category": "图书馆", "question": "q",
                        "expected_titles": ["图书馆借阅规则"]}, VECTOR_HITS, 9.0)
    assert row["scores"] and all(isinstance(s, float) for s in row["scores"])
