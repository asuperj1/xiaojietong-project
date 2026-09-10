#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C++ jt_db 与 Python 直连的性能对比基准（C6，答辩素材）。

对比对象：
  A) C++ 数据层 `jt_db`（连接池 + 预处理语句，经 backend/app/db/cpp_bridge 调用）
  B) Python 直连（pymysql，单连接复用，不带连接池）

测试项：主键点查、分页查询。两者执行完全相同的 SQL。

用法（凭据经环境变量注入，勿写死）：
  Windows PowerShell:  $env:XJT_DB_PASSWORD='***'; python db/cpp_driver/test/bench_dao.py --n 500
  Linux/macOS:         XJT_DB_PASSWORD=*** python db/cpp_driver/test/bench_dao.py --n 500
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def bench(fn, n: int) -> tuple[float, float, float]:
    """执行 fn n 次，返回 (总秒, QPS, 平均毫秒)。"""
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    dt = time.perf_counter() - t0
    return dt, (n / dt if dt else 0.0), (dt / n * 1000 if n else 0.0)


def main() -> None:
    ap = argparse.ArgumentParser(description="jt_db(C++) vs pymysql 直连 性能基准")
    ap.add_argument("--n", type=int, default=500, help="每项测试执行次数（默认 500）")
    ap.add_argument("--table", default="knowledge_doc", help="测试表（默认 knowledge_doc）")
    args = ap.parse_args()

    host = env("XJT_DB_HOST", "127.0.0.1")
    port = int(env("XJT_DB_PORT", "3307"))
    user = env("XJT_DB_USER", "root")
    dbname = env("XJT_DB_NAME", "xiaojietong")
    password = os.environ.get("XJT_DB_PASSWORD", "")
    if not password:
        print("[错误] 未设置 XJT_DB_PASSWORD 环境变量（凭据勿写入代码/仓库）。")
        sys.exit(1)

    import pymysql  # 直连对比用

    py_conn = pymysql.connect(host=host, port=port, user=user, password=password,
                              database=dbname, charset="utf8mb4", autocommit=True)
    with py_conn.cursor() as cur:
        cur.execute(f"SELECT id FROM {args.table} ORDER BY id")
        ids = [r[0] for r in cur.fetchall()]
    if not ids:
        print(f"[错误] 表 {args.table} 无数据，请先导入种子。")
        sys.exit(1)
    print(f"# 基准测试：{args.table}（{len(ids)} 行），每项 {args.n} 次\n")

    results: dict[str, dict[str, tuple[float, float, float]]] = {}

    # ---------- B) Python 直连 ----------
    def py_point(i=0):
        with py_conn.cursor() as c:
            c.execute(f"SELECT id, title FROM {args.table} WHERE id=%s", (ids[i % len(ids)],))
            c.fetchall()

    def py_page():
        with py_conn.cursor() as c:
            c.execute(f"SELECT id, title FROM {args.table} ORDER BY id LIMIT %s OFFSET %s", (10, 10))
            c.fetchall()

    results["pymysql 直连"] = {
        "主键点查": bench(py_point, args.n),
        "分页查询": bench(py_page, args.n),
    }

    # ---------- A) C++ jt_db ----------
    try:
        from app.db import cpp_bridge as cb
    except Exception as exc:  # pragma: no cover
        print(f"[警告] 无法导入 cpp_bridge：{exc}")
        cb = None
    if cb is not None and cb.available():
        cb.init_db(host, port, user, password, dbname, 2, 16)

        def cpp_point(i=0):
            cb.query(f"SELECT id, title FROM {args.table} WHERE id = ?", [ids[i % len(ids)]])

        def cpp_page():
            cb.query(f"SELECT id, title FROM {args.table} ORDER BY id LIMIT ? OFFSET ?", [10, 10])

        results["C++ jt_db(连接池)"] = {
            "主键点查": bench(cpp_point, args.n),
            "分页查询": bench(cpp_page, args.n),
        }
        print("连接池状态：", cb.pool_stats())
    else:
        print("[警告] jt_db 扩展不可用，仅输出 pymysql 数据。")

    # ---------- 输出 ----------
    print("\n| 测试项 | 驱动 | 总耗时(s) | QPS | 平均耗时(ms) |")
    print("|---|---|---|---|---|")
    for drv, items in results.items():
        for name, (total, qps, avg) in items.items():
            print(f"| {name} | {drv} | {total:.3f} | {qps:.0f} | {avg:.3f} |")

    if "C++ jt_db(连接池)" in results:
        print("\n**提升倍数（QPS：C++ / pymysql）**")
        for name in results["pymysql 直连"]:
            py_qps = results["pymysql 直连"][name][1]
            cpp_qps = results["C++ jt_db(连接池)"][name][1]
            ratio = cpp_qps / py_qps if py_qps else 0
            print(f"- {name}: {ratio:.2f}x")

    py_conn.close()


if __name__ == "__main__":
    main()
