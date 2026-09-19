# -*- coding: utf-8 -*-
"""B21 首页接口 + B22 搜索历史 —— 真实依赖集成测试。

与 `conftest.py` 的约定一致：进程内 `TestClient` 直连**真实数据库**（不做任何
mock），环境不可用时整体 skip。所有写入的数据都在用例结束前清理，不留残留；
搜索历史用**独立探针账号**，避免污染 `test1`/`test2` 的真实历史。
"""
from __future__ import annotations

import pytest

from app.db import cpp_bridge

BANNERS = "/api/v1/home/banners"
FEED = "/api/v1/home/feed"
HISTORY = "/api/v1/search/history"

#: 独立的探针账号（openid 前缀与 conftest 的临时管理员保持同一约定）
_PROBE_A = "pytest_b21b22_a"
_PROBE_B = "pytest_b21b22_b"


# ------------------------------------------------------------------ 夹具 ----

def _insert_banner(title: str, sort: int, *, enabled: int = 1,
                   start: str | None = None, end: str | None = None) -> int:
    """直接建一条轮播。

    走 SQL 而不是管理端接口：轮播的增删改属于 C24 的管理端范围，
    本文件只验证 **B21 的读取口径**，不该顺带把未交付的写接口当作依赖。
    """
    fields = ["title", "image", "link_type", "link_target", "sort", "enabled"]
    values: list = [title, "/static/images/probe.png", "page", "/pages/probe/index", sort, enabled]
    if start is not None:
        fields.append("start_at")
        values.append(start)
    if end is not None:
        fields.append("end_at")
        values.append(end)
    placeholders = ",".join("?" for _ in fields)
    affected, new_id = cpp_bridge.execute(
        f"INSERT INTO home_banner ({','.join(fields)}) VALUES ({placeholders})", values
    )
    assert affected == 1, "探针轮播写入失败"
    return int(new_id)


def _insert_topic(author_id: int, title: str, *, audit_status: int,
                  view_count: int = 0) -> int:
    """建一条 `is_hot = 1` 的帖子；`view_count` 给大值以保证排进热榜前 50。"""
    affected, new_id = cpp_bridge.execute(
        "INSERT INTO topic (author_id, title, content, category, audit_status, "
        "is_hot, status, is_deleted, view_count) VALUES (?,?,?,?,?,1,0,0,?)",
        [author_id, title, "探针内容", "学习", audit_status, view_count],
    )
    assert affected == 1, "探针帖子写入失败"
    return int(new_id)


@pytest.fixture
def cleanup_banners():
    """收集本用例创建的轮播 id，结束后删除（**不碰**种子数据与别人的数据）。"""
    created: list[int] = []
    yield created
    if created:
        placeholders = ",".join("?" for _ in created)
        cpp_bridge.execute(f"DELETE FROM home_banner WHERE id IN ({placeholders})", created)


def _probe_headers(client, code: str) -> dict:
    resp = client.post("/api/v1/auth/wechat-login", json={"code": code})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    return {"Authorization": f"Bearer {data['token']}"}


@pytest.fixture(scope="module")
def hdr_probe_a(client) -> dict:
    """探针账号 A；结束后连同它的搜索历史一起删除。"""
    resp = client.post("/api/v1/auth/wechat-login", json={"code": _PROBE_A})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    uid = int(data["user"]["id"])
    try:
        yield {"Authorization": f"Bearer {data['token']}"}
    finally:
        cpp_bridge.execute("DELETE FROM user_search_history WHERE user_id = ?", [uid])
        cpp_bridge.execute("DELETE FROM user WHERE id = ?", [uid])


@pytest.fixture(scope="module")
def hdr_probe_b(client) -> dict:
    resp = client.post("/api/v1/auth/wechat-login", json={"code": _PROBE_B})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    uid = int(data["user"]["id"])
    try:
        yield {"Authorization": f"Bearer {data['token']}"}
    finally:
        cpp_bridge.execute("DELETE FROM user_search_history WHERE user_id = ?", [uid])
        cpp_bridge.execute("DELETE FROM user WHERE id = ?", [uid])


