"""通知精准推荐与未读管理（B10）。

- **可见口径**：``campus_notice`` 中 ``target_grade`` 为空（全员）或等于用户年级；
- **投递记录**：拉取通知流/未读时**懒生成** ``notice_delivery``（唯一键幂等 upsert），
  记录得分、命中标签、推荐理由、曝光与已读状态；
- **打分维度（可解释）**：
  1. 兴趣标签命中（``user_tag`` 出现在标题/正文/分类）→ 每个 +1.5（上限 3 个）
  2. 行为偏好：用户点赞/收藏过的帖子分类命中通知分类 → +0.6
  3. 年级匹配：``target_grade`` = 用户年级 → +0.8；全员通知基础分 +0.3
  4. 校区匹配：标题/正文含用户校区 → +0.5
  5. 时效衰减：7 天内线性 0.5 → 0
- **未读**：``notice_delivery.is_read = 0``；批量已读同步回写 ``notice_read``（兼容旧口径）。
"""

from __future__ import annotations

from datetime import datetime

from app.db import cpp_bridge
from app.services.notice_scheduler import PRIVATE_TARGET_PREFIX, notice_extended_columns

_MAX_TAG_HITS = 3
_MAX_NOTICES = 200
# 私密推送行（B18）的 target_grade 形如 ``__push:reminder:12:D7``，
# 是"仅投递对象可见"的推送通知，绝不能进入他人的可见口径。
_PRIVATE_LIKE = f"{PRIVATE_TARGET_PREFIX}%"

_BASE_COLUMNS = ("id", "title", "content", "source", "category", "target_grade", "publish_time")
# B19 的 14_notice_extend.sql 提供的扩展列（B20 契约字段）
_EXTENDED_NAMES = ("deadline", "materials", "importance")


def notice_columns(alias: str = "") -> str:
    """按实际表结构拼通知列（B20 字段契约）。

    ``campus_notice`` 的 ``deadline``/``materials``/``importance`` 由 B19 的
    ``14_notice_extend.sql`` 提供——**未导入时不 SELECT 这些列**（否则 SQL 报错），
    导入后接口自动多返回这 3 个字段（**向后兼容**：旧字段全部保留）。
    """
    prefix = f"{alias}." if alias else ""
    cols = list(_BASE_COLUMNS) + [c for c in _EXTENDED_NAMES if c in notice_extended_columns()]
    return ", ".join(f"{prefix}{c}" for c in cols)


def attach_extended_fields(rows: list[dict]) -> list[dict]:
    """给**已查出的通知行**补上扩展字段（B20，供 C++ DAO 路径复用）。

    ``LifeDAO.page_notices`` 的 SELECT 是编译期写死的
    （``id,title,content,source,category,publish_time``），拿不到 B19 的新列；
    为不牵动 C++ 重编译，这里按 id 一次性补查（1 页 1 条 SQL，代价可忽略）。
    未导入 ``14_notice_extend.sql`` 时**原样返回**（行为与旧版一致）。
    """
    want = [c for c in _EXTENDED_NAMES if c in notice_extended_columns()]
    if not rows or not want:
        return rows
    # B20：若行里已经带全扩展字段（说明是支持 include_extended 的 jt_db 直出的），
    # 不必再补查一次。
    if all(col in rows[0] for col in want):
        return rows
    ids = [int(r["id"]) for r in rows if r.get("id") is not None]
    if not ids:
        return rows
    placeholders = ",".join("?" for _ in ids)
    extra = cpp_bridge.query(
        f"SELECT id, {', '.join(want)} FROM campus_notice WHERE id IN ({placeholders})",
        ids,
    )
    by_id = {int(r["id"]): r for r in extra}
    for row in rows:
        source = by_id.get(int(row["id"]), {})
        for col in want:
            row[col] = source.get(col)
    return rows


# `LifeDAO::page_notices` 的 size 上限。注意 C++ 侧是 `size > 100 → size = 20`（**重置**，
# 不是截到 100），所以一次最多只要 100 条，要多了反而只剩 20 条。
_DAO_MAX_SIZE = 100

