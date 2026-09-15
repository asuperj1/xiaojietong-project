"""回归：`/life/notices` 剔除私密推送行时**不得破坏分页**（跨页重复 / 漏项）。

## 背景（PR #60 审查 P0 的修复引入的回归，2026-09-15 实测抓到）

B18 的分层推送去 `campus_notice` 里写了 `target_grade = '__push:...'` 的**私密行**，
`/life/notices`（公共列表）必须把它们剔掉，否则个人待办会泄漏给他人。
`LifeDAO.page_notices` 的 SELECT 不含 `target_grade`，所以只能用「私密行 id 集合」按 id 剔除。

原实现是：

    private_ids = private_notice_ids()
    probe = page
    for _ in range(3):                      # 1 页 + 最多补拉 2 页
        rows = page_notices(probe, size, ...)
        items.extend(r for r in rows if id 不在 private_ids)
        if len(items) >= size: break
        probe += 1
    return items[:size]

**错在哪**：第 N 页的补拉会把它的窗口**整体前移**到第 N+1、N+2 页，
于是第 N 页返回的尾部条目，会再次出现在第 N+1 页（而真正该在第 N+1 页的条目被挤掉）。

**实测证据**（`size=3`，插入 1 条私密行后）：

| 场景 | page1 | page2 | 交集 |
|---|---|---|---|
| 无私密行 | `[9008, 9007, 9006]` | `[9005, 9004, 9003]` | `[]` ✅ |
| 有私密行 | `[9008, 9007, 9006]` | `[9006, 9005, 9004]` | `[9006]` ❌ 9003 被挤出可见范围 |

本文件把该现象固化成断言：**插一条私密行 ⇒ 翻页仍不重不漏**。
"""

from __future__ import annotations

from typing import Iterator

import pytest

from app.db import cpp_bridge

# 探针私密行（targe_grade 用真实前缀，确保走的是生产同一条过滤路径）
_PROBE_MARK = "__push:pytest_paging:1:D7"
_PAGE_SIZE = 3
_MIN_ROWS = _PAGE_SIZE * 3      # 至少要能翻出 3 页才做分页断言


@pytest.fixture
def private_probe_notice() -> Iterator[int]:
    """插入一条「最新」的私密推送行，用完即删（不留残留）。

    用 ``publish_time = NOW() + INTERVAL 1 DAY`` 让它**必定排在第 1 页第一条**，
    这样原来的「补拉」逻辑必然被触发 —— 缺陷是确定性的，不靠运气。
    """
    cpp_bridge.execute("DELETE FROM campus_notice WHERE target_grade = ?", [_PROBE_MARK])
    _, notice_id = cpp_bridge.execute(
        "INSERT INTO campus_notice "
        "(title, content, source, category, target_grade, publish_time) "
        "VALUES (?, ?, ?, ?, ?, NOW() + INTERVAL 1 DAY)",
        ["pytest 私密探针", "pytest", "pytest", "pytest", _PROBE_MARK],
    )
    probe_id = int(notice_id)
    try:
        yield probe_id
    finally:
        cpp_bridge.execute("DELETE FROM notice_delivery WHERE notice_id = ?", [probe_id])
        cpp_bridge.execute("DELETE FROM campus_notice WHERE id = ?", [probe_id])
        left = cpp_bridge.query("SELECT id FROM campus_notice WHERE target_grade = ?", [_PROBE_MARK])
        assert not left, f"探针行未清理干净：{left}"


def _page_ids(client, hdr: dict, page: int) -> list[int]:
    resp = client.get(f"/api/v1/life/notices?page={page}&size={_PAGE_SIZE}", headers=hdr)
    assert resp.status_code == 200, resp.text
    return [int(r["id"]) for r in resp.json()["data"]["items"]]


def _visible_total() -> int:
    """公共可见口径的总行数（用于判断库里有足够数据做分页断言）。"""
    rows = cpp_bridge.query(
        "SELECT COUNT(*) AS c FROM campus_notice WHERE target_grade NOT LIKE '__push:%'"
    )
    return int(rows[0]["c"]) if rows else 0


# --------------------------------------------------------------- 基线（反向对照）

def test_paging_is_stable_without_private_rows(client, hdr_a):
    """反向对照：**没有**私密行时，翻页本来就不该重复。

    这条保证「不重复」这个断言不是恒真 —— 一旦实现被改坏（或数据源异常），
    它同样会红，说明断言真的在起作用。
    """
    if _visible_total() < _MIN_ROWS:
        pytest.skip(f"公共通知不足 {_MIN_ROWS} 条，无法做翻页断言")
    p1, p2 = _page_ids(client, hdr_a, 1), _page_ids(client, hdr_a, 2)
    assert p1 and p2, f"数据不足：page1={p1} page2={p2}"
    assert not set(p1) & set(p2), f"无私密行时也出现跨页重复：page1={p1} page2={p2}"


# --------------------------------------------------------------- 回归主用例

def test_paging_has_no_overlap_when_private_row_is_newest(client, hdr_a, private_probe_notice):
    """⭐ 回归主用例：最新一条是私密行时，page1 与 page2 不得有交集。

    修复前必红（实测交集 = 1 条）；修复后必绿。
    """
    p1, p2 = _page_ids(client, hdr_a, 1), _page_ids(client, hdr_a, 2)
    assert p1 and p2, f"数据不足：page1={p1} page2={p2}"
    assert not set(p1) & set(p2), (
        f"跨页重复：page1={p1} page2={p2}，交集={sorted(set(p1) & set(p2))}"
    )


def test_private_row_is_not_leaked_to_public_list(client, hdr_a, private_probe_notice):
    """私密行不得出现在公共列表（P0 本身不能回退）。"""
    seen = set()
    for page in (1, 2, 3):
        seen |= set(_page_ids(client, hdr_a, page))
    assert private_probe_notice not in seen, "私密推送行泄漏到了 /life/notices"


def test_public_pages_are_contiguous_when_private_row_present(client, hdr_a, private_probe_notice):
    """翻页必须**连续不漏**：page1 + page2 正好等于无过滤顺序的前 2×size 条公共行。

    只看「不重复」还不够 —— 还要证明没有漏项（原实现会把 9003 挤出可见范围）。
    """
    # 取无过滤顺序的前 2×size 条公共行（按 publish_time DESC），即为期望的可见集合
    rows = cpp_bridge.query(
        "SELECT id FROM campus_notice WHERE target_grade NOT LIKE '__push:%' "
        "ORDER BY publish_time DESC LIMIT ?",
        [_PAGE_SIZE * 2],
    )
    if len(rows) < _PAGE_SIZE * 2:
        pytest.skip("公共通知不足 2 页，无法做连续性断言")
    expected = {int(r["id"]) for r in rows}

    actual = set(_page_ids(client, hdr_a, 1)) | set(_page_ids(client, hdr_a, 2))
    assert actual == expected, (
        f"翻页集合与真实顺序不一致（漏项或多出）：缺={sorted(expected - actual)} "
        f"多={sorted(actual - expected)}"
    )
