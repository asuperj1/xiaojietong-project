"""搜索历史接口（B22）：增（去重）/ 倒序拉取 / 单删 / 清空。

契约：`docs/api.md`（均需登录；**只能操作自己的历史**，用 `user_id` 兜住不靠 id 猜）

⚠️ **写入必须用 `ON DUPLICATE KEY UPDATE created_at = CURRENT_TIMESTAMP`**：
`user_search_history` 上有唯一键 `uk_user_keyword(user_id, keyword)`，
直接 INSERT 同一个词会踩 `ERROR 1062 Duplicate entry`；用 ODKU 后语义变成
「重搜同一个词 = 刷新时间 → 记录顶到最前」，天然去重（任务单 L284 的硬要求）。

附带两个工程细节：
- 写入后**裁剪到最近 N 条**（``_MAX_KEEP``），避免历史无限增长；
- `keyword` 先 strip 再校验长度（列是 `VARCHAR(64)`），非法值返回契约错误 `1001`
  而不是让 MySQL 抛 1406/截断。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.core.deps import get_current_user
from app.core.response import err_param, ok
from app.db import cpp_bridge

router = APIRouter(prefix="/search", tags=["search"])

QRY_LIMIT_MAX = 100          # 单次拉取上限
_MAX_KEEP = 50               # 每个用户保留的最近条数（超出裁剪，防无限增长）


class SearchHistoryIn(BaseModel):
    """写入一条搜索历史。"""

    keyword: str = Field(default="", description="搜索词（前后空白会被去掉，最长 64 字）")


@router.get("/history")
def list_history(
    limit: int = Query(20, ge=1, le=QRY_LIMIT_MAX),
    user: dict = Depends(get_current_user),
):
    """倒序拉取最近的搜索历史（`ORDER BY created_at DESC`，走 `idx_user_time`）。

    响应 ``data``：``{ "items": [ {"id":1,"keyword":"图书馆","created_at":"..."} ], "total": n }``
    """
    uid = int(user["id"])
    rows = cpp_bridge.query(
        "SELECT id, keyword, created_at FROM user_search_history "
        "WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
        [uid, int(limit)],
    )
    for r in rows:
        r["id"] = int(r.get("id") or 0)
    total = cpp_bridge.query(
        "SELECT COUNT(*) AS c FROM user_search_history WHERE user_id = ?", [uid]
    )
    return ok({"items": rows, "total": int(total[0]["c"]) if total else len(rows)})


@router.post("/history")
def add_history(body: SearchHistoryIn, user: dict = Depends(get_current_user)):
    """写入搜索词（**自动去重**：同人同词只更新 `created_at`，记录顶到最前）。

    响应 ``data``：``{ "id": 7, "keyword": "图书馆", "dedup": true }``
    （``dedup=true`` 表示该词此前已存在、本次为刷新时间）
    """
    keyword = (body.keyword or "").strip()
    if not keyword:
        raise err_param("搜索词不能为空")
    if len(keyword) > 64:
        raise err_param("搜索词过长（最多 64 字）")

    uid = int(user["id"])
    existed = cpp_bridge.query(
        "SELECT id FROM user_search_history WHERE user_id = ? AND keyword = ?", [uid, keyword]
    )
    # 唯一键 + ODKU：同词不报 1062，改为刷新时间（真去重）
    cpp_bridge.execute(
        "INSERT INTO user_search_history (user_id, keyword) VALUES (?, ?) "
        "ON DUPLICATE KEY UPDATE created_at = CURRENT_TIMESTAMP",
        [uid, keyword],
    )
    rows = cpp_bridge.query(
        "SELECT id, keyword, created_at FROM user_search_history "
        "WHERE user_id = ? AND keyword = ? LIMIT 1",
        [uid, keyword],
    )
    item = rows[0] if rows else {"id": 0, "keyword": keyword, "created_at": None}
    if item.get("id") is not None:
        item["id"] = int(item["id"])

    # 裁剪：只保留最近 _MAX_KEEP 条（幂等，按 created_at 倒序之后的全删）
    cpp_bridge.execute(
        "DELETE FROM user_search_history WHERE user_id = ? AND id NOT IN ("
        "  SELECT id FROM (SELECT id FROM user_search_history WHERE user_id = ? "
        "                  ORDER BY created_at DESC, id DESC LIMIT ?) keep"
        ")",
        [uid, uid, _MAX_KEEP],
    )
    return ok({**item, "dedup": bool(existed)})


@router.delete("/history/{history_id}")
def delete_history(history_id: int, user: dict = Depends(get_current_user)):
    """单删一条（**带 `user_id` 条件**：删别人的 id 不会生效，越权返回 `1001`）。"""
    affected, _ = cpp_bridge.execute(
        "DELETE FROM user_search_history WHERE id = ? AND user_id = ?",
        [int(history_id), int(user["id"])],
    )
    if not affected:
        raise err_param(f"记录不存在或不属于当前用户：{history_id}")
    return ok({"id": int(history_id), "deleted": True})


@router.delete("/history")
def clear_history(user: dict = Depends(get_current_user)):
    """清空当前用户的全部搜索历史（只删自己）。"""
    affected, _ = cpp_bridge.execute(
        "DELETE FROM user_search_history WHERE user_id = ?", [int(user["id"])]
    )
    return ok({"deleted": int(affected)})