# 逐块累积时的兜底上限（100 × 20 = 2000 条），防止异常数据下无限翻页。
_MAX_PROBE_BLOCKS = 20


def public_notice_page(
    private_ids: set[int], page: int, size: int, category: str = "", target_grade: str = ""
) -> list[dict]:
    """`/life/notices` 的公共通知分页：剔除私密推送行，且**不破坏分页**。

    ## 两个坑（都实测复现过）

    **坑一：先取第 page 页、不够再向后补拉** —— 会把本页窗口整体前移，
    第 N 页的尾部条目又出现在第 N+1 页（重复），真正属于第 N+1 页的条目被挤掉（漏项）。
    分页窗口的定义是「按 ``publish_time DESC`` 的第 ``start..start+size`` 条」，
    所以必须**先取到覆盖目标窗口的完整前缀、剔完私密行、再切片**。

    **坑二：缓冲只放"一页"** —— 私密推送行（B18 分层推送）全部堆在时间轴顶端时，
    窗口会被整体前移**私密行的条数**那么多位，而不是一页那么多。
    实测：库里 10 条私密行占据最新的 10 个位置时，``size=1`` 与 ``size=3``
    的一页缓冲（``span = want + size``）根本够不到公共行，接口**返回空列表**。
    因此缓冲取 ``want + max(size, len(private_ids))``。

    ## 开销

    - 无私密行：单次查询，与旧版完全一致（零额外代价）；
    - 有私密行且 ``span <= 100``：单次查询取前缀后切片；
    - 前缀过长（窗口落在 100 条之后，或私密行极多）：按 DAO 上限逐块累积，
      直到凑够 ``want`` 条公共行或数据取尽。

    ## 与 B20 的接合

    内部一律走 ``page_notices_rows()``（而不是直接 ``cpp_bridge.life_dao().page_notices``），
    这样**每一次取数都顺带带出 B19 的扩展列**（jt_db 为新版本时），
    不必再靠 ``attach_extended_fields`` 补查 —— 分页正确性（PR #95）与
    「一次查询带全字段」（B20）叠加，才是最优路径。
    """
    if page < 1:
        page = 1
    if size < 1:
        size = 1
    start = (page - 1) * size
    want = start + size

    if not private_ids:
        return page_notices_rows(page, size, category, target_grade)

    span = want + max(size, len(private_ids))
    if span <= _DAO_MAX_SIZE:
        rows = page_notices_rows(1, span, category, target_grade)
        public = [r for r in rows if int(r["id"]) not in private_ids]
        if len(rows) < span or len(public) >= want:
            return public[start:want]

    # 退化路径：一次拿不了那么多，就按 DAO 上限逐块取，直到凑够 want 条公共行。
    public = []
    block = 1
    while block <= _MAX_PROBE_BLOCKS:
        rows = page_notices_rows(block, _DAO_MAX_SIZE, category, target_grade)
        if not rows:
            break
        public.extend(r for r in rows if int(r["id"]) not in private_ids)
        if len(public) >= want or len(rows) < _DAO_MAX_SIZE:
            break
        block += 1
    return public[start:want]
def page_notices_rows(
    page: int, size: int, category: str = "", target_grade: str = ""
) -> list[dict]:
    """取一页通知行，**优先让 C++ DAO 直出** B19 的扩展列（B20）。

    `LifeDAO::page_notices` 的列清单原本是编译期写死的，拿不到
    ``deadline``/``materials``/``importance``，只能在 Python 侧按 id 二次补查
    （`attach_extended_fields`）。B20 给 DAO 加了 ``include_extended`` 形参后，
    条件具备时一次查询就能带全字段，省掉每页一条额外 SQL。

    三条路径，**结果都与旧版一致**：

    1. 库里有扩展列 **且** jt_db 是 B20 之后的版本 → 直出（最优）；
    2. jt_db 还是旧版本 → 多传的实参抛 `TypeError`，退回旧签名，
       再由 `attach_extended_fields` 补查；
    3. 库里没有扩展列（未导入 `14_notice_extend.sql`）→ 探测为空，
       直接走旧签名，与旧版逐字节一致。
    """
    dao = cpp_bridge.life_dao()
    if notice_extended_columns():
        try:
            return dao.page_notices(page, size, category, target_grade, True)
        except TypeError as exc:
            # pybind11 在**实参个数多于形参**时抛的是固定文案 "incompatible function arguments"，
            # 只认这一条 —— 别把 C++ 层因其它原因抛出的 TypeError 也静默吞掉（review P3）。
            if "incompatible function arguments" not in str(exc):
                raise
    return dao.page_notices(page, size, category, target_grade)