@pytest.fixture(autouse=True)
def _clean_probe_history(hdr_probe_a, hdr_probe_b, client):
    """每条用例前清空探针账号的历史，用例之间互不影响。"""
    for h in (hdr_probe_a, hdr_probe_b):
        client.delete(HISTORY, headers=h)
    yield


# ====================================================== B21 · /home/banners ====

def test_banners_returns_only_enabled_and_in_window(client, hdr_a, cleanup_banners):
    """三条探针：启用中的应出现；停用的、已过期的、未生效的都不该出现。"""
    active = _insert_banner("B21探针-启用中", 900)
    disabled = _insert_banner("B21探针-已停用", 901, enabled=0)
    expired = _insert_banner("B21探针-已过期", 902, end="2020-01-01 00:00:00")
    future = _insert_banner("B21探针-未生效", 903, start="2099-01-01 00:00:00")
    cleanup_banners.extend([active, disabled, expired, future])

    ids = {i["id"] for i in client.get(BANNERS, headers=hdr_a).json()["data"]["items"]}
    assert active in ids
    assert disabled not in ids
    assert expired not in ids
    assert future not in ids


def test_banners_sorted_by_sort_desc(client, hdr_a, cleanup_banners):
    """`sort` 越大越靠前（与 15_home_banner.sql 的约定一致）。"""
    low = _insert_banner("B21探针-低优先级", 910)
    high = _insert_banner("B21探针-高优先级", 920)
    cleanup_banners.extend([low, high])

    items = client.get(BANNERS, headers=hdr_a).json()["data"]["items"]
    order = [i["id"] for i in items]
    assert order.index(high) < order.index(low)


def test_banners_uses_authoritative_columns(client, hdr_a, cleanup_banners):
    """输出字段 = C46 权威列名，且不再有旧别名与 `columns` 探测字段。"""
    bid = _insert_banner("B21探针-字段形状", 930)
    cleanup_banners.append(bid)

    data = client.get(BANNERS, headers=hdr_a).json()["data"]
    item = next(i for i in data["items"] if i["id"] == bid)
    assert {"image", "link_type", "link_target", "sort", "title"} <= set(item)
    assert not ({"image_url", "link_url"} & set(item)), "旧别名字段不该再出现"
    assert "columns" not in data, "information_schema 探测已随 C46 收敛删除"


def test_banners_null_times_are_json_null(client, hdr_a, cleanup_banners):
    """库里的 NULL 必须是 JSON `null`，不能变成空串 `""`。

    C++ DAO 把 SQL NULL 读成空串，路由侧做了归一化 —— 这条用例钉住它：
    `start_at`/`end_at` 为 NULL 表示"不限制"，前端要靠 `null` 判断。
    """
    bid = _insert_banner("B21探针-空时间", 940)
    cleanup_banners.append(bid)

    items = client.get(BANNERS, headers=hdr_a).json()["data"]["items"]
    item = next(i for i in items if i["id"] == bid)
    assert item["start_at"] is None
    assert item["end_at"] is None


def test_banners_limit_is_honoured(client, hdr_a, cleanup_banners):
    for n in range(3):
        cleanup_banners.append(_insert_banner(f"B21探针-limit{n}", 950 + n))

    data = client.get(f"{BANNERS}?limit=2", headers=hdr_a).json()["data"]
    assert len(data["items"]) == 2


def test_banners_requires_auth(client):
    assert client.get(BANNERS).status_code == 401


# ======================================================== B21 · /home/feed ====

def test_feed_rejects_bad_sort_with_contract_code(client, hdr_a):
    """非法 sort 必须是契约错误 1001，**不能**是 FastAPI 的 422。"""
    resp = client.get(f"{FEED}?sort=whatever", headers=hdr_a)
    assert resp.status_code == 400
    assert resp.json()["code"] == 1001


