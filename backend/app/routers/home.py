"""首页数据接口（B21）：轮播位 + 首页信息流。

契约：`docs/api.md` §10.5（均需登录）
- ``GET /home/banners``：只返回**启用中且在有效期内**的轮播，按 ``sort`` **倒序**
  （越大越前，与 ``db/sql/15_home_banner.sql`` 的注释一致）；
- ``GET /home/feed``：首页信息流，``sort=recommend|hot`` + 分页；``recommend`` 复用
  B11 的个性化打分（``services/recommend.build_feed``），``hot`` 复用论坛热度榜
  （``ForumDAO.hot_topics``），冷启动/无兴趣标签时前端可直接切 ``hot``。

⭐ 相对旧实现（``feat/b19-ddl-pack``）的三处迁移
----------------------------------------------
1. **删掉 `information_schema` 列名探测**。那是为 ``home_banner`` 的"漂移形状"
   （``image_url``/``link_url``）打的临时补丁；C46 已把该表收敛为**权威形状**
   （``image`` / ``link_type`` / ``link_target``，见 ``db/sql/15_home_banner.sql``），
   探测与随之返回的 ``columns`` 字段都不再需要。
2. **改走 C++ DAO**。``HomeDAO::list_banners`` 已把「启用 + 有效期 + 排序」的口径
   写死在一处（``db/cpp_driver/src/dao/home_dao.cpp``），``home_dao.h`` 也明确要求
   B21"不要自己在 Python 里拼 SQL"——否则排序/有效期口径会走样，而且表结构一变
   就得跟着改 Python。
3. **输出字段名与权威列名一致**（``image`` / ``link_type`` / ``link_target``），
   不再输出 ``image_url`` / ``link_url`` 别名。前端尚未接入本接口，没有兼容包袱，
   一次对齐省掉日后一层转换。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.deps import get_current_user
from app.core.response import err_param, ok, paged
from app.db import cpp_bridge
from app.services import recommend

router = APIRouter(prefix="/home", tags=["home"])

#: 轮播单次上限（`HomeDAO::list_banners` 会把 >200 夹到 200）
BANNER_LIMIT_MAX = 200

#: 热榜服务端上限：`ForumDAO.hot_topics` 单次最多 50 条
HOT_TOPICS_MAX = 50


def _to_int(row: dict, *fields: str) -> dict:
    """把 C++ 层读出来的数字字段归一化。

    jt_db 的行结果是「列名 → 字符串」（variant 转换），`id`、计数字段读出来是
    `"508"` 而不是 `508`。契约要求数字，而且前端很容易踩 `"5" === 5` 这种坑
    —— 实测 `/home/feed?sort=hot` 曾把字符串 id 直接吐给客户端。
    非数字的值原样保留，避免把数据改坏。
    """
    for field in fields:
        value = row.get(field)
        if value is None or value == "":
            continue
        try:
            row[field] = int(value)
        except (TypeError, ValueError):
            pass
    return row


@router.get("/banners")
def banners(
    limit: int = Query(20, ge=1, le=BANNER_LIMIT_MAX),
    user: dict = Depends(get_current_user),
):
    """首页轮播位（启用中 + 有效期内 + ``sort`` 倒序）。

    响应 ``data.items[]``：

    ``{ "id":1, "title":"迎新季·校园服务上新", "image":"/static/banners/welcome.png",
       "link_type":"page", "link_target":"/pages/service/service", "sort":30,
       "start_at":null, "end_at":null, "enabled":1, "created_at":"..." }``
    """
    rows = cpp_bridge.home_dao().list_banners(int(limit))
    for r in rows:
        _to_int(r, "id", "sort", "enabled")
        # C++ DAO 把 SQL NULL 读成空串，而契约（api.md §10.5）写的是 `null`
        # —— 实测确认库里的值是 NULL，这里归一化，别把"两种空"漏给前端。
        for field in ("start_at", "end_at"):
            if not r.get(field):
                r[field] = None
    return ok({"items": rows})


def _only_audited(rows: list[dict]) -> list[dict]:
    """热榜安全兜底：只保留 `audit_status = 1` 且未删除的帖子。

    `is_hot` 是**人工热度标**，与审核状态是两条独立链路 —— 一旦有人给未过审/被拒的
    帖子打了热度标（实测出现过「代考包过」被打标），首页热榜就会传播违规内容。
    这里按 id 复核一次审核状态；热榜条数上限 50，代价可忽略。

    根治做法是给 `hot_topics` 的 SQL 直接加 `AND audit_status = 1`（需重编译 C++），
    在那边落地之前，这层 Python 兜底必须保留。
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
    if page > 1 and sort == "hot" and page * size > HOT_TOPICS_MAX:
        raise err_param(
            f"hot 排序最多支持前 {HOT_TOPICS_MAX} 条（ForumDAO.hot_topics 上限），"
            "请改用 sort=recommend"
        )
    if sort == "hot":
        rows = cpp_bridge.forum_dao().hot_topics(min(HOT_TOPICS_MAX, page * size))
        # 安全兜底（见 _only_audited）：热榜只返回**已过审**的帖子
        rows = _only_audited(rows)
        for r in rows:                   # C++ 层的数值列是字符串，这里归一化
            _to_int(r, "id", "like_count", "comment_count", "view_count")
        start = (page - 1) * size
        items = rows[start : start + size]
        return ok(paged(items, len(rows), page, size))
    items, total = recommend.build_feed(user, page, size)
    return ok(paged(items, total, page, size))
