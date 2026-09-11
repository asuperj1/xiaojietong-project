#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""索引重建的覆盖式语义测试（审计 CAC-07）。

原实现 `build_index(force=True)` 会先 `store.clear()` + 清空 chunk 表 +
重置所有文档 status，再逐篇重建，造成：

    ① 从清空到重建完成（可能数分钟）期间，RAG 检索**返回空** ——
       表现为用户问校园问题，AI 答"知识库没有相关信息"；
    ② 中途 embedding 失败（Ollama 未就绪）时旧索引已删，**无法回滚**。

本测试验证修复后的覆盖式重建：
    1) 重建过程中检索**始终有结果**（无空窗）
    2) **不再调用 `store.clear()`**
    3) 单篇失败不影响其它文档的已有向量（可重入）
    4) 全量重建后清理**孤儿向量**（DB 已删除文档的残留）
    5) 仅重建指定文档时**不动**其他文档的向量

用法：
    python backend/tests/test_index_rebuild.py

使用真实 NumpyVectorStore（临时目录）+ 桩化的 DB 访问，无需 MySQL。

作者：成员3 · 审计修复
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import rag  # noqa: E402
from app.services.vector_store import NumpyVectorStore  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []
DIM = 8


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"    {detail}" if detail else ""))


def vec(seed: float) -> list[float]:
    """构造一个可控的单位向量。"""
    v = [0.0] * DIM
    v[0] = 1.0
    v[1] = seed
    n = (1.0 + seed * seed) ** 0.5
    return [x / n for x in v]


class FakeDB:
    """桩化 cpp_bridge，只识别 build_index 用到的两种查询。"""

    def __init__(self, alive_ids: list[int]) -> None:
        self.alive_ids = alive_ids
        self.queries: list[str] = []

    def query(self, sql: str, params: Any = None) -> list[dict]:
        self.queries.append(sql)
        if "knowledge_doc" in sql:
            return [{"id": i} for i in self.alive_ids]
        return []


def new_store(tmp: str) -> NumpyVectorStore:
    return NumpyVectorStore(Path(tmp), DIM)