def _user_tags(user_id: int) -> list[str]:
    rows = cpp_bridge.query("SELECT tag FROM user_tag WHERE user_id = ?", [user_id])
    return [str(r.get("tag") or "") for r in rows if r.get("tag")]


def _behavior_categories(user_id: int) -> set[str]:
    """用户近期互动过的帖子分类（点赞 + 收藏）——行为偏好。"""
    rows = cpp_bridge.query(
        "SELECT DISTINCT t.category FROM like_record l JOIN topic t ON t.id = l.target_id "
        "WHERE l.user_id = ? AND l.target_type = 'topic' "
        "UNION "
        "SELECT DISTINCT t.category FROM favorite f JOIN topic t ON t.id = f.target_id "
        "WHERE f.user_id = ? AND f.target_type = 'topic'",
        [user_id, user_id],
    )
    return {str(r.get("category") or "") for r in rows if r.get("category")}


def _visible_notices(user: dict) -> list[dict]:
    grade = str(user.get("grade") or "")
    return cpp_bridge.query(
        f"SELECT {notice_columns()} "
        "FROM campus_notice WHERE (target_grade = '' OR target_grade = ?) "
        "AND target_grade NOT LIKE ? "
        "ORDER BY publish_time DESC LIMIT ?",
        [grade, _PRIVATE_LIKE, _MAX_NOTICES],
    )


