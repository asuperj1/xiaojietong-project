"""生活服务：代收（取件码 / 驿站 / 到件） / 通知。

契约：docs/api.md §10

**B31：代买已下线**（产品方决策，任务单 §7.1）—— `/life/merchants`、
`/life/merchants/{id}/menu` 与代买版 `POST /life/orders`（`{merchant_id, items[]}`）**已移除**，
`merchant` / `menu_item` 表仅留历史数据；`/life/orders` 现在表示**代收订单**。
代收闭环（取件码生成 / 到件 / 站内通知）见 `services/takeaway.py`。

通知（B10）：个性化排序 + 未读汇总 + 批量已读经 services/notice.py
（兴趣标签 + 行为偏好 + 年级/校区 + 时效衰减打分，投递记录懒生成）。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core.deps import get_current_user
from app.core.response import BizError, err_param, ok, paged
from app.db import cpp_bridge
from app.services import notice as notice_service
from app.services import notice_scheduler
from app.services import takeaway

router = APIRouter(prefix="/life", tags=["life"])


@router.get("/pickup-points")
def pickup_points(user: dict = Depends(get_current_user)):
    """可选取件驿站（B31）：固定取件点**单选**，仅 `status=1` 且未删除的，按 `sort` 倒序。

    列名跟随 `db/sql/18_takeaway_pickup.sql` 的权威定义（漂移列 `open_time`/`enabled` 已被收敛脚本清理）。
    """
    return ok({"items": takeaway.pickup_points()})


class TakeawayOrderIn(BaseModel):
    # 默认 0 而非必填：老前端若仍按代买体（{merchant_id, items}）调用，
    # 会落到下面那条契约错误（1001），而不是 FastAPI 的 422 detail
    pickup_point_id: int = 0
    address: str = ""
    contact: str = ""
    contact_phone: str = ""
    remark: str = ""


@router.post("/orders")
def create_order(body: TakeawayOrderIn, user: dict = Depends(get_current_user)):
    """代收下单（B31）：选定取件驿站 → 生成 **6 位取件码**，**不计费**。"""
    if body.pickup_point_id <= 0:
        raise err_param("请选择取件驿站")
    return ok(
        takeaway.create_order(
            int(user["id"]),
            body.pickup_point_id,
            body.address.strip(),
            body.contact.strip(),
            body.contact_phone.strip(),
            body.remark.strip(),
        )
    )


@router.get("/orders")
def my_orders(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    """我的代收订单（B31）：按 id 倒序，逐条带取件码与驿站名。"""
    uid = int(user["id"])
    offset = (page - 1) * size
    rows = cpp_bridge.query(
        "SELECT `id`, `biz_type`, `status`, `pickup_code`, `pickup_point_id`, `remark`, "
        "`pay_amount`, `arrived_at`, `notified_at`, `created_at` FROM `takeaway_order` "
        "WHERE `user_id` = ? AND `biz_type` = ? ORDER BY `id` DESC LIMIT ? OFFSET ?",
        [uid, takeaway.BIZ_TYPE_TAKEAWAY, size, offset],
    )
    total = cpp_bridge.query(
        "SELECT COUNT(*) AS total FROM `takeaway_order` WHERE `user_id` = ? AND `biz_type` = ?",
        [uid, takeaway.BIZ_TYPE_TAKEAWAY],
    )
    items = [takeaway.order_view(r) for r in rows]
    return ok(paged(items, int(total[0]["total"]) if total else 0, page, size))


@router.get("/orders/{order_id}")
def order_detail(order_id: int, user: dict = Depends(get_current_user)):
    """代收订单详情（B31）：**取件码 + 驿站信息 + 到件时间线**。

    前端 F19 据此把取件码大字展示；`arrived_at` / `notified_at` 为空表示尚未到件。
    """
    rows = cpp_bridge.query(
        "SELECT * FROM `takeaway_order` WHERE `id` = ? AND `user_id` = ?",
        [order_id, int(user["id"])],
    )
    if not rows:
        raise BizError(1001, "订单不存在")
    order = takeaway.order_view(rows[0])
    order["items"] = json.loads(order.get("items_json", "[]") or "[]")
    order.pop("items_json", None)
    return ok(order)


@router.get("/notices")
def notices(
    category: str = "",
    target_grade: str = "",
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    """通知列表（公共口径）。

    **B18 私密行过滤（PR #60 审查 P0 修复）**：分层推送（D-7 / D-2）是**投递给具体用户**
    的通知，其标记写在 ``target_grade``（``__push:...``）。**不能**用返回行里的
    ``target_grade`` 判断——``LifeDAO.page_notices`` 的 SELECT 不含该列，读出来恒为 None，
    过滤会静默失效。因此改为：先取「私密行 id 集合」，再按 **id** 剔除。

    ⚠️ 剔除与切片的顺序有讲究，见 ``services.notice.public_notice_page``。两条实测坑：
    ① 「先取第 page 页、不够再向后补拉」会让窗口整体前移 → 跨页重复 + 漏项；
    ② 缓冲只放"一页"时，私密行全堆在时间轴顶端会让 ``size=1`` / ``size=3``
       **直接返回空列表**（私密行有 10 条，一页缓冲够不到公共行）。
    这些推送对**本人**仍可见——走 ``/notice-feed`` 与 ``/notices/unread``（按投递记录取）。
    """
    private_ids = notice_scheduler.private_notice_ids()
    items = notice_service.public_notice_page(private_ids, page, size, category, target_grade)
    # B20：DAO 的 SELECT 是编译期写死的，拿不到 B19 新列 → Python 侧按 id 补查
    notice_service.attach_extended_fields(items)
    return ok(paged(items, len(items), page, size))


@router.get("/notice-feed")
def notice_feed(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    """AI 精准通知推送（B10）：按推荐得分排序，逐条附推荐理由。

    打分 = 兴趣标签命中 + 行为偏好（点赞/收藏分类）+ 年级/校区匹配 + 时效衰减；
    拉取时懒生成投递记录（notice_delivery），并回执曝光。
    """
    rows, total = notice_service.build_feed(user, page, size)
    return ok(paged(rows, total, page, size))


@router.get("/notices/unread-count")
def notices_unread_count(user: dict = Depends(get_current_user)):
    """未读通知数（B10）：与未读列表口径一致，批量已读后归零。"""
    return ok({"count": notice_service.unread_count(user)})


@router.get("/notices/unread")
def notices_unread(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    """未读通知列表（B10，按推荐得分倒序）。"""
    rows, total = notice_service.unread_list(user, page, size)
    return ok(paged(rows, total, page, size))


class ReadBatchIn(BaseModel):
    notice_ids: list[int]


@router.post("/notices/read-batch")
def notices_read_batch(body: ReadBatchIn, user: dict = Depends(get_current_user)):
    """批量标记已读（B10）：投递表置已读并同步旧回执表。"""
    updated = notice_service.mark_read_batch(user, body.notice_ids)
    return ok({"updated": updated})


@router.post("/notices/{notice_id}/read")
def mark_read(notice_id: int, user: dict = Depends(get_current_user)):
    """单条已读：保留旧口径回执，同时同步投递记录（B10）。"""
    ok_flag = cpp_bridge.life_dao().mark_notice_read(int(user["id"]), notice_id)
    notice_service.mark_read_batch(user, [notice_id])
    return ok({"notice_id": notice_id, "read": ok_flag})