def test_feed_hot_pagination_guard(client, hdr_a):
    """热榜单次上限 50 条，越界翻页返回 1001 而不是静默空页。"""
    resp = client.get(f"{FEED}?sort=hot&page=3&size=20", headers=hdr_a)
    assert resp.status_code == 400
    assert resp.json()["code"] == 1001


def test_feed_hot_first_page_is_paged(client, hdr_a):
    resp = client.get(f"{FEED}?sort=hot&page=1&size=3", headers=hdr_a)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert set(data) == {"items", "total", "page", "size"}
    for item in data["items"]:
        # C++ 层的数值列读出来是字符串，路由必须归一化后再返回（否则前端 `===` 会踩坑）
        assert isinstance(item["id"], int), f"id 应是数字，实际 {item['id']!r}"


def test_feed_recommend_is_paged(client, hdr_a):
    resp = client.get(f"{FEED}?sort=recommend&page=1&size=5", headers=hdr_a)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert set(data) == {"items", "total", "page", "size"}
    assert len(data["items"]) <= 5


def test_feed_requires_auth(client):
    assert client.get(FEED).status_code == 401


def test_feed_hot_excludes_unapproved_topics(client, hdr_a, user_a):
    """热榜安全兜底：未过审的帖子即使被打了热度标，也不能出现在首页。

    `is_hot` 是**人工热度标**，与审核状态是两条独立链路；`ForumDAO.hot_topics` 的
    SQL 只过滤 `is_hot / status / is_deleted`，**不过滤 `audit_status`** —— 所以路由
    层必须按 id 复核一遍。这里同时给出**正对照**（已过审的那条必须出现）：
    否则"未过审的不出现"可能只是因为热榜压根没返回任何条目，断言就恒真了。
    """
    uid = int(user_a["user"]["id"])
    approved = _insert_topic(uid, "B21探针-已过审热帖", audit_status=1, view_count=9999)
    rejected = _insert_topic(uid, "B21探针-未过审热帖", audit_status=2, view_count=9998)
    try:
        resp = client.get(f"{FEED}?sort=hot&page=1&size=50", headers=hdr_a)
        assert resp.status_code == 200
        ids = {i["id"] for i in resp.json()["data"]["items"]}
        assert approved in ids, "正对照失败：已过审热帖本该出现（否则本用例恒真）"
        assert rejected not in ids, "未过审的帖子泄漏到了首页热榜"
    finally:
        cpp_bridge.execute("DELETE FROM topic WHERE id IN (?, ?)", [approved, rejected])


# ====================================================== B22 · 搜索历史 ====

def test_history_add_then_dedup_same_row(client, hdr_probe_a):
    """同人同词只留一行：第二次写入 dedup=true 且 id 不变（DAO 的 ODKU 生效）。"""
    first = client.post(HISTORY, headers=hdr_probe_a, json={"keyword": "图书馆"}).json()["data"]
    second = client.post(HISTORY, headers=hdr_probe_a, json={"keyword": "图书馆"}).json()["data"]
    assert first["dedup"] is False
    assert second["dedup"] is True
    assert first["id"] == second["id"]

    data = client.get(HISTORY, headers=hdr_probe_a).json()["data"]
    assert data["total"] == 1
    assert len(data["items"]) == 1


def test_history_strips_whitespace(client, hdr_probe_a):
    data = client.post(HISTORY, headers=hdr_probe_a, json={"keyword": "  高数  "}).json()["data"]
    assert data["keyword"] == "高数"


def test_history_rejects_blank_keyword(client, hdr_probe_a):
    for raw in ("", "   ", "\t\n"):
        resp = client.post(HISTORY, headers=hdr_probe_a, json={"keyword": raw})
        assert resp.status_code == 400
        assert resp.json()["code"] == 1001


