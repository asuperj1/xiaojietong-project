#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RAG 检索的缓存与请求合并测试（审计 CAC-02 / CAC-04）。

验证内容：
    1) **single-flight 合并**：同一提问的 N 个并发请求只回源一次
       （原实现下 N 个请求会各跑一次 embedding + 向量检索 + 可能的全表扫）
    2) **缓存命中**：TTL 内重复提问直接命中，不回源
    3) **空结果也缓存**：未收录的问题不反复回源
    4) **空结果 TTL 更短**：空值 15s 过期，有结果 60s 过期
    5) **不同提问互不干扰**：各自回源一次

用法：
    python backend/tests/test_rag_singleflight.py

无需后端与数据库（用桩函数替换真实回源逻辑）。

作者：成员3 · 审计修复
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import rag  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"    {detail}" if detail else ""))


class Stub:
    """可计数的回源桩。"""

    def __init__(self, delay: float = 0.15, result: list[dict] | None = None) -> None:
        self.delay = delay
        self.result = result if result is not None else [
            {"title": "图书馆规则", "category": "图书馆", "content": "8:00-22:00"}
        ]
        self.calls = 0

    async def __call__(self, question: str, top_k: int) -> list[dict]:
        self.calls += 1
        await asyncio.sleep(self.delay)  # 模拟慢回源，制造并发窗口
        return list(self.result)


def reset() -> None:
    rag._RETRIEVE_CACHE.clear()
    rag._INFLIGHT.clear()


async def main_async() -> int:
    original = rag._retrieve_uncached
    try:
        # ---------- 1) single-flight ----------
        print("[CAC-02] 同一提问的并发请求合并")
        reset()
        stub = Stub()
        rag._retrieve_uncached = stub
        q = "图书馆几点关门"
        results = await asyncio.gather(*[rag.retrieve(q) for _ in range(10)])
        check("10 个并发同问只回源 1 次", stub.calls == 1,
              f"实际回源 {stub.calls} 次（期望 1）")
        check("所有并发请求拿到一致结果",
              all(r == results[0] and len(r) == 1 for r in results),
              f"返回 {len(results)} 份，首份 {results[0][:1]}")

        # ---------- 2) 缓存命中 ----------
        print("\n[CAC-04] TTL 内重复提问命中缓存")
        before = stub.calls
        again = await rag.retrieve(q)
        check("再次提问不回源", stub.calls == before,
              f"回源次数 {before} → {stub.calls}")
        check("命中结果与首次一致", again == results[0])

        # ---------- 3) 空结果缓存（防穿透） ----------
        print("\n[CAC-02] 空结果缓存（未收录问题不反复回源）")
        reset()
        empty = Stub(result=[])
        rag._retrieve_uncached = empty
        r1 = await asyncio.gather(*[rag.retrieve("完全没收录的问题") for _ in range(8)])
        check("8 个并发未收录提问只回源 1 次", empty.calls == 1,
              f"实际回源 {empty.calls} 次（期望 1）")
        check("返回空列表而非报错", all(r == [] for r in r1))
        before = empty.calls
        await rag.retrieve("完全没收录的问题")
        check("空结果在 TTL 内被缓存", empty.calls == before,
              f"回源次数 {before} → {empty.calls}")

        # ---------- 4) 空值 TTL 更短 ----------
        print("\n空值 TTL 与有值 TTL 的差异")
        check("空结果 TTL < 有结果 TTL",
              rag._EMPTY_TTL < rag._CACHE_TTL,
              f"empty={rag._EMPTY_TTL}s, normal={rag._CACHE_TTL}s")

        key_empty = ("ttl探针_空", 3)
        rag._cache_put(key_empty, [])
        rag._RETRIEVE_CACHE[key_empty] = (
            rag._RETRIEVE_CACHE[key_empty][0] - (rag._EMPTY_TTL + 1), [])
        check("超过 _EMPTY_TTL 后空缓存失效",
              rag._cache_get(key_empty) is None)

        key_full = ("ttl探针_有值", 3)
        rag._cache_put(key_full, [{"title": "x"}])
        rag._RETRIEVE_CACHE[key_full] = (
            rag._RETRIEVE_CACHE[key_full][0] - (rag._EMPTY_TTL + 1), [{"title": "x"}])
        check("同样时长下有值缓存仍有效（TTL 更长）",
              rag._cache_get(key_full) is not None)

        # ---------- 5) 不同提问互不干扰 ----------
        print("\n不同提问独立回源")
        reset()
        multi = Stub(delay=0.05)
        rag._retrieve_uncached = multi
        await asyncio.gather(*[rag.retrieve(f"问题{i}") for i in range(5)])
        check("5 个不同提问各回源 1 次", multi.calls == 5,
              f"实际回源 {multi.calls} 次（期望 5）")

        # ---------- 6) 回源异常不遗留 inflight ----------
        print("\n回源异常后的状态清理")
        reset()

        async def boom(q: str, k: int) -> list[dict]:
            raise RuntimeError("模拟向量库故障")

        rag._retrieve_uncached = boom  # type: ignore[assignment]
        try:
            await rag.retrieve("会失败的问题")
        except RuntimeError:
            pass
        check("异常后 _INFLIGHT 已清空（不阻塞后续请求）",
              len(rag._INFLIGHT) == 0,
              f"_INFLIGHT 剩余 {len(rag._INFLIGHT)} 项")

        reset()
        ok = Stub(delay=0.02)
        rag._retrieve_uncached = ok
        await rag.retrieve("会失败的问题")
        check("同一问题后续请求可正常回源", ok.calls == 1,
              f"回源 {ok.calls} 次")
    finally:
        rag._retrieve_uncached = original
        reset()
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass
    print("# RAG 缓存与请求合并测试（CAC-02 / CAC-04）\n")
    asyncio.run(main_async())
    print()
    total = len(PASSED) + len(FAILED)
    print(f"===== 结果：{len(PASSED)}/{total} 通过 =====")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
