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
    """校验收藏对象存在且未被删除/锁定，否则 1001。

    C10：存在性 SQL 已收敛到 C++ `FavoriteDAO::target_exists()`，
    本函数仅负责参数白名单与错误码映射。
    """
    if target_type not in _ALLOWED_TARGETS:
        raise err_param("target_type 仅支持 topic / item")
    if not cpp_bridge.favorite_dao().target_exists(target_type, target_id):
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
    _ensure_target_exists(body.target_type, body.target_id)
    # C10：先查后写的切换逻辑收敛到 FavoriteDAO（事务内保证语义）
    with cpp_bridge.begin():
        favorited = cpp_bridge.favorite_dao().toggle(
            int(user["id"]), body.target_type, body.target_id
        )
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

    # C10：两条 JOIN 列表用 SQL 与 COUNT 均已收敛到 FavoriteDAO
    dao = cpp_bridge.favorite_dao()
    if target_type == "topic":
        rows = dao.page_topics(uid, size, offset)
        total = dao.count_topics(uid)
    else:  # item
        rows = [_item_view(r) for r in dao.page_items(uid, size, offset)]
        total = dao.count_items(uid)

    # 收藏列表中的对象必然已被收藏过，标记 true 便于前端直接复用详情组件
    for r in rows:
        r["favorited"] = True
    return ok(paged(rows, int(total), page, size))
