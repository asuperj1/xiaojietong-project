#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RWLock 行为测试（审计 CAC-06）。

验证三条语义：
    1) **读并发**：多个线程可同时持有读锁（这是相对原互斥锁的核心提升，
       否则检索请求仍会被串行化）；
    2) **写独占**：写者持锁期间，任何读者/写者都无法进入；
    3) **写优先**：持续有读者进入时，等待中的写者仍能拿到锁（不饥饿）——
       纯读优先实现下，建索引会被源源不断的查询永久饿死。

用法：
    python backend/tests/test_rwlock.py

无需后端与数据库，纯内存测试。

作者：成员3 · 审计修复
"""
from __future__ import annotations

import sys
import threading
import time

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.rwlock import RWLock  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"    {detail}" if detail else ""))


def test_readers_concurrent() -> None:
    """3 个读者应能同时持锁。"""
    lock = RWLock()
    inside = threading.Semaphore(0)   # 每个读者进入后 release 一次
    can_exit = threading.Event()
    errors: list[str] = []

    def reader() -> None:
        try:
            with lock.read():
                inside.release()
                can_exit.wait(timeout=5)   # 卡住不离场，逼其他读者排队
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))

    threads = [threading.Thread(target=reader, daemon=True) for _ in range(3)]
    for t in threads:
        t.start()

    # 若读锁是互斥的，这里最多只能收到 1 个许可
    concurrent = 0
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and concurrent < 3:
        if inside.acquire(timeout=0.2):
            concurrent += 1
    can_exit.set()
    for t in threads:
        t.join(timeout=5)

    check("3 个读者可同时持锁（读并发）", concurrent == 3,
          f"同时进入 {concurrent}/3；错误={errors or '无'}")
    check("读者全部正常退出", all(not t.is_alive() for t in threads))


def test_writer_exclusive() -> None:
    """写者持锁时读者必须阻塞。"""
    lock = RWLock()
    writer_in = threading.Event()
    release_writer = threading.Event()
    reader_entered = threading.Event()

    def writer() -> None:
        with lock.write():
            writer_in.set()
            release_writer.wait(timeout=5)

    def reader() -> None:
        writer_in.wait(timeout=5)
        with lock.read():
            reader_entered.set()

    wt = threading.Thread(target=writer, daemon=True)
    rt = threading.Thread(target=reader, daemon=True)
    wt.start()
    rt.start()

    writer_in.wait(timeout=5)
    time.sleep(0.3)                      # 给读者一个抢锁的机会
    blocked = not reader_entered.is_set()
    release_writer.set()
    wt.join(timeout=5)
    rt.join(timeout=5)

    check("写者持锁时读者被阻塞", blocked,
          "读者在写者持锁期间进入成功（应为阻塞）" if not blocked else "")
    check("写者释放后读者可进入", reader_entered.is_set())


def test_writer_priority() -> None:
    """读者持续进入时，写者等待时间应有界（不被饿死）。"""
    lock = RWLock()
    stop = threading.Event()
    reader_holds: list[float] = []
    writer_waited: list[float] = []

    def reader() -> None:
        while not stop.is_set():
            with lock.read():
                t0 = time.monotonic()
                time.sleep(0.005)        # 模拟一次短检索
                reader_holds.append(time.monotonic() - t0)

    readers = [threading.Thread(target=reader, daemon=True) for _ in range(4)]
    for t in readers:
        t.start()

    time.sleep(0.2)                      # 让读者先跑起来
    t0 = time.monotonic()
    with lock.write():
        writer_waited.append(time.monotonic() - t0)
        time.sleep(0.05)                 # 模拟一次写
    stop.set()
    for t in readers:
        t.join(timeout=5)

    waited = writer_waited[0] if writer_waited else 99.0
    check("持续读流量下写者仍能获锁（写优先，无饥饿）", waited < 2.0,
          f"写者等待 {waited * 1000:.0f}ms（阈值 2000ms），期间完成 {len(reader_holds)} 次读")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass

    print("# RWLock 行为测试（CAC-06）\n")
    print("[1] 读并发")
    test_readers_concurrent()
    print("\n[2] 写独占")
    test_writer_exclusive()
    print("\n[3] 写优先")
    test_writer_priority()

    print()
    total = len(PASSED) + len(FAILED)
    print(f"===== 结果：{len(PASSED)}/{total} 通过 =====")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
