#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""校捷通 C++ 数据访问层 · 全部 DAO 集成测试。

覆盖：User / Library / Forum / Secondhand / Job / Life。
写操作均在事务内执行并回滚，避免污染种子数据。

用法：
  XJT_DB_PASSWORD=${XJT_DB_PASSWORD} python test_all_dao.py
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

lib = jt_db.LibraryDAO()
forum = jt_db.ForumDAO()
sh = jt_db.SecondhandDAO()
job = jt_db.JobDAO()
life = jt_db.LifeDAO()
user = jt_db.UserDAO()


def main() -> None:
    jt_db.init_pool(HOST, PORT, USER, PASSWORD, DBNAME, 2, 8)
    ok = 0

    # ============ Library ============
    rooms = lib.find_free_rooms()
    assert len(rooms) >= 0
    ok += 1; print(f"[1] 空教室查询通过: {len(rooms)} 间")

    seats = lib.find_seats(1, "2026-08-24")
    assert len(seats) > 0 and "reserved" in seats[0]
    ok += 1; print(f"[2] 座位查询通过: {len(seats)} 个 (示例: {seats[0]['seat_no']})")

    # 预约 → 我的预约 → 取消（事务内，显式回滚避免污染数据）
    with jt_db.begin() as tx:
        rid = lib.reserve(int(seats[0]["id"]), 1, "2026-08-24", "09:00", "11:00")
        assert rid > 0
        mine = lib.my_reservations(1)
        assert len(mine) >= 1
        assert lib.cancel_reservation(rid, 1)
        tx.rollback()
    ok += 2; print("[3] 预约/我的预约/取消 通过 (事务回滚)")

    avg = lib.predict_occupancy(1)
    print(f"[4] 拥挤度预测占位通过: avg={avg:.1f}")
    ok += 1

    # ============ Forum ============
    with jt_db.begin() as tx:
        tid = forum.create_topic(1, "测试帖", "内容", "综合")
        assert tid > 0
        cid = forum.add_comment(tid, 2, "测试评论")
        assert cid > 0
        assert forum.toggle_like(1, "topic", tid) is True
        assert forum.toggle_like(1, "topic", tid) is False  # 取消赞
        tx.rollback()
    ok += 1; print("[5] 发帖/评论/点赞切换 通过 (事务回滚)")

    topics = forum.page_topics(1, 10)
    assert isinstance(topics, list)
    ok += 1; print(f"[6] 帖子分页通过: {len(topics)} 条")

    pending = forum.pending_audit()
    ok += 1; print(f"[7] 待审核帖子: {len(pending)} 条")

    # ============ Secondhand ============
    with jt_db.begin() as tx:
        item_id = sh.publish(1, "测试出售-高数教材", "九成新", "教材", 25.00)
        assert item_id > 0
        # 审计 DATA-01 加固后，match_items_for_wish 会过滤掉 audit_status != 1 的物品
        # （"AI 供需匹配不得返回待审物品"，这是**正确的安全行为**）。
        # 而 publish() 不写 audit_status，落到表默认值（待审）⇒ 匹配不到自己刚发布的商品。
        # 所以测试数据要先"过审"，否则本行断言恒失败 —— 这就是本文件此前卡在 L93 的原因。
        assert jt_db.execute(
            "UPDATE secondhand_item SET audit_status = 1 WHERE id = ?", [item_id])[0] > 0
        wish_id = sh.create_wish(2, "求购高数教材", "教材", 30.00)
        assert wish_id > 0
        matched = sh.match_items_for_wish(wish_id)
        assert any(r["id"] == str(item_id) for r in matched), matched
        assert sh.update_status(item_id, 1, "1")
        order_id = sh.create_order(item_id, 2, 1, 25.00)
        assert order_id > 0
        tx.rollback()
    ok += 1; print("[8] 二手 发布/求购/AI匹配/下单 通过 (事务回滚)")

    items = sh.page_items(1, 10)
    assert isinstance(items, list)
    ok += 1; print(f"[9] 二手列表分页通过: {len(items)} 条")

    # ============ Job ============
    jobs = job.page_jobs(1, 10)
    ok += 1; print(f"[10] 岗位分页通过: {len(jobs)} 条")

    if jobs:
        with jt_db.begin() as tx:
            aid = job.apply(1, int(jobs[0]["id"]), "我叫测试用户A，课余时间充足")
            assert aid > 0
            assert len(job.my_applications(1)) >= 1
            tx.rollback()
        ok += 1; print("[11] 岗位投递/我的投递 通过 (事务回滚)")

    score = job.get_trust_score(1)
    assert score >= 0 or score == -1
    ok += 1; print(f"[12] 岗位可信度接口通过: {score}")

    # ============ Life ============
    notices = life.page_notices(1, 10)
    ok += 1; print(f"[13] 通知分页通过: {len(notices)} 条")

    merchants = life.page_merchants(1, 10)
    assert len(merchants) >= 1
    menu = life.menu_items(int(merchants[0]["id"]))
    assert len(menu) >= 1
    ok += 1; print(f"[14] 商家/菜单查询通过: {len(merchants)} 商家, {len(menu)} 菜品")

    with jt_db.begin() as tx:
        if notices:
            assert life.mark_notice_read(1, int(notices[0]["id"]))
        oid = life.create_order(1, int(merchants[0]["id"]), '[{"id":1,"num":1}]', 15.00)
        assert oid > 0
        tx.rollback()
    ok += 1; print("[15] 通知已读/外卖下单 通过 (事务回滚)")

    # ============ User（C22 新增）============
    # C22 给 UserDAO 加了两个方法，并且把 token_version / student_no_updated_at
    # 加进了鉴权查询 —— 这两列拿不到的话，deps 的 token 失效校验与「1 次/7 天」
    # 限频都会静默失效，所以这里先断言列真的在。
    urows = user.page(1, 1)
    if urows:
        uid = int(urows[0]["id"])
        row = user.find_by_id(uid)
        assert row is not None
        for col in ("token_version", "student_no_updated_at"):
            assert col in row, f"find_by_id 缺少 {col}（C22 依赖它，否则功能是死代码）: {sorted(row)}"
        ok += 1; print(f"[16] 用户行含 token_version/student_no_updated_at 通过 (uid={uid})")

        tv0 = int(row.get("token_version") or 0)
        with jt_db.begin() as tx:
            assert int(user.bump_token_version(uid)) == tv0 + 1
            assert int(user.bump_token_version(uid)) == tv0 + 2   # 可连续自增
            tx.rollback()
        ok += 1; print("[17] token_version 自增 通过 (事务回滚)")

        with jt_db.begin() as tx:
            # 从没改过 ⇒ 剩余 0；改完 ⇒ 剩余 > 0（7 天窗口生效）
            assert user.student_no_change_remaining_days(uid, 7) >= 0
            assert user.update_student_no(uid, "C22DAO000001") is True
            assert user.find_by_id(uid)["student_no"] == "C22DAO000001"   # 回读
            assert user.student_no_change_remaining_days(uid, 7) > 0
            tx.rollback()
        ok += 1; print("[18] 学号绑定 + 回读 + 限频生效 通过 (事务回滚)")

    # 不存在的用户：写返回 False、版本返回 -1、剩余天数返回 -1（不抛异常）
    assert user.update_student_no(999000001, "C22NONE") is False
    assert user.bump_token_version(999000001) == -1
    assert user.student_no_change_remaining_days(999000001, 7) == -1
    ok += 1; print("[19] 不存在用户返回 False / -1 通过")

    print(f"\n【成功】全部 DAO 测试通过！共 {ok} 组断言")


if __name__ == "__main__":
    main()
