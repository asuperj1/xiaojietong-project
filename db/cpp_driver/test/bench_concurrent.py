#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""并发基准：C++ jt_db（共享连接池） vs pymysql（每线程独立连接）（C9）。

【为什么需要这个脚本】
    C6 的单线程微基准（`bench_dao.py`）得出「C++ 层为 pymysql 的 0.47x/0.60x」，
    但那恰好避开了 C++ 层的收益场景 —— 单线程串行时，连接池的并发复用毫无意义，
    每次调用反而多付了 pybind 跨语言 + prepare/bind/execute/fetch 的固定开销。

    本脚本补上**并发**维度：N 个线程同时打同一组 SQL，观察连接池复用与
    跨语言调用的真实表现。

【用法】
    $env:XJT_DB_PASSWORD='***'
    python db/cpp_driver/test/bench_concurrent.py --n 800 --threads 8
    python db/cpp_driver/test/bench_concurrent.py --n 800 --threads 1,4,8,16 --json

选项：
    --n        每项总执行次数（默认 800）
    --threads  并发线程数，可逗号分隔多个（默认 1）
    --table    测试表（默认 knowledge_doc）
    --rows     造数行数（可选，写入临时表 bench_tmp 并改为测该表）
    --json     以 JSON 输出结果（便于汇总到报告）

