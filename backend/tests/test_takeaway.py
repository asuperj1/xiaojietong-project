"""B31 代收业务闭环契约用例：下单 → 6 位取件码 → 到件 → 站内通知。

对应任务单 §3.2 **B31** / §7.1 代收边界：取件码 **6 位** · 驿站**单选** · 到件**复用站内通知**
（`notice_delivery`）· **不计费** · 核销留待下期；并且**代买入口必须已下线**（按路径断言 404）。

用例复用 conftest 的会话级夹具（`hdr_a` / `hdr_b` / `hdr_admin` / `user_a`），**不额外登录**
（`/auth/wechat-login` 有 SEC-10 限流 10 次/分钟）；自建订单在结束时连同到件通知与投递记录
一起删除 —— 否则会给 `test1` 留下未读通知，污染其它用例的未读断言。
"""

from __future__ import annotations

import re

import pytest

from app.db import cpp_bridge
from app.services.notice_scheduler import PRIVATE_TARGET_PREFIX

pytestmark = pytest.mark.integration

_CODE_RE = re.compile(r"^\d{6}$")


def _marker(order_id: int) -> str:
    """到件通知的私密标记（与 notice_scheduler._marker 同格式）。"""
    return f"{PRIVATE_TARGET_PREFIX}takeaway:{order_id}:arrived"


def _point_id(client, hdr) -> int:
    items = client.get("/api/v1/life/pickup-points", headers=hdr).json()["data"]["items"]
    assert items, "应有启用的驿站（种子见 db/sql/18_takeaway_pickup.sql）"
    return int(items[0]["id"])


@pytest.fixture()
def orders(client, hdr_a):
    """订单工厂（可指定请求方）+ 清理：连到件通知与投递记录一起删干净。"""
    created: list[int] = []

    def make(hdr=None, **body) -> dict:
        who = hdr or hdr_a
        payload = {"pickup_point_id": _point_id(client, who)}
        payload.update(body)
        resp = client.post("/api/v1/life/orders", headers=who, json=payload)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        created.append(int(data["order_id"]))
        return data

    try:
        yield make
    finally:
        for oid in created:
            marker = _marker(oid)
            cpp_bridge.execute(
                "DELETE FROM `notice_delivery` WHERE `notice_id` IN "
                "(SELECT `id` FROM `campus_notice` WHERE `target_grade` = ?)",
                [marker],
            )
            cpp_bridge.execute("DELETE FROM `campus_notice` WHERE `target_grade` = ?", [marker])
            cpp_bridge.execute("DELETE FROM `takeaway_order` WHERE `id` = ?", [oid])


# ------------------------------------------------------------ 驿站 / 下单 ----

def test_pickup_points_listed_sorted_desc(client, hdr_a):
    """驿站列表：仅启用中的，按 `sort` 倒序（前端单选数据源）。"""
    items = client.get("/api/v1/life/pickup-points", headers=hdr_a).json()["data"]["items"]
    assert items, "应有驿站种子"
    sorts = [int(it["sort"]) for it in items]
    assert sorts == sorted(sorts, reverse=True), "应按 sort 倒序（越大越靠前）"
    for it in items:
        assert it["name"] and it["id"]


def test_create_order_generates_six_digit_code_without_fee(client, hdr_a, orders):
    """下单：**6 位数字取件码** + **不计费** + 详情/列表都能回查（含驿站名）。"""
    first = orders()
    second = orders()

    assert _CODE_RE.match(first["pickup_code"]), f"取件码应为 6 位数字：{first['pickup_code']}"
    assert first["pay_amount"] == 0, "代收不计费"
    assert first["status"] == 1
    assert first["pickup_code"] != second["pickup_code"], "两单取件码不应相同"

    detail = client.get(f"/api/v1/life/orders/{first['order_id']}", headers=hdr_a).json()["data"]
    assert detail["pickup_code"] == first["pickup_code"]
    assert detail["pickup_point_name"], "详情应带驿站名（F19 大字展示取件码 + 驿站信息）"
    assert detail["arrived_at"] == "" and detail["notified_at"] == "", "尚未到件应为空"

    listing = client.get("/api/v1/life/orders?page=1&size=50", headers=hdr_a).json()["data"]
    ids = [int(it["id"]) for it in listing["items"]]
    assert first["order_id"] in ids and second["order_id"] in ids
    assert listing["total"] >= 2


def test_create_order_rejects_bad_pickup_point(client, hdr_a):
    """驿站必须存在且启用；老前端的代买请求体也要落到契约错误（1001），而不是 422。"""
    zero = client.post("/api/v1/life/orders", headers=hdr_a, json={"pickup_point_id": 0})
    assert zero.status_code == 400 and zero.json()["code"] == 1001

    missing = client.post("/api/v1/life/orders", headers=hdr_a, json={"pickup_point_id": 999999})
    assert missing.status_code == 400 and missing.json()["code"] == 1001

    legacy = client.post(
        "/api/v1/life/orders",
        headers=hdr_a,
        json={"merchant_id": 1, "items": [{"id": 1, "num": 1}]},
    )
    assert legacy.status_code == 400 and legacy.json()["code"] == 1001


