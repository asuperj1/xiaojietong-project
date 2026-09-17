#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""校捷通 C++ 数据访问层 · 全部 DAO 集成测试。

覆盖：User（含 C22 学号/token · C23 搜索历史）/ Home（C24 轮播）/ Library / Forum /
Secondhand / Job / Life（含 C25 代收闭环）。
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
home = jt_db.HomeDAO()


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

    # ============ User · 搜索历史（C23）============
    urows = user.page(1, 1)
    assert urows, "user 表无数据，无法验证搜索历史"
    uid = int(urows[0]["id"])

    with jt_db.begin() as tx:
        hid = user.add_search_history(uid, "测试-C23-高数")
        assert hid > 0, "add_search_history 未返回行 id"
        # 去重：带空格的同词再写一次 ⇒ 不新增行、且返回同一 id
        assert int(user.add_search_history(uid, "  测试-C23-高数  ")) == int(hid), \
            "trim 后应命中同一行（去重失效）"
        hid2 = user.add_search_history(uid, "测试-C23-线代")
        assert hid2 > hid, "后写入的行 id 应更大"
        hist = user.list_search_history(uid, 20)
        assert any(int(r["id"]) == int(hid) for r in hist), hist
        assert [int(r["id"]) for r in hist][0] == int(hid2), "最近搜索应排第一"
        # 排序契约是 **(created_at DESC, id DESC)**。原断言只比较 `created_at` 列表是否
        # 倒序 —— 而本用例写入的行**都落在同一秒**，`created_at` 全相等 ⇒ 那个断言**恒真**
        # （评审实测：把 ORDER BY 里的 `id DESC` tie-break 删掉，原断言仍全绿）。
        # 改为断言整表按双键有序。
        #
        # ⚠️ 但它仍**测不出**"删掉 tie-break"这个变异（实测 survived）：`created_at` 相等时,
        #    InnoDB 对 `idx_user_created` 的倒序索引扫描**本身**就返回 id 降序，两种写法
        #    观测结果一致。所以 `id DESC` 是一条**防御性保证**（不依赖存储引擎的自然顺序），
        #    而不是在当前数据上可观测的行为差异 —— 这里如实标注，避免后来者误以为它已被锁住。
        pairs = [(str(r["created_at"]), int(r["id"])) for r in hist]
        assert pairs == sorted(pairs, reverse=True), f"未按 (created_at, id) 双键倒序：{pairs}"
        # 超长关键词：UTF-8 字符截断而非报错（列宽 VARCHAR(128)）
        assert int(user.add_search_history(uid, "测试-C23-" + "长" * 300)) > 0
        # 空 / 纯空白拒绝落库
        assert int(user.add_search_history(uid, "   ")) == -1
        # 单删：拿别人的 user_id 删不掉（防越权）
        assert user.delete_search_history(uid + 99999, int(hid)) is False
        assert user.delete_search_history(uid, int(hid)) is True
        # 清空返回删除行数
        assert int(user.clear_search_history(uid)) >= 1
        tx.rollback()
    left = jt_db.query(
        "SELECT COUNT(*) AS c FROM user_search_history WHERE keyword LIKE '测试-C23-%'")
    assert int(left[0]["c"]) == 0, left
    ok += 1; print("[16] 搜索历史 增(去重)/查(倒序)/单删(防越权)/清空 通过 (事务回滚)")

    # ============ Home · 轮播位（C24）============
    banners = home.list_banners()
    assert len(banners) >= 3, f"种子轮播应有 >= 3 条，实际 {len(banners)}"
    sorts = [int(b["sort"]) for b in banners]
    assert sorts == sorted(sorts, reverse=True), f"未按 sort DESC 排序：{sorts}"
    assert all(int(b["enabled"]) == 1 for b in banners), "默认列表不该含停用轮播"

    with jt_db.begin() as tx:
        # 停用：不出现在前台列表，但管理端列表能看到（反向对照）
        off = home.create_banner("测试-C24-停用", "", "none", "", 5, "", "", 0)
        assert off > 0
        visible = {int(b["id"]) for b in home.list_banners(200)}
        all_ids = {int(b["id"]) for b in home.list_all_banners(200)}
        assert int(off) not in visible, "停用轮播出现在了前台列表"
        assert int(off) in all_ids, "管理端列表应能看到停用轮播（反向对照失败）"

        # 已过期 / 未生效：均不得出现在前台列表，但管理端必须能看到
        # （否则已过期的轮播在后台不可见、无法编辑或重新启用）
        expired = home.create_banner("测试-C24-过期", "", "none", "", 5, "",
                                     "2000-01-01 00:00:00", 1)
        not_yet = home.create_banner("测试-C24-未生效", "", "none", "", 5,
                                     "2099-01-01 00:00:00", "", 1)
        visible = {int(b["id"]) for b in home.list_banners(200)}
        all_ids = {int(b["id"]) for b in home.list_all_banners(200)}
        assert int(expired) not in visible, "已过期轮播出现在了前台列表"
        assert int(not_yet) not in visible, "未生效轮播出现在了前台列表"
        assert int(expired) in all_ids, "管理端应能看到已过期轮播"
        assert int(not_yet) in all_ids, "管理端应能看到未生效轮播"

        # 有效期内 + sort 最大 ⇒ 应排第一
        live = home.create_banner("测试-C24-生效", "", "none", "", 999)
        assert live > 0
        assert int(home.list_banners(200)[0]["id"]) == int(live), \
            "有效期内的最大值应排第一"
        assert home.find_banner(live) is not None

        # 上/下架
        assert home.set_banner_enabled(live, 0) is True
        assert int(live) not in {int(b["id"]) for b in home.list_banners(200)}
        assert home.set_banner_enabled(live, 1) is True
        assert int(live) in {int(b["id"]) for b in home.list_banners(200)}

        # 改（整行）与删
        assert home.update_banner(live, "测试-C24-已改", "", "page", "/pages/x", 3, "", "", 1)
        assert home.find_banner(live)["title"] == "测试-C24-已改"
        assert home.remove_banner(live) is True
        assert home.find_banner(live) is None
        tx.rollback()

    left = jt_db.query(
        "SELECT COUNT(*) AS c FROM home_banner WHERE title LIKE '测试-C24-%'")
    assert int(left[0]["c"]) == 0, left
    ok += 1; print("[17] 轮播 列表(启用/有效期/sort DESC)/增改删 通过 (事务回滚)")

    # ============ Life · 代收闭环（C25）============
    points = life.page_pickup_points()
    assert len(points) >= 3, f"驿站种子应有 >= 3 条，实际 {len(points)}"
    p_sorts = [int(p["sort"]) for p in points]
    assert p_sorts == sorted(p_sorts, reverse=True), f"驿站未按 sort DESC：{p_sorts}"
    codes = {life.generate_pickup_code() for _ in range(5)}
    assert all(len(c) == 6 for c in codes), codes
    assert all(c.isalnum() for c in codes), codes

    with jt_db.begin() as tx:
        # 先记下历史「代买」存量，结尾要证明它没被动过
        legacy_before = int(
            jt_db.query("SELECT COUNT(*) AS c FROM takeaway_order WHERE biz_type = 1")[0]["c"]
        )

        # 驿站：增 → 排序 → 软删（软删后任何列表都查不到）
        pid = life.create_pickup_point("测试-C25-驿站", "某处", "08:00-20:00", "", 999, 1)
        assert pid > 0
        assert int(life.page_pickup_points(False, 200)[0]["id"]) == int(pid), "sort 最大应排第一"
        assert life.find_pickup_point(pid) is not None
        assert life.remove_pickup_point(pid) is True
        assert life.find_pickup_point(pid) is None, "软删后应查不到"
        assert all(int(p["id"]) != int(pid) for p in life.page_pickup_points(True, 200)), \
            "软删的驿站不该出现在任何列表（含 include_disabled）"
        # ⚠️ 上面两条**物理删也能满足** —— 必须直接查库确认是"行still在、is_deleted=1"。
        # 头注释写的「软删：驿站会被历史订单的 pickup_point_id 引用，物理删会让历史订单
        # 查不到驿站」，只有这条断言才真正验证到（评审实测：把 UPDATE ... is_deleted=1
        # 改成 DELETE FROM pickup_point，上面两条断言仍然全绿）。
        _soft = jt_db.query("SELECT is_deleted FROM pickup_point WHERE id = ?", [pid])
        assert _soft, "软删后行不应从表里消失（那是物理删）"
        assert int(_soft[0]["is_deleted"]) == 1, f"应标记 is_deleted=1，实际 {_soft[0]}"

        # 代收下单：biz_type=2 / delivery_fee=0 / 取件码 6 位且可回查
        seed_point = int(points[0]["id"])
        oid = life.create_pickup_order(1, 1, '[{"id":1,"num":1}]', 12.50, seed_point)
        assert oid > 0
        code = str(jt_db.query(
            "SELECT pickup_code FROM takeaway_order WHERE id = ?", [oid])[0]["pickup_code"])
        assert len(code) == 6, f"取件码应为 6 位：{code!r}"
        order = life.find_order_by_pickup_code(code)
        assert order is not None, "取件码应能回查到订单"
        assert int(order["biz_type"]) == 2, f"代收订单 biz_type 应为 2：{order['biz_type']}"
        assert int(order["pickup_point_id"]) == seed_point
        row = jt_db.query(
            "SELECT delivery_fee FROM takeaway_order WHERE id = ?", [oid])[0]
        assert float(row["delivery_fee"]) == 0.0, "代收不计费 ⇒ delivery_fee 必须为 0"

        # 到件：错误取件码必须失败（不能拿别的码标这单）
        assert life.mark_order_arrived(oid, "WRONG9") is False, "错误取件码竟然标记成功"
        assert jt_db.query(
            "SELECT arrived_at FROM takeaway_order WHERE id = ?", [oid])[0]["arrived_at"] == ""
        assert life.mark_order_arrived(oid, code) is True
        arrived1 = jt_db.query(
            "SELECT arrived_at FROM takeaway_order WHERE id = ?", [oid])[0]["arrived_at"]
        assert arrived1, "到件时间应已写入"
        # 幂等：重复到件不得改动已记录的时间
        assert life.mark_order_arrived(oid, code) is True
        arrived2 = jt_db.query(
            "SELECT arrived_at FROM takeaway_order WHERE id = ?", [oid])[0]["arrived_at"]
        assert arrived2 == arrived1, f"重复到件不应改动时间：{arrived1} -> {arrived2}"

        # ⚠️ 上面这次比较**不足以证明幂等** —— 两次调用落在同一秒，`NOW()` 返回相同值，
        # 即便实现写成裸 `SET arrived_at = NOW()`（覆盖式）也照样相等。
        # （评审实测：把 `IFNULL(arrived_at, NOW())` 改成 `NOW()`，原断言仍全绿。）
        # 真正能判别的做法：先把 arrived_at 人为拨到 1 小时前，再标一次到件 ——
        # 必须是"保持旧值"，而不是"刷新成现在"。
        jt_db.execute(
            "UPDATE takeaway_order SET arrived_at = NOW() - INTERVAL 1 HOUR WHERE id = ?", [oid])
        backdated = jt_db.query(
            "SELECT arrived_at FROM takeaway_order WHERE id = ?", [oid])[0]["arrived_at"]
        assert life.mark_order_arrived(oid, code) is True
        after = jt_db.query(
            "SELECT arrived_at FROM takeaway_order WHERE id = ?", [oid])[0]["arrived_at"]
        assert after == backdated, (
            f"重复到件改写了首次到件时间：{backdated} -> {after}（说明写成了覆盖式 NOW()）")

        # 到件通知：幂等
        assert life.mark_order_notified(oid) is True
        n1 = jt_db.query(
            "SELECT notified_at FROM takeaway_order WHERE id = ?", [oid])[0]["notified_at"]
        assert n1
        assert life.mark_order_notified(oid) is True
        n2 = jt_db.query(
            "SELECT notified_at FROM takeaway_order WHERE id = ?", [oid])[0]["notified_at"]
        assert n2 == n1, "重复通知标记不应改动时间"

        # 历史代买订单：存量与标签不变
        legacy_after = int(
            jt_db.query("SELECT COUNT(*) AS c FROM takeaway_order WHERE biz_type = 1")[0]["c"]
        )
        assert legacy_after == legacy_before, \
            f"历史代买订单被动过：{legacy_before} -> {legacy_after}"
        tx.rollback()
    ok += 1; print("[18] 代收闭环 驿站/取件码/到件(幂等+防错码)/历史代买不受影响 通过 (事务回滚)")

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
        ok += 1; print(f"[19] 用户行含 token_version/student_no_updated_at 通过 (uid={uid})")

        tv0 = int(row.get("token_version") or 0)
        with jt_db.begin() as tx:
            assert int(user.bump_token_version(uid)) == tv0 + 1
            assert int(user.bump_token_version(uid)) == tv0 + 2   # 可连续自增
            tx.rollback()
        ok += 1; print("[20] token_version 自增 通过 (事务回滚)")

        with jt_db.begin() as tx:
            # 从没改过 ⇒ 剩余 0；改完 ⇒ 剩余 > 0（7 天窗口生效）
            assert user.student_no_change_remaining_days(uid, 7) >= 0
            assert user.update_student_no(uid, "C22DAO000001") is True
            assert user.find_by_id(uid)["student_no"] == "C22DAO000001"   # 回读
            assert user.student_no_change_remaining_days(uid, 7) > 0
            tx.rollback()
        ok += 1; print("[21] 学号绑定 + 回读 + 限频生效 通过 (事务回滚)")

    # 不存在的用户：写返回 False、版本返回 -1、剩余天数返回 -1（不抛异常）
    assert user.update_student_no(999000001, "C22NONE") is False
    assert user.bump_token_version(999000001) == -1
    assert user.student_no_change_remaining_days(999000001, 7) == -1
    ok += 1; print("[22] 不存在用户返回 False / -1 通过")

    print(f"\n【成功】全部 DAO 测试通过！共 {ok} 组断言")


if __name__ == "__main__":
    main()