作者：成员3（C++ 数据层 / 模型微调 / 数据库）· C9
"""
from __future__ import annotations

import argparse
import json as jsonlib
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def run_n(fn, k: int) -> None:
    for _ in range(k):
        fn()


def bench_parallel(fn, n: int, threads: int) -> tuple[float, float, float]:
    """多线程执行 fn 共 n 次，返回 (总秒, QPS, 平均毫秒)。"""
    if threads <= 1:
        t0 = time.perf_counter()
        run_n(fn, n)
        dt = time.perf_counter() - t0
        return dt, (n / dt if dt else 0.0), (dt / n * 1000 if n else 0.0)

    per = max(1, n // threads)
    total = per * threads
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=threads) as pool:
        for fut in [pool.submit(run_n, fn, per) for _ in range(threads)]:
            fut.result()
    dt = time.perf_counter() - t0
    return dt, (total / dt if dt else 0.0), (dt / total * 1000 if total else 0.0)


def main() -> int:
    ap = argparse.ArgumentParser(description="jt_db(C++) vs pymysql 并发基准")
    ap.add_argument("--n", type=int, default=800, help="每项总执行次数")
    ap.add_argument("--threads", default="1", help="并发线程数，逗号分隔，如 1,4,8,16")
    ap.add_argument("--table", default="knowledge_doc", help="测试表")
    ap.add_argument("--rows", type=int, default=0, help="造数行数（可选）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = ap.parse_args()

    thread_list = [int(x) for x in str(args.threads).split(",") if x.strip()]

    host = env("XJT_DB_HOST", "127.0.0.1")
    port = int(env("XJT_DB_PORT", "3307"))
    user = env("XJT_DB_USER", "root")
    dbname = env("XJT_DB_NAME", "xiaojietong")
    password = os.environ.get("XJT_DB_PASSWORD", "")
    if not password:
        print("[错误] 未设置 XJT_DB_PASSWORD 环境变量。", file=sys.stderr)
        return 1

    import pymysql

    py_conn_args = dict(host=host, port=port, user=user, password=password,
                        database=dbname, charset="utf8mb4", autocommit=True)

    # ---------- 可选：造数到临时表 ----------
    table = args.table
    if args.rows > 0:
        table = "bench_tmp"
        print(f"# 造数：向 {table} 写入 {args.rows} 行 ...")
        boot = pymysql.connect(**py_conn_args)
        with boot.cursor() as cur:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS {table} ("
                "id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,"
                "title VARCHAR(128) NOT NULL DEFAULT '',"
                "payload TEXT NULL,"
                "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                "PRIMARY KEY (id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            have = cur.fetchone()[0]
            if have < args.rows:
                batch = []
                need = args.rows - have
                for i in range(need):
                    batch.append((f"行 {have + i + 1}", "x" * 200))
                cur.executemany(
                    f"INSERT INTO {table} (title, payload) VALUES (%s, %s)", batch)
        boot.close()
        print(f"      {table} 现有行数：{args.rows}\n")

    ids: list[int] = []
    boot = pymysql.connect(**py_conn_args)
    with boot.cursor() as cur:
        cur.execute(f"SELECT id FROM {table} ORDER BY id LIMIT 2000")
        ids = [r[0] for r in cur.fetchall()]
    boot.close()
    if not ids:
        print(f"[错误] 表 {table} 无数据。", file=sys.stderr)
        return 1

    print(f"# 并发基准：{table}（{len(ids)} 行参与）｜每项 {args.n} 次｜"
          f"并发 {thread_list}\n")

    # ---------- pymysql：每线程独立连接 ----------
    tls = threading.local()

    def py_conn():
        conn = getattr(tls, "conn", None)
        if conn is None:
            conn = pymysql.connect(**py_conn_args)
            tls.conn = conn
        return conn

    def py_point(counter=[0]):
        i = counter[0]
        counter[0] += 1
        with py_conn().cursor() as c:
            c.execute(f"SELECT id, title FROM {table} WHERE id=%s", (ids[i % len(ids)],))
            c.fetchall()

    def py_page(counter=[0]):
        with py_conn().cursor() as c:
            c.execute(f"SELECT id, title FROM {table} ORDER BY id LIMIT 10 OFFSET 10")
            c.fetchall()

    def py_large(counter=[0]):
        with py_conn().cursor() as c:
            c.execute(f"SELECT id, title FROM {table} ORDER BY id LIMIT 500")
            c.fetchall()

    # ---------- C++ jt_db：共享连接池 ----------
    cb = None
    try:
        from app.db import cpp_bridge
        if cpp_bridge.available():
            cb = cpp_bridge
    except Exception as exc:  # noqa: BLE001
        print(f"[警告] 无法导入 cpp_bridge：{exc}")

    cpp_ready = False
    if cb is not None:
        cb.init_db(host, port, user, password, dbname, 2, 16)
        cpp_ready = True

    cpp_counter = [0]

    def cpp_point():
        i = cpp_counter[0]
        cpp_counter[0] += 1
        cb.query(f"SELECT id, title FROM {table} WHERE id = ?", [ids[i % len(ids)]])

    def cpp_page():
        cb.query(f"SELECT id, title FROM {table} ORDER BY id LIMIT ? OFFSET ?", [10, 10])

    def cpp_large():
        cb.query(f"SELECT id, title FROM {table} ORDER BY id LIMIT ?", [500])

    scenarios = [
        ("主键点查", py_point, cpp_point),
        ("分页查询(10行)", py_page, cpp_page),
        ("大结果集(500行)", py_large, cpp_large),
    ]

    rows_out: list[dict] = []
    for threads in thread_list:
        print(f"===== 并发 {threads} =====")
        for name, py_fn, cpp_fn in scenarios:
            py_t = bench_parallel(py_fn, args.n, threads)
            cpp_t = bench_parallel(cpp_fn, args.n, threads) if cpp_ready else None
            ratio = (cpp_t[1] / py_t[1]) if (cpp_t and py_t[1]) else 0.0
            rows_out.append({
                "threads": threads, "scenario": name,
                "py_qps": round(py_t[1]), "cpp_qps": round(cpp_t[1]) if cpp_t else None,
                "ratio": round(ratio, 2),
            })
            line = (f"  {name:<16} pymysql {py_t[1]:>9.0f} QPS  |  "
                    f"jt_db {cpp_t[1] if cpp_t else 0:>9.0f} QPS  |  比值 {ratio:.2f}x")
            print(line)
        if cpp_ready:
            print(f"  连接池状态：{cb.pool_stats()}（max=16）")
        print()

    if args.json:
        print(jsonlib.dumps(rows_out, ensure_ascii=False, indent=2))
        return 0

    # ---------- markdown 汇总 ----------
    print("| 并发 | 场景 | pymysql QPS | jt_db QPS | 比值(C++/py) |")
    print("|---|---|---|---|---|")
    for r in rows_out:
        cpp_v = r["cpp_qps"] if r["cpp_qps"] is not None else "-"
        print(f"| {r['threads']} | {r['scenario']} | {r['py_qps']} | {cpp_v} | {r['ratio']:.2f}x |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
