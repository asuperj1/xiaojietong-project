# -*- coding: utf-8 -*-
"""`/life/notices` 公共口径分页 —— 剔除私密推送行时的分页正确性（离线单测）。

背景：B18 分层推送的通知行（``target_grade = '__push:...'``）绝不能进入他人可见的
公共列表，剔除逻辑因此挂在读路径上。这里固定两条**实测复现**过的坑：

1. **小 size 返回空**：私密行（本库 10 条）全堆在时间轴顶端时，
   缓冲若只放"一页"，``size=1`` / ``size=3`` 根本够不到公共行 → 接口返回空列表；
2. **跨页重复 + 漏项**：用"取第 page 页、不够再向后补拉"凑数时，窗口整体前移，
   同一行出现在相邻两页，靠后的行被挤掉。

用例全程离线：``cpp_bridge.life_dao`` 被换成内存假 DAO（含 C++ 侧
``size > 100 → size = 20`` 的重置语义），不连数据库、不依赖 jt_db。
"""
from __future__ import annotations

import pytest

from app.services import notice as notice_service

PRIVATE_ID_BASE = 900          # 私密推送行 id 段
PRIVATE_N = 10                 # 与线上实测一致：最新 10 条都是私密行
PUBLIC_ID_BASE = 100           # 公共通知 id 段


def _dataset(n_private: int = PRIVATE_N, n_public: int = 14) -> list[dict]:
    """构造「私密行占据时间轴顶端」的数据集（publish_time DESC 之后的顺序）。"""
    rows = [
        {"id": PRIVATE_ID_BASE + i, "title": f"私密推送 {i}", "target_grade": "__push:x"}
        for i in range(n_private)
    ]
    rows += [{"id": PUBLIC_ID_BASE + i, "title": f"公共通知 {i}"} for i in range(n_public)]
    return rows


def _private_ids(n_private: int = PRIVATE_N) -> set[int]:
    return set(range(PRIVATE_ID_BASE, PRIVATE_ID_BASE + n_private))


class FakeDAO:
    """内存 DAO，语义对齐 `LifeDAO::page_notices`（含 size 上限重置）。"""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[tuple[int, int]] = []

    def page_notices(self, page: int, size: int, category: str, target_grade: str) -> list[dict]:
        if size > 100:
            size = 20          # C++ 侧是**重置**为 20，不是截断到 100
        self.calls.append((page, size))
        start = (page - 1) * size
        return [dict(r) for r in self.rows[start:start + size]]


@pytest.fixture
def fake_dao(monkeypatch):
    """把 `notice_service` 里的 DAO 换成假的，返回一个安装器。"""

    def _install(rows: list[dict]) -> FakeDAO:
        dao = FakeDAO(rows)
        monkeypatch.setattr(notice_service.cpp_bridge, "life_dao", lambda: dao)
        return dao

    return _install


# ------------------------------------------------------- 坑一：小 size 返回空 ----


@pytest.mark.parametrize("size", [1, 2, 3, 4])
def test_small_size_returns_public_rows(fake_dao, size):
    """size 小于私密行条数时，仍必须返回公共行（修复前恒为空）。"""
    fake_dao(_dataset())
    out = notice_service.public_notice_page(_private_ids(), 1, size)
    assert [r["id"] for r in out] == [PUBLIC_ID_BASE + i for i in range(size)]


def test_legacy_backfill_logic_really_returns_empty(fake_dao):
    """反向对照：证明上面的断言不是恒真 —— 旧的「补拉 2 页」确实返回空。"""
    dao = fake_dao(_dataset())

    def legacy(private_ids: set[int], page: int, size: int) -> list[dict]:
        items: list[dict] = []
        probe = page
        for _ in range(3):                      # 1 页 + 最多补拉 2 页（旧实现）
            rows = dao.page_notices(probe, size, "", "")
            if not rows:
                break
            items.extend(r for r in rows if int(r["id"]) not in private_ids)
            if len(items) >= size or len(rows) < size:
                break
            probe += 1
        return items[:size]

    assert legacy(_private_ids(), 1, 3) == []                                          # 旧：空
    assert notice_service.public_notice_page(_private_ids(), 1, 3) != []               # 新：有


def test_size_larger_than_public_rows_returns_all(fake_dao):
    fake_dao(_dataset())
    out = notice_service.public_notice_page(_private_ids(), 1, 20)
    assert len(out) == 14 and all(r["id"] < PRIVATE_ID_BASE for r in out)


# ------------------------------------------------- 坑二：跨页重复 / 漏项 ----


def test_pages_are_contiguous_without_overlap(fake_dao):
    """逐页翻下来必须是「无重复、无漏项」的一段连续公共行。"""
    fake_dao(_dataset())
    seen: list[int] = []
    for page in (1, 2, 3, 4):
        seen.extend(r["id"] for r in notice_service.public_notice_page(_private_ids(), page, 3))
    assert seen == [PUBLIC_ID_BASE + i for i in range(12)]
    assert len(seen) == len(set(seen))


def test_last_page_is_partial_not_duplicated(fake_dao):
    """越过数据末尾应返回空，而不是把上一页内容重复吐出来。"""
    fake_dao(_dataset())
    assert notice_service.public_notice_page(_private_ids(), 9, 3) == []


# --------------------------------------------------------- 私密行不得泄漏 ----


@pytest.mark.parametrize("size", [1, 3, 20])
def test_private_rows_never_leak(fake_dao, size):
    fake_dao(_dataset())
    out = notice_service.public_notice_page(_private_ids(), 1, size)
    assert all(r["id"] < PRIVATE_ID_BASE for r in out)


# ------------------------------------------------------------- 快路径 / 退化 ----


def test_no_private_rows_keeps_original_single_query(fake_dao):
    """无私密行时保持单次查询，参数与调用方一致（零额外开销）。"""
    dao = fake_dao(_dataset(n_private=0))
    out = notice_service.public_notice_page(set(), 2, 5)
    assert [r["id"] for r in out] == [PUBLIC_ID_BASE + i for i in range(5, 10)]
    assert dao.calls == [(2, 5)]


def test_many_private_rows_falls_back_and_still_fills_page(fake_dao):
    """私密行超过 DAO 单次上限时走逐块累积，仍要凑满这一页。"""
    dao = fake_dao(_dataset(n_private=150, n_public=30))
    out = notice_service.public_notice_page(_private_ids(150), 1, 5)
    assert [r["id"] for r in out] == [PUBLIC_ID_BASE + i for i in range(5)]
    assert all(size <= 100 for _, size in dao.calls)      # 从不超过 DAO 上限
    assert len(dao.calls) >= 2                            # 确实走了多块


def test_private_ids_larger_than_dataset_returns_empty(fake_dao):
    """极端情形：整库都是私密行 → 返回空，且不无限翻页。"""
    dao = fake_dao(_dataset(n_private=10, n_public=0))
    assert notice_service.public_notice_page(_private_ids(), 1, 5) == []
    assert len(dao.calls) <= 3
