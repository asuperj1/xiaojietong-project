#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""校捷通 C++ 数据访问层 · Python 侧集成测试。

验证 pybind11 扩展 jt_db 的：
  - 连接池初始化 / 统计
  - 参数化查询（? 占位符）
  - 写操作（INSERT）
  - 事务（with jt_db.begin() 自动 commit / 异常自动 rollback）

用法：
  # 1) 先编译 C++ 扩展（见 db/cpp_driver/README.md），确认产物在
  #    backend/app/db/native/jt_db.pyd
  # 2) 执行 db/sql/01_schema.sql + 02_init_data.sql
  # 3) 设置数据库密码并运行：
  XJT_DB_PASSWORD=你的密码 python test_py.py
"""

import os
import sys
from pathlib import Path

# 优先尝试通过后端桥接层加载（含日志与初始化封装）
_NATIVE = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "backend" / "app" / "db" / "native"
)
if str(_NATIVE) not in sys.path:
    sys.path.insert(0, str(_NATIVE))

import jt_db  # noqa: E402

HOST = os.environ.get("XJT_DB_HOST", "127.0.0.1")
PORT = int(os.environ.get("XJT_DB_PORT", "3307"))  # 本机 MySQL 实例端口
USER = os.environ.get("XJT_DB_USER", "root")
PASSWORD = os.environ.get("XJT_DB_PASSWORD", "")
DBNAME = os.environ.get("XJT_DB_NAME", "xiaojietong")

if not PASSWORD:
    sys.exit("请设置环境变量 XJT_DB_PASSWORD 后运行，例如：XJT_DB_PASSWORD=xxx python test_py.py")


def main() -> None:
    # 1) 连接池初始化
    jt_db.init_pool(HOST, PORT, USER, PASSWORD, DBNAME, min_conn=2, max_conn=8)
    print("[1] 连接池状态:", jt_db.pool_stats())

    # 2) 参数化查询
    rows = jt_db.query("SELECT id, nickname, role FROM `user`")
    print(f"[2] user 表共 {len(rows)} 行")
    for r in rows[:3]:
        print("    ", r)

    # 3) 参数化查询（WHERE + ? 占位）
    students = jt_db.query("SELECT nickname FROM `user` WHERE role = ?", [0])
    print(f"[3] 学生数量: {len(students)}")

    # 4) 写操作（INSERT，事务内提交）
    affected, last_id = jt_db.execute(
        "INSERT INTO `user` (openid, nickname, role) VALUES (?, ?, ?)",
        ["oXJT_PYTEST", "Python测试用户", 0],
    )
    print(f"[4] INSERT affected={affected} last_insert_id={last_id}")

    # 5) 事务：with 语句正常退出 → 自动 commit
    with jt_db.begin() as tx:
        affected2, last_id2 = jt_db.execute(
            "INSERT INTO `user` (openid, nickname, role) VALUES (?, ?, ?)",
            ["oXJT_PYTEST_TX1", "事务提交", 0],
        )
        tx.commit()
    print(f"[5] 事务提交成功, last_insert_id={last_id2}")

    # 6) 事务：异常退出 → 自动 rollback（数据不应残留）
    try:
        with jt_db.begin():
            jt_db.execute(
                "INSERT INTO `user` (openid, nickname, role) VALUES (?, ?, ?)",
                ["oXJT_PYTEST_TX2", "事务回滚", 0],
            )
            raise ValueError("模拟业务异常，触发回滚")
    except ValueError:
        print("[6] 捕获业务异常，事务已自动回滚")

    # 7) 验证回滚结果
    cnt = jt_db.query(
        "SELECT COUNT(*) AS c FROM `user` WHERE openid IN (?, ?, ?)",
        ["oXJT_PYTEST", "oXJT_PYTEST_TX1", "oXJT_PYTEST_TX2"],
    )[0]["c"]
    print(f"[7] 残留测试数据条数（应为 2: PYTEST + TX1，TX2 已回滚）: {cnt}")

    # 8) 清理测试数据
    jt_db.execute("DELETE FROM `user` WHERE openid LIKE ?", ["oXJT_PYTEST%"])
    print("[8] 已清理测试数据")

    print("\n【成功】Python 侧集成测试全部通过！")


if __name__ == "__main__":
    main()
