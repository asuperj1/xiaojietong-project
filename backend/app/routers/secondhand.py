"""二手：物品 / 求购 / 匹配 / 订单。

契约：docs/api.md §6
"""

from __future__ import annotations

import math

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core.deps import get_current_user
from app.core.response import BizError, err_param, ok, paged
from app.db import cpp_bridge

router = APIRouter(prefix="/secondhand", tags=["secondhand"])


def _item_view(i: dict) -> dict:
    i["images"] = _parse_json(i.get("images_json", ""))
    i.pop("images_json", None)
    i["price"] = float(i.get("price", 0))
    return i


def _parse_json(raw) -> list:
    import json

    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


@router.get("/items")
def list_items(
    category: str = "",
    q: str = "",
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    rows = cpp_bridge.secondhand_dao().page_items(page, size, category, q)
    items = [_item_view(r) for r in rows]
    return ok(paged(items, len(items), page, size))


class PublishIn(BaseModel):
    title: str
    description: str = ""
    category: str = ""
    price: float = 0
    condition_level: int = 5
    images: list[str] = []


@router.post("/items")
def publish(body: PublishIn, user: dict = Depends(get_current_user)):
    if not body.title.strip():
        raise err_param("标题不能为空")
    item_id = cpp_bridge.secondhand_dao().publish(
        int(user["id"]), body.title, body.description, body.category, body.price
    )
    return ok({"item_id": item_id})


class AiDescribeIn(BaseModel):
    image_url: str = ""
    user_note: str = ""


@router.post("/items/ai-describe")
def ai_describe(body: AiDescribeIn, user: dict = Depends(get_current_user)):
    """AI 辅助发布（占位）：接入图像识别/大模型后替换。
    当前按用户备注关键词给出简单分类与定价建议。"""
    note = body.user_note or ""
    category = "教材" if ("书" in note or "教材" in note) else "其他"
    price = 30.0 if category == "教材" else 20.0
    return ok(
        {
            "category": category,
            "title": f"{note or '闲置物品'}（AI 建议标题）",
            "suggested_price": price,
            "description": f"九成新，价格可小刀。{note}",
        }
    )


@router.put("/items/{item_id}/status")
def update_item_status(
    item_id: int, body: dict, user: dict = Depends(get_current_user)
):
    status = str(body.get("status", ""))
    if status not in ("0", "1", "2"):
        raise err_param("status 取值 0/1/2")
    ok_flag = cpp_bridge.secondhand_dao().update_status(item_id, int(user["id"]), status)
    if not ok_flag:
        raise BizError(3001, "只能操作自己的物品")
    return ok({"item_id": item_id, "status": int(status)})


class WishIn(BaseModel):
    content: str
    category: str = ""
    budget: float = 0


@router.post("/wishes")
def create_wish(body: WishIn, user: dict = Depends(get_current_user)):
    wish_id = cpp_bridge.secondhand_dao().create_wish(
        int(user["id"]), body.content, body.category, body.budget
    )
    return ok({"wish_id": wish_id})


@router.get("/wishes/{wish_id}/match")
def match_wish(
    wish_id: int,
    limit: int = Query(10, ge=1, le=50),
    user: dict = Depends(get_current_user),
):
    items = cpp_bridge.secondhand_dao().match_items_for_wish(wish_id, limit)
    return ok({"items": items})


class OrderIn(BaseModel):
    item_id: int
    seller_id: int
    amount: float = 0


@router.post("/orders")
def create_order(body: OrderIn, user: dict = Depends(get_current_user)):
    with cpp_bridge.begin():
        order_id = cpp_bridge.secondhand_dao().create_order(
            body.item_id, int(user["id"]), body.seller_id, body.amount
        )
        # 物品标记已售
        cpp_bridge.execute(
            "UPDATE secondhand_item SET status = 1 WHERE id = ?", [body.item_id]
        )
    return ok({"order_id": order_id})
