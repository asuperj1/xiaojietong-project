# -*- coding: utf-8 -*-
"""B20：DAO 直出通知扩展列 + Python 层三条路径（离线单测）。

`LifeDAO::page_notices` 的列清单原本是编译期写死的，拿不到 B19 的
``deadline``/``materials``/``importance``，只能在 Python 侧按 id 二次补查。
B20 给 DAO 加了 ``include_extended`` 形参后，`page_notices_rows()` 要在
**三种组合**下都给出与旧版一致的结果：

============================  ==========================================
库里有无扩展列                行为
============================  ==========================================
有 + jt_db 是 B20 之后版本     一次查询直出（最优）
有 + jt_db 仍是旧版本          多传的实参抛 TypeError → 回退旧签名 → 补查
无（未导入 14_notice_extend）  不尝试新形参，直接旧签名（与旧版逐字节一致）
============================  ==========================================

全程离线：假 DAO + 假探测函数，不连数据库。
"""
from __future__ import annotations

import pytest

from app.services import notice as notice_service

EXTENDED = ("deadline", "materials", "importance")


class FakeDAO:
    """模拟 jt_db 的新/旧两个版本，并记录调用签名。"""

    def __init__(self, *, supports_extended: bool) -> None:
        self.supports_extended = supports_extended
        self.calls: list[tuple] = []

    def page_notices(self, page, size, category="", target_grade="", include_extended=False):
        if include_extended and not self.supports_extended:
            # 与 pybind11 在形参不匹配时抛出的异常同类型
            raise TypeError("page_notices(): incompatible function arguments")
        self.calls.append((page, size, category, target_grade, include_extended))
        row = {
            "id": 1, "title": "t", "content": "c", "source": "s",
            "category": "k", "publish_time": "2026-01-01 00:00:00",
        }
        if include_extended:
            row.update({"deadline": None, "materials": None, "importance": 3})
        return [dict(row)]


@pytest.fixture
def env(monkeypatch):
    """安装假 DAO 与假列探测。"""

    def _install(*, dao_supports: bool, columns_exist: bool):
        dao = FakeDAO(supports_extended=dao_supports)
        monkeypatch.setattr(notice_service.cpp_bridge, "life_dao", lambda: dao)
        monkeypatch.setattr(
            notice_service, "notice_extended_columns",
            lambda: set(EXTENDED) if columns_exist else set(),
        )
        return dao

    return _install


# ------------------------------------------------- 路径一：直出（最优） ----


def test_prefers_dao_extended_when_columns_exist(env):
    dao = env(dao_supports=True, columns_exist=True)
    rows = notice_service.page_notices_rows(2, 5, "通知", "2024级")
    assert dao.calls == [(2, 5, "通知", "2024级", True)]
    assert all(col in rows[0] for col in EXTENDED)


def test_attach_is_noop_on_dao_extended_rows(env, monkeypatch):
    """直出的行里已有扩展列 —— 不该再补一次查询。"""
    env(dao_supports=True, columns_exist=True)
    rows = notice_service.page_notices_rows(1, 5)

    def _boom(*_a, **_kw):                       # 真去查库就应该炸
        raise AssertionError("扩展列已直出，不应再补查")

    monkeypatch.setattr(notice_service.cpp_bridge, "query", _boom)
    assert notice_service.attach_extended_fields(rows) is rows


# ------------------------------------------------- 路径二：旧 jt_db 回退 ----


def test_falls_back_to_old_signature_on_typeerror(env):
    dao = env(dao_supports=False, columns_exist=True)
    rows = notice_service.page_notices_rows(1, 5)
    assert dao.calls == [(1, 5, "", "", False)]          # 回退后不再带 True
    assert not any(col in rows[0] for col in EXTENDED)   # 由补查去填


def test_legacy_rows_still_get_backfilled(env, monkeypatch):
    env(dao_supports=False, columns_exist=True)
    rows = notice_service.page_notices_rows(1, 5)
    seen = {}

    def _fake_query(sql, params=None):
        seen["sql"] = sql
        return [{"id": 1, "deadline": "2026-09-30", "materials": "成绩单", "importance": 4}]

    monkeypatch.setattr(notice_service.cpp_bridge, "query", _fake_query)
    out = notice_service.attach_extended_fields(rows)
    assert out[0]["deadline"] == "2026-09-30"
    assert out[0]["importance"] == 4
    assert "IN (" in seen["sql"]


# ------------------------------------------- 路径三：库里没有扩展列 ----


def test_skips_extended_probe_when_columns_missing(env):
    dao = env(dao_supports=True, columns_exist=False)
    rows = notice_service.page_notices_rows(1, 5)
    # 探测为空时**不尝试**新形参，与旧版逐字节一致
    assert dao.calls == [(1, 5, "", "", False)]
    assert not any(col in rows[0] for col in EXTENDED)


def test_attach_returns_rows_unchanged_without_columns(env, monkeypatch):
    env(dao_supports=True, columns_exist=False)
    rows = [{"id": 1}]

    def _boom(*_a, **_kw):
        raise AssertionError("没有扩展列却去补查")

    monkeypatch.setattr(notice_service.cpp_bridge, "query", _boom)
    assert notice_service.attach_extended_fields(rows) is rows


# ------------------------------------------------------------- 边界 ----


def test_attach_handles_empty_rows(env, monkeypatch):
    env(dao_supports=True, columns_exist=True)

    def _boom(*_a, **_kw):
        raise AssertionError("空行不该查库")

    monkeypatch.setattr(notice_service.cpp_bridge, "query", _boom)
    assert notice_service.attach_extended_fields([]) == []
