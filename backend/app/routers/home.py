"""首页数据接口（B21）：轮播位 + 首页信息流。

契约：`docs/api.md` §10.x（均需登录）
- ``GET /home/banners``：只返回**启用中且在有效期内**的轮播，按 ``sort`` **倒序**（越大越前，
  与 ``db/sql/15_home_banner.sql`` 的注释一致）；
- ``GET /home/feed``：首页信息流，``sort=recommend|hot`` + 分页；``recommend`` 复用
  B11 的个性化打分（``services/recommend.build_feed``），``hot`` 复用论坛热度榜
  （``ForumDAO.hot_topics``），冷启动/无兴趣标签时前端可直接切 ``hot``。

⚠️ **列名兼容（临时）**：``home_banner`` 在不同环境存在两套形状 ——
``image_url``/``link_url``（本机库，早于 DDL 包存在）与 ``image``/``link_type``/``link_target``
（``15_home_banner.sql`` 的建表定义）。B19 的 schema 收敛尚未定论，因此这里用
``information_schema`` 探测后**归一化输出契约字段** ``image_url``/``link_url``，
两种形状都能返回；收敛完成后可删掉探测、直接 SELECT（同 B19/B20 的既有做法）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.core.deps import get_current_user
from app.core.response import err_param, ok, paged
from app.db import cpp_bridge
from app.services import recommend

router = APIRouter(prefix="/home", tags=["home"])

# 轮播位的两种历史形状（探测用）
_BANNER_COLUMN_ALIASES = ("image_url", "link_url", "image", "link_type", "link_target")


def _banner_columns() -> set[str]:
    """``home_banner`` 实际存在的相关列（进程内不缓存：DDL 收敛后无需重启即可切换）。"""
    try:
        rows = cpp_bridge.query(
            "SELECT column_name AS c FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'home_banner' "
            "AND column_name IN ('image_url','link_url','image','link_type','link_target')"
        )
    except Exception:  # noqa: BLE001 - 探测失败按新形状处理（本机库形状）
        return {"image_url", "link_url"}
    return {str(r.get("c") or "") for r in rows if r.get("c")}


def _banner_projection(cols: set[str]) -> tuple[str, str, str]:
    """按存在的列拼 SELECT 表达式（统一别名为契约字段 image_url / link_url）。"""
    image = "image_url" if "image_url" in cols else ("image AS image_url" if "image" in cols else "'' AS image_url")
    link = "link_url" if "link_url" in cols else ("link_target AS link_url" if "link_target" in cols else "'' AS link_url")
    kind = "link_type" if "link_type" in cols else "'' AS link_type"
    return image, link, kind


@router.get("/banners")
def banners(user: dict = Depends(get_current_user)):
    """首页轮播位（启用中 + 有效期内 + ``sort`` 倒序）。

    响应 ``data.items[]``：
    ``{ "id":1, "title":"迎新季·校园服务上新", "image_url":"/static/banners/welcome.png",
       "link_url":"/pages/service/service", "link_type":"page", "sort":30 }``
    """
    cols = _banner_columns()
    image, link, kind = _banner_projection(cols)
    rows = cpp_bridge.query(
        f"SELECT id, title, {image}, {link}, {kind}, sort, start_at, end_at "
        "FROM home_banner "
        "WHERE enabled = 1 "
        "  AND (start_at IS NULL OR start_at <= NOW()) "
        "  AND (end_at IS NULL OR end_at >= NOW()) "
        "ORDER BY sort DESC, id ASC"
    )
    for r in rows:                       # 契约：id/sort 为数字
        r["id"] = int(r.get("id") or 0)
        r["sort"] = int(r.get("sort") or 0)
    return ok({"items": rows, "columns": sorted(cols)})


def _only_audited(rows: list[dict]) -> list[dict]:
    """热榜安全兜底：只保留 `audit_status = 1` 且未删除的帖子。

    `is_hot` 是**人工热度标**，与审核状态是两条独立链路 —— 一旦有人给未过审/被拒的
    帖子打了热度标（实测出现过「代考包过」被打标），首页热榜就会传播违规内容。
    这里按 id 复核一次审核状态；热榜条数上限 50，代价可忽略。
    """
    ids = [int(r["id"]) for r in rows if r.get("id") is not None]
    if not ids:
        return rows
    placeholders = ",".join("?" for _ in ids)
    allowed = cpp_bridge.query(
        f"SELECT id FROM topic WHERE id IN ({placeholders}) "
        "AND audit_status = 1 AND is_deleted = 0",
        ids,
    )
    allowed_ids = {int(r["id"]) for r in allowed}
    return [r for r in rows if int(r.get("id") or 0) in allowed_ids]


@router.get("/feed")
def home_feed(
    sort: str = Query("recommend"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    """首页信息流（分页 + 排序 + 鉴权）。

    - ``sort=recommend``（默认）：B11 个性化打分（兴趣标签 + 行为偏好 + 热度 + 时效），
      逐条带 ``score``/``reason``，冷启动自动回落热度榜；
    - ``sort=hot``：论坛热度榜（``ForumDAO.hot_topics``），单次最多 50 条，
      故分页深度受该上限约束（``total`` 为本次可取到的条数）。

    ``sort`` 非法值返回契约错误 ``1001``（**不用** FastAPI 的 `pattern=`：
    那会返回非契约的 `422 {"detail":...}`，见审计"空 body → 422"同族问题）。
    """
    if sort not in ("recommend", "hot"):
        raise err_param(f"sort 只支持 recommend / hot，收到 {sort!r}")
    if page > 1 and sort == "hot" and page * size > 50:
        raise err_param("hot 排序最多支持前 50 条（ForumDAO.hot_topics 上限），请改用 sort=recommend")
    if sort == "hot":
        rows = cpp_bridge.forum_dao().hot_topics(min(50, page * size))
        # 安全兜底（见 _only_audited）：热榜只返回**已过审**的帖子
        rows = _only_audited(rows)
        start = (page - 1) * size
        items = rows[start : start + size]
        return ok(paged(items, len(rows), page, size))
    items, total = recommend.build_feed(user, page, size)
    return ok(paged(items, total, page, size))
