"""搜索历史接口（B22）：增（去重）/ 倒序拉取 / 单删 / 清空。

契约：`docs/api.md` §10.5（均需登录；**只能操作自己的历史**，用 `user_id` 兜住不靠 id 猜）

⭐ 相对旧实现（``feat/b19-ddl-pack``）的迁移
------------------------------------------
1. **改走 C++ DAO**（`UserDAO` 的四个方法，C23 交付）：
   ``add_search_history`` / ``list_search_history`` / ``delete_search_history`` /
   ``clear_search_history``。**写入去重**（``ON DUPLICATE KEY UPDATE``）与
   **越权防护**（``WHERE id = ? AND user_id = ?``）都已经在 DAO 里，
   路由不再重复拼这两条 SQL，避免两处口径不一致。
2. **长度校验与 DDL 对齐到 128 字**：``user_search_history.keyword`` 是
   ``VARCHAR(128)``（MySQL 的 VARCHAR(n) 是**字符数**），DAO 侧
   ``utf8_truncate(..., 128)`` 同样按**字符**截断；旧实现里的 64 是照当时
   ``VARCHAR(64)`` 写的，现在 DDL / DAO / 路由三方统一到 128。
3. 索引名注释更正为 ``idx_user_created``（旧文档写的 ``idx_user_time`` 并不存在，
   见 ``db/sql/16_search_history.sql``）。

为什么还要在 Python 里补两条 SQL：① `total` 需要 COUNT，DAO 未提供；
② 「每人只保留最近 50 条」的裁剪 DAO 未提供。除此之外的读写全部走 DAO。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.core.deps import get_current_user
from app.core.response import err_param, err_server, ok
from app.db import cpp_bridge

router = APIRouter(prefix="/search", tags=["search"])

QRY_LIMIT_MAX = 100          # 单次拉取上限（DAO 侧上限是 200，这里收得更紧）
MAX_KEEP = 50                # 每个用户保留的最近条数（超出裁剪，防历史无限增长）
KEYWORD_MAX = 128            # 与 user_search_history.keyword VARCHAR(128) 对齐


class SearchHistoryIn(BaseModel):
    """写入一条搜索历史。"""

    keyword: str = Field(default="", description="搜索词（前后空白会被去掉，最长 128 字）")


@router.get("/history")
def list_history(
    limit: int = Query(20, ge=1, le=QRY_LIMIT_MAX),
    user: dict = Depends(get_current_user),
):
    """倒序拉取最近的搜索历史（`ORDER BY created_at DESC, id DESC`，走 `idx_user_created`）。

    响应 ``data``：``{ "items": [ {"id":1,"keyword":"图书馆","created_at":"..."} ], "total": n }``
    """
    uid = int(user["id"])
    rows = cpp_bridge.user_dao().list_search_history(uid, int(limit))
    for r in rows:
        r["id"] = int(r.get("id") or 0)
    total = cpp_bridge.query(
        "SELECT COUNT(*) AS c FROM user_search_history WHERE user_id = ?", [uid]
    )
    return ok({"items": rows, "total": int(total[0]["c"]) if total else len(rows)})


@router.post("/history")
def add_history(body: SearchHistoryIn, user: dict = Depends(get_current_user)):
    """写入搜索词（**自动去重**：同人同词只更新 `created_at`，记录顶到最前）。

    响应 ``data``：``{ "id": 7, "keyword": "图书馆", "created_at": "...", "dedup": true }``
    （``dedup=true`` 表示该词此前已存在、本次为刷新时间）
    """
    keyword = (body.keyword or "").strip()
    if not keyword:
        raise err_param("搜索词不能为空")
    if len(keyword) > KEYWORD_MAX:
        raise err_param(f"搜索词过长（最多 {KEYWORD_MAX} 字）")

    uid = int(user["id"])
    # `dedup` 只是给前端的提示字段 —— 真正的去重由 DAO 的 ODKU 保证（返回的是同一行 id）
    existed = bool(cpp_bridge.query(
        "SELECT id FROM user_search_history WHERE user_id = ? AND keyword = ? LIMIT 1",
        [uid, keyword],
    ))

    new_id = int(cpp_bridge.user_dao().add_search_history(uid, keyword))
    if new_id <= 0:                      # DAO 对空关键词返回 -1；空串上面已经挡掉
        raise err_server("搜索词写入失败")

    rows = cpp_bridge.query(
        "SELECT id, keyword, created_at FROM user_search_history WHERE id = ?", [new_id]
    )
    item = rows[0] if rows else {"id": new_id, "keyword": keyword, "created_at": None}
    item["id"] = int(item.get("id") or new_id)

    # 裁剪：只保留最近 MAX_KEEP 条（幂等，按 created_at 倒序之后的全删）
    cpp_bridge.execute(
        "DELETE FROM user_search_history WHERE user_id = ? AND id NOT IN ("
        "  SELECT id FROM (SELECT id FROM user_search_history WHERE user_id = ? "
        "                  ORDER BY created_at DESC, id DESC LIMIT ?) keep"
        ")",
        [uid, uid, MAX_KEEP],
    )
    return ok({**item, "dedup": existed})


@router.delete("/history/{history_id}")
def delete_history(history_id: int, user: dict = Depends(get_current_user)):
    """单删一条（DAO 的 WHERE 带 `user_id`：删别人的 id 不会生效，返回 `1001`）。"""
    if not cpp_bridge.user_dao().delete_search_history(int(user["id"]), int(history_id)):
        raise err_param(f"记录不存在或不属于当前用户：{history_id}")
    return ok({"id": int(history_id), "deleted": True})


@router.delete("/history")
def clear_history(user: dict = Depends(get_current_user)):
    """清空当前用户的全部搜索历史（只删自己）。"""
    affected = int(cpp_bridge.user_dao().clear_search_history(int(user["id"])))
    return ok({"deleted": affected})