async def run_case_no_gap() -> None:
    """重建期间检索不应中断，且不再调用 clear()。"""
    print("[1] 覆盖式重建：检索无空窗 + 不调用 clear()")
    tmp = tempfile.mkdtemp()
    try:
        store = new_store(tmp)
        q = vec(0.0)

        # 预置文档 1 的旧向量
        store.add(["1_0"], [q],
                  [{"doc_id": 1, "seq": 0, "title": "旧标题", "category": "图书馆"}],
                  ["旧内容"])
        check("预置旧向量可被检索到", len(store.search(q, 3, 0.0)) == 1)

        clear_called: list[int] = []
        orig_clear = store.clear

        def spy_clear() -> None:
            clear_called.append(1)
            orig_clear()

        store.clear = spy_clear  # type: ignore[method-assign]

        hits_during: list[int] = []

        async def fake_index_doc(doc_id: int) -> dict:
            # 关键观察点：重建「进行中」时的检索可见性
            hits_during.append(len(store.search(q, 3, 0.0)))
            store.delete_by_doc_id(doc_id)          # index_doc 的覆盖写入
            store.add([f"{doc_id}_0"], [q],
                      [{"doc_id": doc_id, "seq": 0,
                        "title": "新标题", "category": "图书馆"}],
                      ["新内容"])
            return {"doc_id": doc_id, "chunks": 1, "status": "ok"}

        db = FakeDB(alive_ids=[1])
        orig = (rag.get_vector_store, rag.index_doc, rag.cpp_bridge.query)
        try:
            rag.get_vector_store = lambda force_reload=False: store  # type: ignore[assignment]
            rag.index_doc = fake_index_doc  # type: ignore[assignment]
            rag.cpp_bridge.query = db.query  # type: ignore[assignment]

            result = await rag.build_index(force=True)
        finally:
            rag.get_vector_store, rag.index_doc, rag.cpp_bridge.query = orig  # type: ignore[assignment]

        check("重建期间检索始终有结果（无空窗）",
              bool(hits_during) and all(n > 0 for n in hits_during),
              f"重建中每次检索命中数 {hits_during}（期望均 > 0）")
        check("force 重建不再调用 store.clear()", not clear_called,
              f"clear() 被调用 {len(clear_called)} 次（期望 0）")
        check("重建后内容已更新",
              store.search(q, 3, 0.0)[0]["title"] == "新标题")
        check("单轮重建统计正确", result["total"] == 1 and result["ok"] == 1,
              f"total={result['total']} ok={result['ok']}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def run_case_failure_keeps_old() -> None:
    """单篇失败不应破坏其它文档的向量。"""
    print("\n[2] 单篇失败可重入：旧向量不受影响")
    tmp = tempfile.mkdtemp()
    try:
        store = new_store(tmp)
        q = vec(0.0)
        store.add(["1_0", "2_0"], [q, q],
                  [{"doc_id": 1, "seq": 0, "title": "文档1", "category": "c"},
                   {"doc_id": 2, "seq": 0, "title": "文档2", "category": "c"}],
                  ["内容1", "内容2"])

        async def fake_index_doc(doc_id: int) -> dict:
            if doc_id == 2:
                return {"doc_id": 2, "chunks": 1, "status": "embed_failed"}
            return {"doc_id": doc_id, "chunks": 1, "status": "ok"}

        db = FakeDB(alive_ids=[1, 2])
        orig = (rag.get_vector_store, rag.index_doc, rag.cpp_bridge.query)
        try:
            rag.get_vector_store = lambda force_reload=False: store  # type: ignore[assignment]
            rag.index_doc = fake_index_doc  # type: ignore[assignment]
            rag.cpp_bridge.query = db.query  # type: ignore[assignment]
            result = await rag.build_index(force=True)
        finally:
            rag.get_vector_store, rag.index_doc, rag.cpp_bridge.query = orig  # type: ignore[assignment]

        remaining = store.list_doc_ids() or set()
        check("失败文档的旧向量仍在（未先清空）", 2 in remaining,
              f"向量库现有 doc_id={sorted(remaining)}")
        check("失败被正确计数", result["failed"] == 1 and result["ok"] == 1,
              f"ok={result['ok']} failed={result['failed']}")


    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def run_case_orphan_cleanup() -> None:
    """全量重建后应清理 DB 中已不存在的文档向量。"""
    print("\n[3] 全量重建后清理孤儿向量")
    tmp = tempfile.mkdtemp()
    try:
        store = new_store(tmp)
        q = vec(0.0)
        store.add(["1_0", "99_0"], [q, q],
                  [{"doc_id": 1, "seq": 0, "title": "在库文档", "category": "c"},
                   {"doc_id": 99, "seq": 0, "title": "已删除文档", "category": "c"}],
                  ["内容1", "内容2"])
        check("重建前存在孤儿 doc_id=99", 99 in (store.list_doc_ids() or set()))

        async def fake_index_doc(doc_id: int) -> dict:
            return {"doc_id": doc_id, "chunks": 1, "status": "ok"}

        db = FakeDB(alive_ids=[1])          # DB 中只有 1
        orig = (rag.get_vector_store, rag.index_doc, rag.cpp_bridge.query)
        try:
            rag.get_vector_store = lambda force_reload=False: store  # type: ignore[assignment]
            rag.index_doc = fake_index_doc  # type: ignore[assignment]
            rag.cpp_bridge.query = db.query  # type: ignore[assignment]
            result = await rag.build_index(force=True)
        finally:
            rag.get_vector_store, rag.index_doc, rag.cpp_bridge.query = orig  # type: ignore[assignment]

        check("孤儿向量已被清理", result["orphans_removed"] == 1,
              f"orphans_removed={result['orphans_removed']}")
        check("清理后仅剩在库文档",
              (store.list_doc_ids() or set()) == {1},
              f"现有 doc_id={sorted(store.list_doc_ids() or set())}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def run_case_partial_keeps_others() -> None:
    """仅重建指定文档时，不应清理其它文档的向量。"""
    print("\n[4] 指定文档重建不波及他人")
    tmp = tempfile.mkdtemp()
    try:
        store = new_store(tmp)
        q = vec(0.0)
        store.add(["1_0", "2_0"], [q, q],
                  [{"doc_id": 1, "seq": 0, "title": "文档1", "category": "c"},
                   {"doc_id": 2, "seq": 0, "title": "文档2", "category": "c"}],
                  ["内容1", "内容2"])

        async def fake_index_doc(doc_id: int) -> dict:
            return {"doc_id": doc_id, "chunks": 1, "status": "ok"}

        # DB 里只有文档 1（模拟文档 2 已删），但不应触发孤儿清理
        db = FakeDB(alive_ids=[1])
        orig = (rag.get_vector_store, rag.index_doc, rag.cpp_bridge.query)
        try:
            rag.get_vector_store = lambda force_reload=False: store  # type: ignore[assignment]
            rag.index_doc = fake_index_doc  # type: ignore[assignment]
            rag.cpp_bridge.query = db.query  # type: ignore[assignment]
            result = await rag.build_index(doc_ids=[1], force=True)
        finally:
            rag.get_vector_store, rag.index_doc, rag.cpp_bridge.query = orig  # type: ignore[assignment]

        check("指定文档重建不清理孤儿", result["orphans_removed"] == 0,
              f"orphans_removed={result['orphans_removed']}（期望 0）")
        check("其它文档向量保持完好",
              (store.list_doc_ids() or set()) == {1, 2},
              f"现有 doc_id={sorted(store.list_doc_ids() or set())}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def main_async() -> None:
    await run_case_no_gap()
    await run_case_failure_keeps_old()
    await run_case_orphan_cleanup()
    await run_case_partial_keeps_others()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass
    print("# 索引覆盖式重建测试（CAC-07）\n")
    asyncio.run(main_async())
    print()
    total = len(PASSED) + len(FAILED)
    print(f"===== 结果：{len(PASSED)}/{total} 通过 =====")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
