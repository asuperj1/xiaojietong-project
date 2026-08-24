#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""校捷通 C++ 数据访问层 · DAO 层集成测试。

验证 UserDAO（范式 DAO）：
  - 查询：find_by_openid / find_by_id / page / count
  - 写：create / update_profile / update_role / remove
  - 事务内使用 DAO（验证 DbSession 事务连接绑定对 DAO 同样生效）

用法：
  XJT_DB_PASSWORD=jhq000000 python test_dao.py
"""

import os
import sys
from pathlib import Path

_NATIVE = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "backend" / "app" / "db" / "native"
)
if str(_NATIVE) not in sys.path:
    sys.path.insert(0, str(_NATIVE))

import jt_db  # noqa: E402

HOST = os.environ.get("XJT_DB_HOST", "127.0.0.1")
PORT = int(os.environ.get("XJT_DB_PORT", "3307"))
USER = os.environ.get("XJT_DB_USER", "root")
PASSWORD = os.environ.get("XJT_DB_PASSWORD", "")
DBNAME = os.environ.get("XJT_DB_NAME", "xiaojietong")

if not PASSWORD:
    sys.exit("请设置 XJT_DB_PASSWORD 后运行")

dao = jt_db.UserDAO()


def main() -> None:
    jt_db.init_pool(HOST, PORT, USER, PASSWORD, DBNAME, 2, 8)
    print(f"[1] 连接池状态: {jt_db.pool_stats()}")

    # [2] 查询
    u = dao.find_by_openid("oXJT_TEST_0001")
    assert u is not None and u["nickname"] == "测试用户A", u
    print("[2] find_by_openid 通过:", u["openid"], u["nickname"])

    u2 = dao.find_by_id(int(u["id"]))
    assert u2 is not None and u2["id"] == u["id"]
    print("[3] find_by_id 通过: id=", u2["id"])

    # [4] 分页
    page = dao.page(1, 10)
    print(f"[4] page(1,10) 返回 {len(page)} 条, total={dao.count()}")

    # [5] 创建（事务内）
    new_id = None
    with jt_db.begin():
        new_id = dao.create("oXJT_DAO_TEST", "DAO测试用户", "", "", 0)
        assert new_id > 0, new_id
        # 事务内查询也应走事务连接（此处若走池连接也不会失败，仅验证一致性）
        created = dao.find_by_id(new_id)
        assert created is not None and created["openid"] == "oXJT_DAO_TEST"
    print(f"[5] 事务内 create 通过: new_id={new_id}")

    # [6] 更新
    ok1 = dao.update_profile(new_id, "DAO改名", "http://x/avatar.png")
    ok2 = dao.update_role(new_id, 1)
    updated = dao.find_by_id(new_id)
    assert ok1 and ok2 and updated["nickname"] == "DAO改名" and updated["role"] == "1"
    print("[6] update_profile / update_role 通过:", updated["nickname"], updated["role"])

    # [7] 事务回滚对 DAO 生效（关键验证：DAO 在事务内走事务连接）
    try:
        with jt_db.begin():
            dao.create("oXJT_DAO_ROLLBACK", "回滚测试", "", "", 0)
            raise ValueError("触发回滚")
    except ValueError:
        pass
    rollback_user = dao.find_by_openid("oXJT_DAO_ROLLBACK")
    assert rollback_user is None, "回滚失败！DAO 事务内写入未回滚"
    print("[7] 事务内 DAO 写入已正确回滚（DbSession 绑定生效）")

    # [8] 清理测试数据
    dao.remove(new_id)
    # 兜底清理（确保回滚测试未残留）
    for oid in ("oXJT_DAO_TEST", "oXJT_DAO_ROLLBACK"):
        uu = dao.find_by_openid(oid)
        if uu:
            dao.remove(int(uu["id"]))
    print("[8] 已清理测试数据, 剩余用户数:", dao.count())

    print("\n【成功】DAO 层集成测试全部通过！")


if __name__ == "__main__":
    main()