def score_notice(notice: dict, user: dict, tags: list[str], behavior: set[str]) -> dict:
    """单条通知对用户的推荐得分、命中标签与可读理由。"""
    text = (
        f"{notice.get('title') or ''}{notice.get('content') or ''}"
        f"{notice.get('category') or ''}"
    )
    score = 0.3  # 全员基础分
    matched: list[str] = []
    reason = ""

    # 1) 兴趣标签命中
    for tag in tags:
        if tag and tag in text and len(matched) < _MAX_TAG_HITS:
            matched.append(tag)
            score += 1.5
            if not reason:
                reason = f"你关注了{tag}"

    # 2) 行为偏好（点赞/收藏过的分类）
    category = str(notice.get("category") or "")
    if category and category in behavior:
        score += 0.6
        if not reason:
            reason = f"你常看{category}类内容"

    # 3) 年级匹配
    target_grade = str(notice.get("target_grade") or "")
    user_grade = str(user.get("grade") or "")
    if target_grade and user_grade and target_grade == user_grade:
        score += 0.8
        if not reason:
            reason = f"面向{user_grade}的通知"

    # 4) 校区匹配
    campus = str(user.get("campus") or "")
    if campus and campus in text:
        score += 0.5
        if not reason:
            reason = f"{campus}相关"

    # 5) 时效衰减（7 天内线性 0.5 → 0）
    published = notice.get("publish_time")
    if isinstance(published, str):
        try:
            published = datetime.strptime(published, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            published = None
    if isinstance(published, datetime):
        days = max(0.0, (datetime.now() - published).total_seconds() / 86400)
        score += max(0.0, 0.5 * (1 - days / 7))

    # 6) 重要度（B19/B20 抽取结果 1~5；缺失或未打分按 0，不影响旧行为）
    try:
        importance = int(notice.get("importance") or 0)
    except (TypeError, ValueError):
        importance = 0
    importance = max(0, min(5, importance))
    if importance:
        score += 0.2 * importance
        if not reason:
            reason = f"重要度 {importance} 的通知"

    return {
        "score": round(score, 3),
        "matched_tags": matched,
        "reason": reason or "全校通用通知",
    }


def ensure_deliveries(user: dict) -> None:
    """为该用户生成/刷新投递记录（幂等 upsert）。"""
    uid = int(user["id"])
    tags = _user_tags(uid)
    behavior = _behavior_categories(uid)
    for notice in _visible_notices(user):
        r = score_notice(notice, user, tags, behavior)
        cpp_bridge.execute(
            "INSERT INTO notice_delivery (notice_id, user_id, matched_tags, score, reason) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON DUPLICATE KEY UPDATE matched_tags = VALUES(matched_tags), "
            "score = VALUES(score), reason = VALUES(reason)",
            [int(notice["id"]), uid, ",".join(r["matched_tags"]), r["score"], r["reason"][:255]],
        )


def unread_count(user: dict) -> int:
    """未读数（先刷新投递记录，保证口径一致）。"""
    ensure_deliveries(user)
    rows = cpp_bridge.query(
        "SELECT COUNT(*) AS c FROM notice_delivery WHERE user_id = ? AND is_read = 0",
        [int(user["id"])],
    )
    return int(rows[0]["c"]) if rows else 0


def unread_list(user: dict, page: int, size: int) -> tuple[list[dict], int]:
    """未读通知列表（按推荐得分倒序）。"""
    ensure_deliveries(user)
    uid = int(user["id"])
    rows = cpp_bridge.query(
        f"SELECT {notice_columns('n')}, "
        "d.score, d.reason, d.matched_tags, d.created_at AS delivered_at "
        "FROM notice_delivery d JOIN campus_notice n ON n.id = d.notice_id "
        "WHERE d.user_id = ? AND d.is_read = 0 "
        "ORDER BY d.score DESC, n.publish_time DESC LIMIT ? OFFSET ?",
        [uid, size, (page - 1) * size],
    )
    total = cpp_bridge.query(
        "SELECT COUNT(*) AS c FROM notice_delivery WHERE user_id = ? AND is_read = 0",
        [uid],
    )
    return rows, int(total[0]["c"]) if total else 0


def build_feed(user: dict, page: int, size: int) -> tuple[list[dict], int]:
    """个性化通知流（按得分倒序，带推荐理由）；同时标记曝光。"""
    ensure_deliveries(user)
    uid = int(user["id"])
    rows = cpp_bridge.query(
        f"SELECT {notice_columns('n')}, "
        "d.score, d.reason, d.matched_tags, d.is_read "
        "FROM notice_delivery d JOIN campus_notice n ON n.id = d.notice_id "
        "WHERE d.user_id = ? "
        "ORDER BY d.score DESC, n.publish_time DESC LIMIT ? OFFSET ?",
        [uid, size, (page - 1) * size],
    )
    total = cpp_bridge.query(
        "SELECT COUNT(*) AS c FROM notice_delivery WHERE user_id = ?", [uid]
    )
    # 曝光回执（首次曝光时间只写一次）
    cpp_bridge.execute(
        "UPDATE notice_delivery SET is_exposed = 1, exposed_at = IFNULL(exposed_at, NOW()) "
        "WHERE user_id = ? AND is_exposed = 0",
        [uid],
    )
    return rows, int(total[0]["c"]) if total else 0


def mark_read_batch(user: dict, notice_ids: list[int]) -> int:
    """批量标记已读（投递表 + 兼容旧 notice_read 口径），返回更新行数。"""
    ids = [int(i) for i in notice_ids if int(i) > 0]
    if not ids:
        return 0
    uid = int(user["id"])
    placeholders = ",".join("?" for _ in ids)
    updated, _ = cpp_bridge.execute(
        f"UPDATE notice_delivery SET is_read = 1, read_at = NOW() "
        f"WHERE user_id = ? AND notice_id IN ({placeholders}) AND is_read = 0",
        [uid] + ids,
    )
    for nid in ids:  # 兼容旧已读回执表
        cpp_bridge.execute(
            "INSERT IGNORE INTO notice_read (notice_id, user_id) VALUES (?, ?)",
            [nid, uid],
        )
    return int(updated)
