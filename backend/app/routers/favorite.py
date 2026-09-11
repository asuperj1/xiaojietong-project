"""收藏（通用）：收藏/取消收藏 与 我的收藏列表。

- 数据表：`favorite`（user_id + target_type + target_id，唯一键防重复）
- target_type 支持：topic（帖子）/ item（二手物品）
- 契约：docs/api.md §8（v1.2 新增收藏小节）
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core.deps import get_current_user
from app.core.response import BizError, err_param, ok, paged
from app.db import cpp_bridge

router = APIRouter(prefix="/favorites", tags=["favorite"])

# 允许收藏的目标类型（favorite.target_type 注释约定：topic/merchant/item...）
_ALLOWED_TARGETS = ("topic", "item")


class ToggleFavoriteIn(BaseModel):
    target_type: str
    target_id: int


def _ensure_target_exists(target_type: str, target_id: int) -> None:
    """校验收藏对象存在且未被删除/锁定，否则 1001。"""
    if target_type == "topic":
        rows = cpp_bridge.query(
            "SELECT id FROM topic WHERE id = ? AND is_deleted = 0 AND status != 1",
            [target_id],
        )
    elif target_type == "item":
        rows = cpp_bridge.query(
            "SELECT id FROM secondhand_item WHERE id = ? AND is_deleted = 0",
            [target_id],
        )
    else:
        raise err_param("target_type 仅支持 topic / item")
    if not rows:
        raise BizError(1001, "收藏对象不存在")


def _parse_json(raw) -> list:
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


def _item_view(i: dict) -> dict:
    i["images"] = _parse_json(i.get("images_json", ""))
    i.pop("images_json", None)
    i["price"] = float(i.get("price", 0))
    return i


@router.post("")
def toggle_favorite(body: ToggleFavoriteIn, user: dict = Depends(get_current_user)):
    """收藏 / 取消收藏（幂等切换）。"""
    uid = int(user["id"])
    _ensure_target_exists(body.target_type, body.target_id)
    existed = cpp_bridge.query(
        "SELECT id FROM favorite WHERE user_id = ? AND target_type = ? AND target_id = ?",
        [uid, body.target_type, body.target_id],
    )
    with cpp_bridge.begin():
        if existed:
            cpp_bridge.execute(
                "DELETE FROM favorite WHERE user_id = ? AND target_type = ? AND target_id = ?",
                [uid, body.target_type, body.target_id],
            )
            favorited = False
        else:
            cpp_bridge.execute(
                "INSERT INTO favorite (user_id, target_type, target_id) VALUES (?, ?, ?)",
                [uid, body.target_type, body.target_id],
            )
            favorited = True
    return ok(
        {
            "target_type": body.target_type,
            "target_id": body.target_id,
            "favorited": favorited,
        }
    )


@router.get("")
def my_favorites(
    target_type: str = Query("topic", description="topic=我收藏的帖子 / item=我收藏的物品"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    """我的收藏列表（分页；自动剔除已被删除/下架的对象）。"""
    uid = int(user["id"])
    if target_type not in _ALLOWED_TARGETS:
        raise err_param("target_type 仅支持 topic / item")
    offset = (page - 1) * size

    if target_type == "topic":
        where = (
            "f.user_id = ? AND f.target_type = 'topic' "
            "AND t.is_deleted = 0 AND t.status != 1"
        )
        rows = cpp_bridge.query(
            "SELECT f.id AS favorite_id, f.created_at AS favorited_at, "
            "t.id, t.title, t.category, t.like_count, t.comment_count, t.view_count, "
            "t.audit_status, t.ai_summary, t.is_hot, t.created_at "
            f"FROM favorite f JOIN topic t ON t.id = f.target_id WHERE {where} "
            "ORDER BY f.id DESC LIMIT ? OFFSET ?",
            [uid, size, offset],
        )
        total = cpp_bridge.query(
            "SELECT COUNT(*) AS total "
            f"FROM favorite f JOIN topic t ON t.id = f.target_id WHERE {where}",
            [uid],
        )
        # 收藏列表中的帖子必然已被收藏过，标记 true 便于前端直接复用详情组件
        for r in rows:
            r["favorited"] = True
    else:  # item
        where = "f.user_id = ? AND f.target_type = 'item' AND t.is_deleted = 0"
        rows = cpp_bridge.query(
            "SELECT f.id AS favorite_id, f.created_at AS favorited_at, "
            "t.id, t.title, t.category, t.price, t.condition_level, t.images_json, "
            "t.status, t.audit_status, t.trust_score, t.created_at "
            f"FROM favorite f JOIN secondhand_item t ON t.id = f.target_id WHERE {where} "
            "ORDER BY f.id DESC LIMIT ? OFFSET ?",
            [uid, size, offset],
        )
        total = cpp_bridge.query(
            "SELECT COUNT(*) AS total "
            f"FROM favorite f JOIN secondhand_item t ON t.id = f.target_id WHERE {where}",
            [uid],
        )
        rows = [_item_view(r) for r in rows]
        for r in rows:
            r["favorited"] = True
    return ok(paged(rows, int(total[0]["total"]) if total else 0, page, size))
