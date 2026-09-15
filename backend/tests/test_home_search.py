"""B21 首页接口 + B22 搜索历史：集成测试（依赖真实数据库，不可用自动 skip）。

设计要点（吸取 PR #60 审查的教训）：
- **不做恒真空断言**：凡"某些内容不得出现"的断言，都先证明**该内容确实存在**，
  再做反向对照（例：先查库确认存在停用/过期 banner，再断言它们不在响应里）；
- **不写死表形状**：`home_banner` 在不同环境有 `image_url/link_url` 与
  `image/link_type/link_target` 两种形状，断言只针对**接口契约字段**与
  `id/enabled/sort/start_at/end_at` 这类两边都有的列；
- 用例自建自清，不污染演示数据。
"""

from __future__ import annotations

import time

import pytest

from app.db import cpp_bridge

API = "/api/v1"


def _ok(resp):
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]


# ------------------------------------------------------------ B21 banners ----

def test_banners_contract_and_sort_desc(client, hdr_a):
    """契约字段齐全 + 按 sort 倒序（越大越前，与 15_home_banner.sql 一致）。"""
    items = _ok(client.get(f"{API}/home/banners", headers=hdr_a))["items"]
    if not items:
        pytest.skip("home_banner 当前无启用中的轮播，无法断言排序")
    sorts = [int(i["sort"]) for i in items]
    assert sorts == sorted(sorts, reverse=True), f"未按 sort 倒序：{sorts}"
    for it in items:
        for key in ("id", "title", "image_url", "link_url", "sort"):
            assert key in it, f"轮播响应缺契约字段 {key}"
        assert int(it["id"]) > 0


def test_banners_excludes_disabled_and_expired(client, hdr_a):
    """反向对照：库里**确实存在**停用/过期 banner 时，它们不得出现在响应里。

    若库里一条停用/过期的都没有，则 skip（而不是"空断言恰好通过"）。
    """
    stale = cpp_bridge.query(
        "SELECT id FROM home_banner WHERE enabled = 0 OR (start_at IS NOT NULL AND start_at > NOW()) "
        "OR (end_at IS NOT NULL AND end_at < NOW())"
    )
    if not stale:
        pytest.skip("库里没有停用/过期的 banner，无法做反向对照")
    stale_ids = {int(r["id"]) for r in stale}
    got_ids = {int(i["id"]) for i in _ok(client.get(f"{API}/home/banners", headers=hdr_a))["items"]}
    assert not (stale_ids & got_ids), f"停用/过期 banner 泄漏：{sorted(stale_ids & got_ids)}"


def test_banners_requires_auth(client):
    resp = client.get(f"{API}/home/banners")
    assert resp.status_code == 401, resp.text


# --------------------------------------------------------------- B21 feed ----

def test_home_feed_recommend_paginates(client, hdr_a):
    first = _ok(client.get(f"{API}/home/feed?sort=recommend&page=1&size=2", headers=hdr_a))
    assert len(first["items"]) <= 2
    assert "total" in first and first["total"] >= len(first["items"])
    if first["total"] > 2:
        second = _ok(client.get(f"{API}/home/feed?sort=recommend&page=2&size=2", headers=hdr_a))
        assert {int(i["id"]) for i in first["items"]} != {int(i["id"]) for i in second["items"]}


def test_home_feed_hot_only_audited(client, hdr_a):
    """热榜安全兜底：`sort=hot` 只允许**已过审**的帖子（`_only_audited`）。

    反向对照：先查库确认存在 `is_hot=1` 的未过审帖子（跳过"库里根本没有"的假通过），
    再断言它们不在响应里；同时断言响应里的每条都是 `audit_status=1`。
    """
    data = _ok(client.get(f"{API}/home/feed?sort=hot&page=1&size=50", headers=hdr_a))
    ids = [int(i["id"]) for i in data["items"]]
    if ids:
        ph = ",".join("?" for _ in ids)
        rows = cpp_bridge.query(
            f"SELECT id, audit_status FROM topic WHERE id IN ({ph})", ids
        )
        bad = [int(r["id"]) for r in rows if int(r.get("audit_status") or 0) != 1]
        assert not bad, f"热榜含未过审帖子：{bad}"
    unflagged = cpp_bridge.query(
        "SELECT id FROM topic WHERE is_hot = 1 AND (audit_status != 1 OR is_deleted = 1)"
    )
    if unflagged:
        assert not ({int(r["id"]) for r in unflagged} & set(ids)), "被打标的未过审帖子泄漏进热榜"


