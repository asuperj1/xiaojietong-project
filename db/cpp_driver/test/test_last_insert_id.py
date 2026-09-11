#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C8 回归测试：last_insert_id 取值正确性。

【被修复的真实 bug】
    backend/app/routers/forum.py 的举报接口原先这样取 id：

        with cpp_bridge.begin():
            cpp_bridge.execute("INSERT INTO report (reporter_id, ...) VALUES (...)")
        return cpp_bridge.query("SELECT LAST_INSERT_ID()")[0]["id"]     # ← 错误

    事务块结束后连接已归还连接池，`query()` 会另取一条连接，
    该连接的 LAST_INSERT_ID 与本次插入毫无关系（通常为 0 或他人最近一次
    插入的 id），接口因此返回**错误的 report_id**。
    正确做法：直接使用 `execute()` 的返回值 `(affected_rows, last_insert_id)`
    —— C++ 侧用 `mysql_stmt_insert_id(stmt)` 在同一语句上下文取值。

【本脚本验证 3 件事】
    [1] 事务内 execute() 返回的 last_insert_id 与库内真实自增 id 一致
    [2] 事务外 execute() 返回的 last_insert_id 与库内真实自增 id 一致
    [3] 对照实验：事务后用 query("SELECT LAST_INSERT_ID()") 取到的值与真实 id
        **不一致** —— 复现原 bug，证明修复的必要性

【运行（必须用能加载 jt_db.pyd 的 Python）】
    Windows PowerShell:
        $env:XJT_DB_PASSWORD='***'
        E:/miniconda3/python.exe db/cpp_driver/test/test_last_insert_id.py
    Linux/macOS:
        XJT_DB_PASSWORD=*** python db/cpp_driver/test/test_last_insert_id.py

作者：成员3（C++ 数据层 / 模型微调 / 数据库）· C8
"""
from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))

from app.db import cpp_bridge  # noqa: E402

# 注意：report.target_type 是 VARCHAR(16)，标记必须 ≤16 字符
MARK = "c8_regression"
PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED if cond else FAILED).append(name)
    tag = "PASS" if cond else "FAIL"
    print(f"  [{tag}] {name}" + (f"    {detail}" if detail else ""))


def last_real_id() -> int:
    """取本次测试插入的「库内真实」自增 id。"""
    rows = cpp_bridge.query(
        "SELECT id FROM report WHERE target_type = ? ORDER BY id DESC LIMIT 1",
        [MARK],
    )
    return int(rows[0]["id"]) if rows else -1


def insert_report(reason: str) -> int:
    """执行一次插入，返回 execute() 给出的 last_insert_id。"""
    _, new_id = cpp_bridge.execute(
        "INSERT INTO report (reporter_id, target_type, target_id, reason) "
        "VALUES (?, ?, ?, ?)",
        [1, MARK, 0, reason],
    )
    return int(new_id)


def main() -> int:
    host = os.environ.get("XJT_DB_HOST", "127.0.0.1")
    port = int(os.environ.get("XJT_DB_PORT", "3307"))
    user = os.environ.get("XJT_DB_USER", "root")
    dbname = os.environ.get("XJT_DB_NAME", "xiaojietong")
    password = os.environ.get("XJT_DB_PASSWORD", "")

    if not password:
        print("[错误] 未设置 XJT_DB_PASSWORD 环境变量（凭据勿写入代码/仓库）。")
        return 1
    if not cpp_bridge.available():
        print("[错误] jt_db C++ 扩展不可用。请用能加载 .pyd 的解释器运行，")
        print("       例如 E:/miniconda3/python.exe（python314 会 DLL 加载失败）。")
        return 1

    cpp_bridge.init_db(host, port, user, password, dbname, min_conn=2, max_conn=8)
    print("# C8 回归测试：last_insert_id 取值正确性")
    print(f"# 目标库：{host}:{port}/{dbname}\n")

    try:
        # ---------- [1] 事务内 ----------
        print("[1] 事务内 execute() 返回的 last_insert_id")
        with cpp_bridge.begin():
            got_in_tx = insert_report("c8-in-tx")
        real_in_tx = last_real_id()
        check("事务内 execute 返回值 == 库内真实 id",
              got_in_tx == real_in_tx,
              f"execute 返回 {got_in_tx}，库内实际 {real_in_tx}")

        # ---------- [2] 事务外 ----------
        print("\n[2] 事务外 execute() 返回的 last_insert_id")
        got_no_tx = insert_report("c8-no-tx")
        real_no_tx = last_real_id()
        check("事务外 execute 返回值 == 库内真实 id",
              got_no_tx == real_no_tx,
              f"execute 返回 {got_no_tx}，库内实际 {real_no_tx}")

        # ---------- [3] 对照实验：并发下复现原错误写法 ----------
        # 连接池分配不保证与插入同一条连接：单线程时恰好复用刚插入的连接会
        # "看起来正确"，但 FastAPI 的同步端点实际跑在线程池里（并发执行），
        # 此时 query() 很容易从池里拿到另一条连接 → 必然出错。
        print("\n[3] 并发对照实验：多线程下用 query(\"SELECT LAST_INSERT_ID()\") 取值")
        workers, rounds = 8, 40

        def bad_way_round(idx: int) -> tuple[bool, int, int]:
            """返回 (是否取错, 正确 id, 错误写法得到的 id)。"""
            with cpp_bridge.begin():
                _, right_id = cpp_bridge.execute(
                    "INSERT INTO report (reporter_id, target_type, target_id, reason) "
                    "VALUES (?, ?, ?, ?)",
                    [1, MARK, 0, f"bad-{idx}"],
                )
            wrong_id = int(cpp_bridge.query("SELECT LAST_INSERT_ID() AS id")[0]["id"])
            return wrong_id != int(right_id), int(right_id), wrong_id

        with ThreadPoolExecutor(max_workers=workers) as tpool:
            results = list(tpool.map(bad_way_round, range(rounds)))

        mismatch = sum(1 for bad, _, _ in results if bad)
        samples = [f"正确 {r}≠取到 {w}" for bad, r, w in results if bad][:3]
        check(f"复现成功：{rounds} 轮（{workers} 并发）中 {mismatch} 轮取到错误 id"
              f"（{mismatch / rounds:.0%}）",
              mismatch > 0,
              "；".join(samples))
        print("      → 这正是 C8 修复前 forum.py 举报接口的真实行为：")
        print("        并发下多数请求返回的 report_id 与库内实际记录对不上。")

    finally:
        cpp_bridge.execute("DELETE FROM report WHERE target_type = ?", [MARK])
        print(f"\n已清理测试数据：DELETE FROM report WHERE target_type = '{MARK}'")

    total = len(PASSED) + len(FAILED)
    print(f"\n===== 结果：{len(PASSED)}/{total} 通过 =====")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