def test_order_is_isolated_between_users(client, hdr_a, hdr_b, orders):
    """他人订单既不在我的列表里，也读不到详情（越权 1001）。"""
    mine = orders()

    other = client.get("/api/v1/life/orders?page=1&size=50", headers=hdr_b).json()["data"]
    assert mine["order_id"] not in [int(it["id"]) for it in other["items"]]

    resp = client.get(f"/api/v1/life/orders/{mine['order_id']}", headers=hdr_b)
    assert resp.status_code == 400 and resp.json()["code"] == 1001


def test_create_requires_auth(client):
    assert client.post("/api/v1/life/orders", json={"pickup_point_id": 1}).status_code == 401


# --------------------------------------------------------- 到件 → 站内通知 ----

def test_arrive_marks_order_and_pushes_one_notice(client, hdr_a, hdr_admin, user_a, orders):
    """**闭环核心**：到件登记 → 订单时间线更新 + 恰好一条站内通知，且本人能在未读里看到。"""
    order = orders()
    oid = order["order_id"]

    resp = client.post(f"/api/v1/admin/takeaway/orders/{oid}/arrive", headers=hdr_admin)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == 2
    assert resp.json()["data"]["notified"] is True

    detail = client.get(f"/api/v1/life/orders/{oid}", headers=hdr_a).json()["data"]
    assert detail["arrived_at"] and detail["notified_at"], "到件后时间线应有值"

    marker = _marker(oid)
    notices = cpp_bridge.query(
        "SELECT `id`, `title`, `content` FROM `campus_notice` WHERE `target_grade` = ?", [marker]
    )
    assert len(notices) == 1, "到件应恰好生成一条通知行"
    notice_id = int(notices[0]["id"])
    assert order["pickup_code"] in notices[0]["content"], "通知正文应含取件码"

    uid = int(user_a["user"]["id"])
    delivered = cpp_bridge.query(
        "SELECT COUNT(*) AS n FROM `notice_delivery` WHERE `notice_id` = ? AND `user_id` = ?",
        [notice_id, uid],
    )
    assert int(delivered[0]["n"]) == 1, "应恰好投递给下单人一条"

    # **站内通知闭环的证据**：本人未读里能看到它……
    unread = client.get("/api/v1/life/notices/unread?page=1&size=50", headers=hdr_a).json()["data"]
    assert notice_id in [int(it["id"]) for it in unread["items"]], "到件通知应进入本人未读"
    # ……但它属于私密推送，不进公共通知列表（B18 口径）
    public = client.get("/api/v1/life/notices?page=1&size=50", headers=hdr_a).json()["data"]
    assert notice_id not in [int(it["id"]) for it in public["items"]]


def test_arrive_is_idempotent(client, hdr_a, hdr_admin, orders):
    """重复到件：不再重复投递，`arrived_at` 保持首次登记时间。"""
    oid = orders()["order_id"]
    first = client.post(f"/api/v1/admin/takeaway/orders/{oid}/arrive", headers=hdr_admin).json()["data"]
    arrived_at = client.get(f"/api/v1/life/orders/{oid}", headers=hdr_a).json()["data"]["arrived_at"]

    second = client.post(f"/api/v1/admin/takeaway/orders/{oid}/arrive", headers=hdr_admin).json()["data"]
    assert first["notified"] is True
    assert second["notified"] is False, "第二次不应再通知"

    notice_id = int(
        cpp_bridge.query(
            "SELECT `id` FROM `campus_notice` WHERE `target_grade` = ?", [_marker(oid)]
        )[0]["id"]
    )
    n = cpp_bridge.query("SELECT COUNT(*) AS n FROM `notice_delivery` WHERE `notice_id` = ?", [notice_id])
    assert int(n[0]["n"]) == 1, "重复到件不应新增投递记录"
    again = client.get(f"/api/v1/life/orders/{oid}", headers=hdr_a).json()["data"]["arrived_at"]
    assert again == arrived_at


def test_arrive_requires_admin(client, hdr_b, orders):
    """到件登记是运营动作：普通用户 403 / 2003。"""
    oid = orders()["order_id"]
    resp = client.post(f"/api/v1/admin/takeaway/orders/{oid}/arrive", headers=hdr_b)
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == 2003


def test_arrive_unknown_order(client, hdr_admin):
    resp = client.post("/api/v1/admin/takeaway/orders/99999999/arrive", headers=hdr_admin)
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == 1001


def test_arrive_rejects_legacy_biz_type(client, hdr_admin, user_a):
    """历史「代买」订单（biz_type=1）不能走到件登记。"""
    uid = int(user_a["user"]["id"])
    _, oid = cpp_bridge.execute(
        "INSERT INTO `takeaway_order` (`user_id`, `merchant_id`, `items_json`, `status`, "
        "`biz_type`, `pickup_code`) VALUES (?, 1, '[]', 1, 1, '000000')",
        [uid],
    )
    oid = int(oid)
    try:
        resp = client.post(f"/api/v1/admin/takeaway/orders/{oid}/arrive", headers=hdr_admin)
        assert resp.status_code == 400, resp.text
        assert resp.json()["code"] == 3001
    finally:
        cpp_bridge.execute("DELETE FROM `takeaway_order` WHERE `id` = ?", [oid])


# --------------------------------------------------------------- 代买下线 ----

def test_legacy_merchant_routes_are_gone(client, hdr_a):
    """**代买入口已下线**：商家/菜单路由必须 404（B31 验收口径「无代买入口」）。"""
    assert client.get("/api/v1/life/merchants", headers=hdr_a).status_code == 404
    assert client.get("/api/v1/life/merchants/1/menu", headers=hdr_a).status_code == 404
