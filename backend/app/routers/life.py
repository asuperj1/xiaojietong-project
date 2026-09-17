"""生活服务：商家 / 菜单 / 外卖 / 通知。

契约：docs/api.md §10
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
from app.services.storage import resign

router = APIRouter(prefix="/life", tags=["life"])


@router.get("/merchants")
def merchants(
    category: str = "",
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    rows = cpp_bridge.life_dao().page_merchants(page, size, category)
    return ok(paged(rows, len(rows), page, size))


@router.get("/merchants/{merchant_id}/menu")
def menu(merchant_id: int, user: dict = Depends(get_current_user)):
    items = cpp_bridge.life_dao().menu_items(merchant_id)
    for item in items:  # B14 P1 修复：菜品图入库为裸路径，返回前重新签名
        item["image"] = resign(item.get("image", ""))
    return ok({"items": items})


class OrderItem(BaseModel):
    id: int
    num: int = 1


class OrderIn(BaseModel):
    merchant_id: int
    items: list[OrderItem]
    address: str = ""
    contact: str = ""
    contact_phone: str = ""
    remark: str = ""


@router.post("/orders")
def create_order(body: OrderIn, user: dict = Depends(get_current_user)):
    if not body.items:
        raise err_param("订单不能为空")
    # 计算金额
    total = 0.0
    placeholders, params = [], []
    for it in body.items:
        placeholders.append("?")
        params.append(it.id)
    in_clause = ",".join(placeholders)
    rows = cpp_bridge.query(
        f"SELECT id, price FROM menu_item WHERE id IN ({in_clause})", params
    )
    price_map = {int(r["id"]): float(r["price"]) for r in rows}
    for it in body.items:
        total += price_map.get(it.id, 0) * it.num

    items_json = json.dumps(
        [{"id": i.id, "num": i.num} for i in body.items], ensure_ascii=False
    )
    with cpp_bridge.begin():
        order_id = cpp_bridge.life_dao().create_order(
            int(user["id"]), body.merchant_id, items_json, round(total, 2)
        )
    return ok({"order_id": order_id, "pay_amount": round(total, 2)})


@router.get("/orders/{order_id}")
def order_detail(order_id: int, user: dict = Depends(get_current_user)):
    rows = cpp_bridge.query(
        "SELECT * FROM takeaway_order WHERE id = ? AND user_id = ?",
        [order_id, int(user["id"])],
    )
    if not rows:
        raise BizError(1001, "订单不存在")
    order = rows[0]
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
