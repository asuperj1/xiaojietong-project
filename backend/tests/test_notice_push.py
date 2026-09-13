"""B18 分层推送调度：数据库集成用例（D-7 触达 + 幂等 + 私密行不泄漏 + 回收）。

依赖真实数据库（`client` 夹具已判定可用性，不可用则整体 skip）。
用例自建自清：临时 reminder 与其推送行在 finally 中删除，不污染演示数据。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.db import cpp_bridge
from app.services import notice_scheduler


def _create_reminder(client, hdr: dict, content: str, remind_at: str) -> int:
    resp = client.post(
        "/api/v1/agent/reminders",
        json={"content": content, "remind_at": remind_at},
        headers=hdr,
    )
    assert resp.status_code == 200, resp.text
    return int(resp.json()["data"]["reminder_id"])


def test_dispatch_d7_push_is_idempotent_and_recoverable(client, hdr_a, user_a):
    uid = int(user_a["user"]["id"])
    remind_at = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d 18:00:00")
    rid = _create_reminder(client, hdr_a, "考研报名截止（pytest 用例）", remind_at)
    push_notice_id = 0
    try:
        # 1) 预演：应命中 D7 且不写库
        dry = notice_scheduler.dispatch(user_id=uid, dry_run=True)
        mine = [p for p in dry["pushed"] if p["ref_id"] == rid and p["kind"] == "reminder"]
        assert mine and mine[0]["stage"] == "D7"
        assert mine[0]["days_left"] == 7
        assert cpp_bridge.query(
            "SELECT id FROM campus_notice WHERE target_grade = ?",
            [notice_scheduler._marker("reminder", rid, "D7")],
        ) == []

        # 2) 正式调度：生成推送通知行 + 投递记录
        real = notice_scheduler.dispatch(user_id=uid)
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

        # 3) 幂等：重复调度不再重复推送
        again = notice_scheduler.dispatch(user_id=uid)
        assert not [p for p in again["pushed"] if p["ref_id"] == rid]
        assert again["skipped"]["already_pushed"] >= 1

        # 4) 本人可见（未读列表），但公共通知列表不出现（不泄漏给他人）
        unread = client.get("/api/v1/life/notices/unread?page=1&size=100", headers=hdr_a)
        assert unread.status_code == 200, unread.text
        assert any(int(n["id"]) == push_notice_id for n in unread.json()["data"]["items"])

        public = client.get("/api/v1/life/notices?page=1&size=100", headers=hdr_a)
        assert public.status_code == 200, public.text
        for item in public.json()["data"]["items"]:
            assert not notice_scheduler.is_private_audience(item.get("target_grade"))

        # 5) 管理端只读一览能看到"已推送"标记
        pending = client.get("/api/v1/admin/notices/pending?limit=200", headers=hdr_a)
        assert pending.status_code == 200, pending.text
        mine_pending = [i for i in pending.json()["data"]["items"] if i.get("ref_id") == rid]
        assert mine_pending and mine_pending[0]["pushed"] is True
    finally:
        notice_scheduler.purge_private(kind="reminder", ref_id=rid)
        cpp_bridge.execute("DELETE FROM reminder WHERE id = ?", [rid])


def test_dispatch_skips_items_far_from_deadline(client, hdr_a, user_a):
    """距到期 30 天的待办不应被推送（未进入 7 天窗口）。"""
    uid = int(user_a["user"]["id"])
    remind_at = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d 18:00:00")
    rid = _create_reminder(client, hdr_a, "远期待办（pytest 用例）", remind_at)
    try:
        result = notice_scheduler.dispatch(user_id=uid)
        assert not [p for p in result["pushed"] if p["ref_id"] == rid]
        assert result["skipped"]["not_due"] >= 1
    finally:
        cpp_bridge.execute("DELETE FROM reminder WHERE id = ?", [rid])


def test_purge_private_reports_counts(client, hdr_a, user_a):
    """回收接口：删除私密推送行后，投递记录一并清掉。"""
    assert notice_scheduler.purge_private(kind="reminder")["notices"] >= 0
    resp = client.post(
        "/api/v1/admin/notices/purge-private", json={"kind": "notice"}, headers=hdr_a
    )
    assert resp.status_code == 200 and resp.json()["code"] == 0
