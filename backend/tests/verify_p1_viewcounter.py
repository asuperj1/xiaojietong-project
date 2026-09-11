#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1 加固验证：浏览量延迟聚合写入（CAC-03）。

验证内容：
    1) 连续读取同一帖子 N 次后，**立即**查库应看不到变化（证明写已延迟，
       读路径没有逐次 UPDATE，即热点行不再被反复加锁）
    2) 等待一个 flush 周期后，增量被**合并成一次**写入并正确累加
    3) 应用关闭时会做最后一次落库（由 lifespan 的 stop() 保证，本脚本只覆盖 1、2）

用法：
    python backend/tests/verify_p1_viewcounter.py
    python backend/tests/verify_p1_viewcounter.py --reads 20 --wait 8

前置：后端已启动；本机可执行 mysql 客户端（或设置 XJT_MYSQL_CLI）。

作者：成员3 · 审计修复
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import uuid

import httpx

PASSED: list[str] = []
FAILED: list[str] = []

MYSQL = os.environ.get(
    "XJT_MYSQL_CLI", r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe")


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"    {detail}" if detail else ""))


def mysql(sql: str) -> str:
    """执行 SQL 并返回 stdout（同一条命令内多条语句共用连接）。"""
    port = os.environ.get("XJT_DB_PORT", "3307")
    env = dict(os.environ)
    password = env.get("XJT_DB_PASSWORD")
    if password:
        env["MYSQL_PWD"] = password
    out = subprocess.run(
        [MYSQL, "-h", "127.0.0.1", "-P", port, "-u", "root", "-N", "-B", "-e", sql],
        env=env, check=True, capture_output=True)
    return out.stdout.decode("utf-8", "replace").strip()


def view_count(topic_id: int) -> int:
    raw = mysql(f"SELECT view_count FROM xiaojietong.topic WHERE id={topic_id};")
    return int(raw or 0)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser(description="浏览量聚合验证")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--reads", type=int, default=20, help="连续读取次数")
    ap.add_argument("--wait", type=float, default=8.0, help="等待落库的秒数")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    print(f"# 浏览量聚合验证｜目标 {base}｜连续读 {args.reads} 次\n")

    if not os.path.isfile(MYSQL):
        print(f"[致命] 找不到 mysql 客户端：{MYSQL}（可用 XJT_MYSQL_CLI 指定）")
        return 2

    client = httpx.Client(base_url=base, timeout=30, trust_env=False)

    # ---------- 准备 ----------
    r = client.post("/api/v1/auth/wechat-login",
                    json={"code": f"cac03_{uuid.uuid4().hex[:8]}"})
    if r.status_code != 200 or r.json().get("code") != 0:
        print(f"[致命] 登录失败：HTTP {r.status_code} {r.text[:200]}")
        print("       若为 429，说明登录限流已触发，请等 60 秒后重试。")
        return 2
    token = r.json()["data"]["token"]
    uid = int(r.json()["data"]["user"]["id"])
    headers = {"Authorization": f"Bearer {token}"}

    raw = mysql(
        f"INSERT INTO xiaojietong.topic (author_id, title, content, category) "
        f"VALUES ({uid}, 'CAC03验证帖_{uuid.uuid4().hex[:6]}', '浏览量聚合验证', '综合');"
        f"SELECT LAST_INSERT_ID();")
    topic_id = int(raw.splitlines()[-1])
    print(f"[准备] 测试帖 id={topic_id}，初始 view_count={view_count(topic_id)}\n")

    try:
        # ---------- 1) 读 N 次后立即查库 ----------
        print(f"[CAC-03] 连续 GET /topics/{topic_id} {args.reads} 次")
        ok_reads = 0
        for _ in range(args.reads):
            resp = client.get(f"/api/v1/topics/{topic_id}", headers=headers)
            if resp.status_code == 200 and resp.json().get("code") == 0:
                ok_reads += 1
        check(f"{args.reads} 次读取全部成功", ok_reads == args.reads,
              f"成功 {ok_reads}/{args.reads}")

        immediate = view_count(topic_id)
        check("读取后立即查库：尚未落库（证明读路径无逐次 UPDATE）",
              immediate == 0,
              f"view_count={immediate}（期望 0，说明计数仍在内存中）")

        # ---------- 2) 等待落库 ----------
        print(f"       等待 {args.wait:.0f}s 让后台任务落库…")
        time.sleep(args.wait)
        after = view_count(topic_id)
        check("超过一个 flush 周期后增量已合并落库",
              after == args.reads,
              f"view_count={after}（期望 {args.reads}）")

        # ---------- 3) 再读一轮，确认可反复累加 ----------
        for _ in range(args.reads):
            client.get(f"/api/v1/topics/{topic_id}", headers=headers)
        time.sleep(args.wait)
        final = view_count(topic_id)
        check("第二轮读取继续正确累加",
              final == args.reads * 2,
              f"view_count={final}（期望 {args.reads * 2}）")
    finally:
        mysql(f"DELETE FROM xiaojietong.topic WHERE id={topic_id};")
        print(f"       （已清理测试帖 {topic_id}）")

    print()
    total = len(PASSED) + len(FAILED)
    print(f"===== 结果：{len(PASSED)}/{total} 通过 =====")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
