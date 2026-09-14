"""B18 分层推送调度：数据库集成用例（D-7 触达 + 幂等 + 私密行不泄漏 + 回收）。

依赖真实数据库（`client` 夹具已判定可用性，不可用则整体 skip）。
管理端接口一律用 ``hdr_admin``（独立管理员夹具，不依赖 user_a 的角色）。
用例自建自清：临时 reminder / 对照通知与其推送行在 ``finally`` 中删除。

⚠️ **关于「零泄漏」断言的反向对照（PR #60 审查 P1）**

``/life/notices`` 返回的行里**没有** ``target_grade`` 字段（C++ DAO 的 SELECT 不含该列），
所以「断言返回行里没有 __push: 前缀」是**恒真空**断言——永远不会失败。
正确做法是三条联合验证：

1. 该私密行**确实存在**（查库 ``WHERE id = ? AND target_grade LIKE '__push:%'``）；
2. 它的 id **不在**公共列表 ``/life/notices`` 中；
3. **反向对照**：临时插一条公共通知（``target_grade=''``），断言它**在**公共列表中
   ——证明 ② 的"不在"不是"接口返回空"造成的假通过。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.db import cpp_bridge
from app.services import notice_scheduler

_PUBLIC_CONTROL_TITLE = "pytest 反向对照·公共通知"


def _create_reminder(client, hdr: dict, content: str, remind_at: str) -> int:
    resp = client.post(
        "/api/v1/agent/reminders",
        json={"content": content, "remind_at": remind_at},
        headers=hdr,
    )
    assert resp.status_code == 200, resp.text
    return int(resp.json()["data"]["reminder_id"])


def _dispatch(client, hdr_admin: dict, **payload) -> dict:
    resp = client.post(
        "/api/v1/admin/notices/dispatch",
        json={"async": False, **payload},
        headers=hdr_admin,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _list_ids(client, hdr: dict, path: str) -> set[int]:
    resp = client.get(path, headers=hdr)
    assert resp.status_code == 200, resp.text
    return {int(i["id"]) for i in resp.json()["data"]["items"]}


def _insert_public_control_notice() -> int:
    """插一条**公共**通知作反向对照，返回其 id（调用方负责删除）。"""
    _, notice_id = cpp_bridge.execute(
        "INSERT INTO campus_notice (title, content, source, category, target_grade) "
        "VALUES (?, ?, ?, ?, '')",
        [_PUBLIC_CONTROL_TITLE, "用于证明公共列表确实返回数据", "pytest", "测试"],
    )
    return int(notice_id)


def test_dispatch_d7_push_is_idempotent_and_recoverable(client, hdr_a, hdr_admin, user_a):
    uid = int(user_a["user"]["id"])
    remind_at = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d 18:00:00")
    rid = _create_reminder(client, hdr_a, "考研报名截止（pytest 用例）", remind_at)
    control_id = _insert_public_control_notice()
    push_notice_id = 0
    try:
        # 1) 预演：应命中 D7 且不写库
        dry = _dispatch(client, hdr_admin, dry_run=True, user_id=uid)
        mine = [p for p in dry["pushed"] if p["ref_id"] == rid and p["kind"] == "reminder"]
        assert mine and mine[0]["stage"] == "D7"
        assert mine[0]["days_left"] == 7
        assert cpp_bridge.query(
            "SELECT id FROM campus_notice WHERE target_grade = ?",
            [notice_scheduler._marker("reminder", rid, "D7")],
        ) == []

        # 2) 正式调度：生成推送通知行 + 投递记录
        real = _dispatch(client, hdr_admin, user_id=uid)
        hit = [p for p in real["pushed"] if p["ref_id"] == rid and p["kind"] == "reminder"]
        assert hit, real
        push_notice_id = int(hit[0]["notice_id"])
        assert hit[0]["delivery_id"] > 0

        rows = cpp_bridge.query(
            "SELECT user_id, is_read, channel, score, reason FROM notice_delivery WHERE id = ?",
            [hit[0]["delivery_id"]],
        )
        assert rows and int(rows[0]["user_id"]) == uid and int(rows[0]["is_read"]) == 0
        assert float(rows[0]["score"]) > 0 and str(rows[0]["reason"])
        assert "还有 7 天" in str(rows[0]["reason"])

        # 3) 幂等：重复调度不再重复推送
        again = _dispatch(client, hdr_admin, user_id=uid)
        assert not [p for p in again["pushed"] if p["ref_id"] == rid]
        assert again["skipped"]["already_pushed"] >= 1

        # 4) 本人可见（未读列表含该 id）
        unread_ids = _list_ids(client, hdr_a, "/api/v1/life/notices/unread?page=1&size=100")
        assert push_notice_id in unread_ids, "推送未出现在本人未读列表"

        # 5) 私密行不泄漏到公共口径（三条联合验证，见模块 docstring）
        exists = cpp_bridge.query(
            "SELECT id FROM campus_notice WHERE id = ? AND target_grade LIKE ?",
            [push_notice_id, f"{notice_scheduler.PRIVATE_TARGET_PREFIX}%"],
        )
        assert exists, "私密推送行不存在（泄漏断言会假通过）"
        public_ids = _list_ids(client, hdr_a, "/api/v1/life/notices?page=1&size=100")
        assert push_notice_id not in public_ids, "私密推送行泄漏到公共通知列表 /life/notices"
        assert control_id in public_ids, "反向对照通知未出现在公共列表（该断言可能假通过）"

        # 6) 管理端只读一览能看到"已推送"标记
        pending = client.get("/api/v1/admin/notices/pending?limit=200", headers=hdr_admin)
        assert pending.status_code == 200, pending.text
        mine_pending = [i for i in pending.json()["data"]["items"] if i.get("ref_id") == rid]
        assert mine_pending and mine_pending[0]["pushed"] is True
    finally:
        notice_scheduler.purge_private(kind="reminder", ref_id=rid)
        cpp_bridge.execute("DELETE FROM reminder WHERE id = ?", [rid])
        cpp_bridge.execute("DELETE FROM campus_notice WHERE id = ?", [control_id])


def test_dispatch_requires_admin(client, user_b, hdr_b):
    """管理端调度接口必须有管理员鉴权（反向对照：普通用户被拒）。

    契约：``err_forbidden`` = HTTP 403 + body ``code=2003``（见 core/response.py）。
    用 ``user_b``（普通账号）而非 ``user_a``——后者在本机恰好是管理员，用它做越权
    断言会变成"恒真空"（请求成功 → 断言失败，或反过来靠环境侥幸通过）。
    """
    if int(user_b["user"].get("role") or 0) == 1:
        pytest.skip("测试账号 test2 也是管理员，无法验证越权拒绝")

    resp = client.post(
        "/api/v1/admin/notices/dispatch", json={"async": False, "dry_run": True}, headers=hdr_b
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == 2003, resp.text


def test_dispatch_skips_items_far_from_deadline(client, hdr_a, hdr_admin, user_a):
    """距到期 30 天的待办不应被推送（未进入 7 天窗口）。"""
    uid = int(user_a["user"]["id"])
    remind_at = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d 18:00:00")
    rid = _create_reminder(client, hdr_a, "远期待办（pytest 用例）", remind_at)
    try:
        result = _dispatch(client, hdr_admin, user_id=uid)
        assert not [p for p in result["pushed"] if p["ref_id"] == rid]
        assert result["skipped"]["not_due"] >= 1
    finally:
        cpp_bridge.execute("DELETE FROM reminder WHERE id = ?", [rid])


def test_purge_private_deletes_notice_and_delivery(client, hdr_a, hdr_admin, user_a):
    """回收接口：真实删除私密推送行并连带清掉投递记录（非空断言）。"""
    uid = int(user_a["user"]["id"])
    remind_at = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d 18:00:00")
    rid = _create_reminder(client, hdr_a, "回收用例（pytest）", remind_at)
    try:
        real = _dispatch(client, hdr_admin, user_id=uid)
        hit = [p for p in real["pushed"] if p["ref_id"] == rid]
        assert hit, "未产生推送行，无法验证回收"
        notice_id = int(hit[0]["notice_id"])
        delivery_id = int(hit[0]["delivery_id"])

        resp = client.post(
            "/api/v1/admin/notices/purge-private",
            json={"kind": "reminder", "ref_id": rid},
            headers=hdr_admin,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["notices"] >= 1, "未删除任何推送通知行"
        assert data["deliveries"] >= 1, "未删除任何投递记录"
        assert cpp_bridge.query("SELECT id FROM campus_notice WHERE id = ?", [notice_id]) == []
        assert cpp_bridge.query("SELECT id FROM notice_delivery WHERE id = ?", [delivery_id]) == []
    finally:
        notice_scheduler.purge_private(kind="reminder", ref_id=rid)
        cpp_bridge.execute("DELETE FROM reminder WHERE id = ?", [rid])