def test_history_keyword_length_boundary(client, hdr_probe_a):
    """边界跟着 DDL 走：`VARCHAR(128)` → 128 字通过、129 字拒绝。

    （旧实现按 `VARCHAR(64)` 校验，DDL 收敛到 128 后两边本应一致，
    这里把边界钉死，避免以后又漂回去。）
    """
    ok = client.post(HISTORY, headers=hdr_probe_a, json={"keyword": "字" * 128})
    assert ok.status_code == 200
    assert ok.json()["data"]["keyword"] == "字" * 128

    too_long = client.post(HISTORY, headers=hdr_probe_a, json={"keyword": "字" * 129})
    assert too_long.status_code == 400
    assert too_long.json()["code"] == 1001


def test_history_list_is_desc_and_reports_total(client, hdr_probe_a):
    for word in ("甲", "乙", "丙"):
        client.post(HISTORY, headers=hdr_probe_a, json={"keyword": word})

    data = client.get(f"{HISTORY}?limit=10", headers=hdr_probe_a).json()["data"]
    assert data["total"] == 3
    assert len(data["items"]) == 3
    # 最近写入的排最前（同一秒内由 id DESC 兜底）
    assert data["items"][0]["keyword"] == "丙"


def test_history_delete_only_own_rows(client, hdr_probe_a, hdr_probe_b):
    """删别人的记录：返回 1001，且**那条记录必须还在**（真越权验证）。"""
    mine = client.post(HISTORY, headers=hdr_probe_a, json={"keyword": "我的词"}).json()["data"]
    theirs = client.post(HISTORY, headers=hdr_probe_b, json={"keyword": "别人的词"}).json()["data"]

    resp = client.delete(f"{HISTORY}/{theirs['id']}", headers=hdr_probe_a)
    assert resp.status_code == 400
    assert resp.json()["code"] == 1001

    still_there = client.get(HISTORY, headers=hdr_probe_b).json()["data"]
    assert [i["id"] for i in still_there["items"]] == [theirs["id"]]

    # 删自己的正常
    assert client.delete(f"{HISTORY}/{mine['id']}", headers=hdr_probe_a).status_code == 200


def test_history_delete_missing_id_is_1001(client, hdr_probe_a):
    resp = client.delete(f"{HISTORY}/99999999", headers=hdr_probe_a)
    assert resp.status_code == 400
    assert resp.json()["code"] == 1001


def test_history_clear_only_affects_self(client, hdr_probe_a, hdr_probe_b):
    client.post(HISTORY, headers=hdr_probe_a, json={"keyword": "待清空A"})
    client.post(HISTORY, headers=hdr_probe_b, json={"keyword": "待清空B"})

    cleared = client.delete(HISTORY, headers=hdr_probe_a).json()["data"]
    assert cleared["deleted"] >= 1
    assert client.get(HISTORY, headers=hdr_probe_a).json()["data"]["total"] == 0
    # B 的历史不受影响
    assert client.get(HISTORY, headers=hdr_probe_b).json()["data"]["total"] == 1


def test_history_is_trimmed_to_max_keep(client, hdr_probe_a):
    """写入后自动裁剪到最近 50 条（防历史无限增长，契约里的硬要求）。"""
    for n in range(54):
        client.post(HISTORY, headers=hdr_probe_a, json={"keyword": f"批量词{n:03d}"})
    client.post(HISTORY, headers=hdr_probe_a, json={"keyword": "最后一条"})

    data = client.get(f"{HISTORY}?limit=100", headers=hdr_probe_a).json()["data"]
    assert data["total"] == 50
    assert len(data["items"]) == 50
    assert data["items"][0]["keyword"] == "最后一条"      # 最新的没被裁掉


def test_history_requires_auth(client):
    assert client.get(HISTORY).status_code == 401
    assert client.post(HISTORY, json={"keyword": "x"}).status_code == 401
    assert client.delete(HISTORY).status_code == 401
