"""C++ 数据访问层桥接模块。

对业务层暴露统一的数据访问门面，内部调用 pybind11 编译的 `jt_db` 扩展
（由 db/cpp_driver 编译生成，Windows 为 jt_db.pyd，Linux 为 jt_db.cpython-*.so）。

约定：
- 所有 SQL 使用 `?` 占位符 + 参数列表（内部走预处理语句，防注入）。
- 查询返回 list[dict]；写操作返回 (affected_rows, last_insert_id)。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Optional

# C++ 扩展编译产物统一放置 backend/app/db/native/
_NATIVE_DIR = Path(__file__).resolve().parent / "native"

try:
    if str(_NATIVE_DIR) not in sys.path:
        sys.path.insert(0, str(_NATIVE_DIR))
    import jt_db  # type: ignore

    _JT_DB_AVAILABLE = True
except ImportError:  # pragma: no cover - 扩展未编译时的降级路径
    jt_db = None  # type: ignore
    _JT_DB_AVAILABLE = False

_pool_ready = False


def available() -> bool:
    """C++ 扩展是否已编译并可用。"""
    return _JT_DB_AVAILABLE


def pool_ready() -> bool:
    """连接池是否已初始化。"""
    return _JT_DB_AVAILABLE and _pool_ready


def init_db(
    host: str,
    port: int,
    user: str,
    password: str,
    dbname: str,
    min_conn: int = 2,
    max_conn: int = 16,
) -> None:
    """初始化连接池（应用启动时调用一次）。"""
    global _pool_ready
    if jt_db is None:
        raise RuntimeError(
            "jt_db C++ 扩展未编译。请按 db/cpp_driver/README.md 构建后，"
            "将产物拷贝到 backend/app/db/native/"
        )
    jt_db.init_pool(host, port, user, password, dbname, min_conn, max_conn)
    _pool_ready = True


def query(sql: str, params: Optional[list] = None) -> list[dict[str, Any]]:
    """执行查询，返回 [{列名: 值}, ...]。"""
    _require_pool()
    return jt_db.query(sql, params or [])


def execute(sql: str, params: Optional[list] = None) -> tuple[int, int]:
    """执行写操作，返回 (affected_rows, last_insert_id)。"""
    _require_pool()
    return jt_db.execute(sql, params or [])


def ping() -> bool:
    """连接池健康检查。"""
    _require_pool()
    return jt_db.ping()


def pool_stats() -> dict:
    """返回连接池统计 {idle, active}。"""
    if jt_db is None:
        return {"available": False}
    return jt_db.pool_stats()


def begin():
    """开启事务，返回事务对象（with 语句自动 commit/rollback）。"""
    _require_pool()
    return jt_db.begin()


def user_dao():
    """获取 C++ UserDAO 实例。"""
    _require_pool()
    return jt_db.UserDAO()


def library_dao():
    _require_pool()
    return jt_db.LibraryDAO()


def forum_dao():
    _require_pool()
    return jt_db.ForumDAO()


def secondhand_dao():
    _require_pool()
    return jt_db.SecondhandDAO()


def job_dao():
    _require_pool()
    return jt_db.JobDAO()


def life_dao():
    _require_pool()
    return jt_db.LifeDAO()


def _require_pool() -> None:
    if jt_db is None:
        raise RuntimeError("jt_db C++ 扩展未编译")
    if not _pool_ready:
        raise RuntimeError("连接池未初始化，请先调用 init_db()")