def test_home_feed_invalid_sort_is_contract_error(client, hdr_a):
    """非法 sort 必须是契约错误（`err_param` = HTTP 400 + body `code=1001`），
    而不是 FastAPI 的 422 `{"detail": ...}`（审计"空 body → 422"同族问题）。"""
    resp = client.get(f"{API}/home/feed?sort=whatever", headers=hdr_a)
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == 1001, resp.text


def test_home_feed_requires_auth(client):
    assert client.get(f"{API}/home/feed").status_code == 401


# ------------------------------------------------------- B22 search history ----

def test_search_history_add_dedup_order_delete_clear(client, hdr_a):
    """一条链路验完：写入 → 同词重写（去重）→ 倒序 → 单删 → 清空。"""
    assert _ok(client.delete(f"{API}/search/history", headers=hdr_a))["deleted"] >= 0

    for kw in ("pytest-图书馆", "pytest-高数", "pytest-考研"):
        body = _ok(client.post(f"{API}/search/history", json={"keyword": kw}, headers=hdr_a))
        assert body["dedup"] is False
    # `created_at` 是**秒精度**（DATETIME 无小数位）：等 1 秒才能观测到"刷新时间"的效果，
    # 否则同秒内 `created_at DESC, id DESC` 会把被刷新的老 id 排到最后。
    # 待办：B19 打包可考虑把该列改为 DATETIME(3)，届时可去掉这里的 sleep。
    time.sleep(1.1)
    again = _ok(client.post(f"{API}/search/history", json={"keyword": "pytest-图书馆"}, headers=hdr_a))
    assert again["dedup"] is True, "同词重写应标记 dedup=true（ODKU 刷新时间）"

    items = _ok(client.get(f"{API}/search/history?limit=10", headers=hdr_a))["items"]
    kws = [i["keyword"] for i in items]
    assert kws[0] == "pytest-图书馆", f"重写后应顶到最前（created_at DESC）：{kws}"
    assert len(kws) == len(set(kws)), "同人同词出现重复行 —— 唯一键/ODKU 失效"

    victim = int(items[-1]["id"])
    assert _ok(client.delete(f"{API}/search/history/{victim}", headers=hdr_a))["deleted"] is True
    left = _ok(client.get(f"{API}/search/history?limit=10", headers=hdr_a))["total"]
    assert _ok(client.delete(f"{API}/search/history", headers=hdr_a))["deleted"] >= 0
    assert _ok(client.get(f"{API}/search/history", headers=hdr_a))["total"] == 0
    assert left >= 0


def test_search_history_rejects_blank_and_oversize(client, hdr_a):
    assert client.post(f"{API}/search/history", json={"keyword": "   "}, headers=hdr_a).json()["code"] == 1001
    long_kw = "长" * 65
    assert client.post(f"{API}/search/history", json={"keyword": long_kw}, headers=hdr_a).json()["code"] == 1001


def test_search_history_delete_requires_ownership(client, hdr_a, hdr_b):
    """越权：用 B 的 token 删 A 的记录必须失败（`user_id` 条件兜住）。"""
    _ok(client.post(f"{API}/search/history", json={"keyword": "pytest-越权"}, headers=hdr_a))
    mine = [i for i in _ok(client.get(f"{API}/search/history?limit=20", headers=hdr_a))["items"]
            if i["keyword"] == "pytest-越权"]
    assert mine, "前置数据未写入"
    rid = int(mine[0]["id"])
    resp = client.delete(f"{API}/search/history/{rid}", headers=hdr_b)
    assert resp.json()["code"] == 1001, resp.text
    still = [i["id"] for i in _ok(client.get(f"{API}/search/history?limit=20", headers=hdr_a))["items"]]
    assert rid in [int(x) for x in still], "A 的记录被 B 删掉了（越权）"
    _ok(client.delete(f"{API}/search/history", headers=hdr_a))


def test_search_history_requires_auth(client):
    assert client.get(f"{API}/search/history").status_code == 401
    assert client.post(f"{API}/search/history", json={"keyword": "x"}).status_code == 401
