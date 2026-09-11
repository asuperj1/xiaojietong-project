#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FavoriteDAO 集成测试（C10 收敛验证）。

验证原 `backend/app/routers/favorite.py` 中散落的原生 SQL 被收敛到 C++ DAO 后，
行为与语义保持一致：
    [1] target_exists：存在性/可见性校验（含非法 target_type）
    [2] toggle：幂等切换（收藏 → 取消 → 再收藏）
    [3] is_favorited：状态查询与 toggle 结果一致
    [4] page_topics / count_topics：分页与总数
    [5] 事务内调用：DAO 走事务连接（与 with begin() 一致）

【运行（必须用能加载 jt_db.pyd 的解释器）】
    $env:XJT_DB_PASSWORD='***'; E:/miniconda3/python.exe db/cpp_driver/test/test_favorite_dao.py

作者：成员3（C++ 数据层 / 模型微调 / 数据库）· C10
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))

from app.db import cpp_bridge  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED if cond else FAILED).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"    {detail}" if detail else ""))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass

    host = os.environ.get("XJT_DB_HOST", "127.0.0.1")
    port = int(os.environ.get("XJT_DB_PORT", "3307"))
    user = os.environ.get("XJT_DB_USER", "root")
    dbname = os.environ.get("XJT_DB_NAME", "xiaojietong")
    password = os.environ.get("XJT_DB_PASSWORD", "")
    if not password:
        print("[错误] 未设置 XJT_DB_PASSWORD 环境变量。")
        return 1
    if not cpp_bridge.available():
        print("[错误] jt_db C++ 扩展不可用（请用能加载 .pyd 的解释器）。")
        return 1

    cpp_bridge.init_db(host, port, user, password, dbname, min_conn=2, max_conn=8)
    dao = cpp_bridge.favorite_dao()

    # 取一个真实用户与真实帖子作为测试对象
    users = cpp_bridge.query("SELECT id FROM `user` ORDER BY id LIMIT 1")
    if not users:
        print("[跳过] 库中无用户数据，无法测试。")
        return 0
    uid = int(users[0]["id"])

    topics = cpp_bridge.query(
        "SELECT id FROM topic WHERE is_deleted = 0 AND status != 1 ORDER BY id LIMIT 1")
    created_topic = False
    if topics:
        tid = int(topics[0]["id"])
    else:
        # 库中暂无可见帖子：临时造一条（audit_status=1 直接可见），结束时删除
        _, new_tid = cpp_bridge.execute(
            "INSERT INTO topic (author_id, title, content, category, audit_status) "
            "VALUES (?, ?, ?, ?, 1)",
            [uid, "__favorite_dao_test__", "C10 测试帖", "综合"])
        tid = int(new_tid)
        created_topic = True
        print(f"[准备] 库中无可见帖子，临时创建 topic id={tid} 用于测试")

    print(f"# FavoriteDAO 测试｜user={uid} topic={tid}\n")

    # 清理可能残留的测试收藏（保证幂等可重跑）
    cpp_bridge.execute(
        "DELETE FROM favorite WHERE user_id = ? AND target_type = 'topic' AND target_id = ?",
        [uid, tid])

    try:
        # ---------- [1] target_exists ----------
        print("[1] target_exists 存在性与白名单校验")
        check("存在的帖子 → True", dao.target_exists("topic", tid) is True)
        check("不存在的帖子 → False", dao.target_exists("topic", 99999999) is False)
        check("非法 target_type → False", dao.target_exists("merchant", tid) is False)

        # ---------- [2] toggle 幂等切换 ----------
        print("\n[2] toggle 幂等切换")
        check("首次 toggle → True（已收藏）", dao.toggle(uid, "topic", tid) is True)
        check("再次 toggle → False（已取消）", dao.toggle(uid, "topic", tid) is False)
        check("第三次 toggle → True（重新收藏）", dao.toggle(uid, "topic", tid) is True)
        check("非法 target_type → False 且不写库",
              dao.toggle(uid, "bogus", tid) is False)

        # ---------- [3] is_favorited 与库内状态一致 ----------
        print("\n[3] is_favorited 与库内状态一致")
        check("is_favorited → True", dao.is_favorited(uid, "topic", tid) is True)
        db_rows = cpp_bridge.query(
            "SELECT id FROM favorite WHERE user_id = ? AND target_type = 'topic' "
            "AND target_id = ?", [uid, tid])
        check("库内确实存在唯一一条收藏记录", len(db_rows) == 1,
              f"实际 {len(db_rows)} 条（唯一键应防重复）")

        # ---------- [4] 分页与总数 ----------
        print("\n[4] page_topics / count_topics")
        rows = dao.page_topics(uid, 10, 0)
        total = dao.count_topics(uid)
        check("page_topics 返回列表且包含刚收藏的帖子",
              any(int(r["id"]) == tid for r in rows), f"共 {len(rows)} 行")
        check("count_topics >= 1", total >= 1, f"total={total}")
        check("收藏列表附带 favorite_id / favorited_at",
              bool(rows) and "favorite_id" in rows[0] and "favorited_at" in rows[0])

        # ---------- [5] 事务内 DAO 复用事务连接 ----------
        print("\n[5] 事务内调用（DAO 走事务连接）")
        with cpp_bridge.begin():
            dao.toggle(uid, "topic", tid)          # 取消
            in_tx = dao.is_favorited(uid, "topic", tid)
        after = dao.is_favorited(uid, "topic", tid)
        check("事务内取消后不可见，且事务提交后状态持久", (in_tx is False) and (after is False),
              f"事务内={in_tx} 提交后={after}")

    finally:
        cpp_bridge.execute(
            "DELETE FROM favorite WHERE user_id = ? AND target_type = 'topic' "
            "AND target_id = ?", [uid, tid])
        if created_topic:
            cpp_bridge.execute("DELETE FROM topic WHERE id = ?", [tid])
        print("\n已清理测试数据（收藏记录" + ("与临时帖子" if created_topic else "") + "）")

    total_n = len(PASSED) + len(FAILED)
    print(f"\n===== 结果：{len(PASSED)}/{total_n} 通过 =====")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
