"""生活服务：商家 / 菜单 / 外卖 / 通知。

契约：docs/api.md §10
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core.deps import get_current_user
from app.core.response import BizError, err_param, ok, paged
from app.db import cpp_bridge

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
    rows = cpp_bridge.life_dao().page_notices(page, size, category, target_grade)
    return ok(paged(rows, len(rows), page, size))


@router.get("/notice-feed")
def notice_feed(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    """AI 精准通知推送（占位）：按用户年级过滤；后续接标签+模型排序。"""
    grade = user.get("grade", "")
    rows = cpp_bridge.life_dao().page_notices(page, size, "", grade)
    return ok(paged(rows, len(rows), page, size))


@router.post("/notices/{notice_id}/read")
def mark_read(notice_id: int, user: dict = Depends(get_current_user)):
    ok_flag = cpp_bridge.life_dao().mark_notice_read(int(user["id"]), notice_id)
    return ok({"notice_id": notice_id, "read": ok_flag})
