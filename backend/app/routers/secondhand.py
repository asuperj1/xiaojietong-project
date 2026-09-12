"""二手：物品 / 求购 / 匹配 / 订单。

契约：docs/api.md §6
审核（B6）：发布经 services/audit.py 判定（拒绝返回 3003；可疑转人工待审）。
AI（B9）：ai-describe / ai-price 经 services/secondhand_ai.py
          （库内同类均价定价 + 模型文案，模型不可用时统计兜底）。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core.deps import get_current_user
from app.core.response import (
    BizError,
    err_audit,
    err_param,
    err_server,
    ok,
    paged,
)
from app.db import cpp_bridge
from app.services.audit import audit_content, status_of
from app.services.secondhand_ai import describe_and_price, suggest_price

router = APIRouter(prefix="/secondhand", tags=["secondhand"])


def _item_view(i: dict) -> dict:
    i["images"] = _parse_json(i.get("images_json", ""))
    i.pop("images_json", None)
    i["price"] = float(i.get("price", 0))
    return i


def _parse_json(raw) -> list:
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


@router.get("/items/mine")
def my_items(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    """我的发布（含上下架/审核状态，便于前端管理）。"""
    uid = int(user["id"])
    offset = (page - 1) * size
    rows = cpp_bridge.query(
        "SELECT id, title, description, category, price, condition_level, images_json, "
        "status, audit_status, trust_score, view_count, created_at "
        "FROM secondhand_item WHERE user_id = ? AND is_deleted = 0 "
        "ORDER BY id DESC LIMIT ? OFFSET ?",
        [uid, size, offset],
    )
    total = cpp_bridge.query(
        "SELECT COUNT(*) AS total FROM secondhand_item "
        "WHERE user_id = ? AND is_deleted = 0",
        [uid],
    )
    items = [_item_view(r) for r in rows]
    for i in items:
        i["seller_name"] = user.get("nickname", "")
    return ok(paged(items, int(total[0]["total"]) if total else 0, page, size))


@router.get("/items/{item_id}")
def item_detail(item_id: int, user: dict = Depends(get_current_user)):
    """物品详情（B9 新增）：含图片列表（images）；浏览量 +1。"""
    rows = cpp_bridge.query(
        "SELECT i.*, u.nickname AS seller_name FROM secondhand_item i "
        "JOIN user u ON i.user_id = u.id "
        "WHERE i.id = ? AND i.is_deleted = 0",
        [item_id],
    )
    if not rows:
        raise BizError(1001, "物品不存在")
    cpp_bridge.execute(
        "UPDATE secondhand_item SET view_count = view_count + 1 WHERE id = ?", [item_id]
    )
    return ok(_item_view(rows[0]))


class PublishIn(BaseModel):
    title: str
    description: str = ""
    category: str = ""
    price: float = 0
    condition_level: int = 5
    images: list[str] = []


@router.post("/items")
async def publish(body: PublishIn, user: dict = Depends(get_current_user)):
    if not body.title.strip():
        raise err_param("标题不能为空")
    item_id = cpp_bridge.secondhand_dao().publish(
        int(user["id"]), body.title, body.description, body.category, body.price
    )
    # B9 修复：images / condition_level 此前未落库（DAO publish 不含这两列），
    # 导致发布后图片丢失；此处补写后 GET /items/{id} 即可返回 images。
    cpp_bridge.execute(
        "UPDATE secondhand_item SET images_json = ?, condition_level = ? WHERE id = ?",
        [json.dumps(body.images, ensure_ascii=False), body.condition_level, item_id],
    )
    # B6 内容审核（先入库拿到 item_id，审核留痕 audit_log 需要 target_id）
    verdict = await audit_content(
        f"{body.title}\n{body.description}",
        target_type="item",
        target_id=item_id,
        user_id=int(user["id"]),
    )
    audit_status = status_of(verdict["level"])
    cpp_bridge.execute(
        "UPDATE secondhand_item SET audit_status = ? WHERE id = ?",
        [audit_status, item_id],
    )
    if verdict["level"] == "block":
        raise err_audit(verdict["reason"] or "内容未通过审核")
    return ok(
        {"item_id": item_id, "audit_status": audit_status, "source": verdict["source"]}
    )


class AiDescribeIn(BaseModel):
    """AI 辅助发布入参（B9 扩展：标题/分类/成色可选；兼容旧 user_note/image_url）。"""

    title: str = ""
    category: str = ""
    condition_level: int = 8
    user_note: str = ""
    image_url: str = ""


@router.post("/items/ai-describe")
async def ai_describe(body: AiDescribeIn, user: dict = Depends(get_current_user)):
    """AI 辅助发布（B9）：生成描述文案 + 卖点 + 建议价。

    定价以**库内同类均价**为支撑（响应含 sample_count / avg_price / reason），
    文案由模型（xjt-3b）生成；模型不可用时降级为模板文案 + 统计定价。
    """
    result = await describe_and_price(
        body.title or body.user_note,
        body.category,
        body.condition_level,
        body.user_note,
    )
    return ok(result)


class AiPriceIn(BaseModel):
    category: str = ""
    title: str = ""
    condition_level: int = 8


@router.post("/items/ai-price")
def ai_price(body: AiPriceIn, user: dict = Depends(get_current_user)):
    """纯定价建议（B9 新增）：库内同类均价 × 成色折算系数，附样本数依据。"""
    return ok(suggest_price(body.category, body.condition_level))


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
    # ⚠️ 契约变更（审计 SEC-05）：seller_id / amount 不再由客户端提供。
    #    原实现直接信任客户端传入的 seller_id 与 amount，可伪造卖家（嫁祸）与 0 元订单；
    #    现改为服务端从 secondhand_item 反查真实卖家与价格。
    remark: str = ""


@router.post("/orders")
def create_order(body: OrderIn, user: dict = Depends(get_current_user)):
    """下单购买二手物品。

    审计修复：
      · SEC-05：任意登录用户曾可通过 `UPDATE ... WHERE id = ?`（无 user_id 条件）
        把他人商品置为「已售」；现改为服务端反查归属 + 原子条件更新。
      · TXN-02：原实现「先 INSERT 订单、再无条件 UPDATE」，并发下可重复成交（超卖）；
        现改为「先原子占位（WHERE status = 0）→ 判 affected → 再落订单」。
    """
    buyer_id = int(user["id"])
    with cpp_bridge.begin():
        # 1) 反查商品真实信息（存在 / 未删除 / 在售 / 归属）
        rows = cpp_bridge.query(
            "SELECT id, user_id, price, status FROM secondhand_item "
            "WHERE id = ? AND is_deleted = 0",
            [body.item_id],
        )
        if not rows:
            raise BizError(1001, "商品不存在")
        item = rows[0]
        if int(item["status"]) != 0:
            raise BizError(3001, "商品已售出或已下架")
        seller_id = int(item["user_id"])
        if seller_id == buyer_id:
            raise BizError(3001, "不能购买自己发布的物品")
        amount = float(item["price"] or 0)

        # 2) 原子占位：仅当仍为「在售(0)」时才置为已售(1)，靠 affected 兜底并发
        affected, _ = cpp_bridge.execute(
            "UPDATE secondhand_item SET status = 1 WHERE id = ? AND status = 0",
            [body.item_id],
        )
        if affected == 0:
            raise BizError(3001, "商品已被他人抢先下单")

        # 3) 落订单（卖家与金额均取自服务端，客户端无法伪造）
        order_id = cpp_bridge.secondhand_dao().create_order(
            body.item_id, buyer_id, seller_id, amount
        )
        if order_id <= 0:
            raise err_server("创建订单失败")
    return ok({"order_id": order_id, "amount": amount})
