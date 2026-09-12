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

_MAX_TAG_HITS = 3
_MAX_NOTICES = 200


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
        "SELECT id, title, content, source, category, target_grade, publish_time "
        "FROM campus_notice WHERE target_grade = '' OR target_grade = ? "
        "ORDER BY publish_time DESC LIMIT ?",
        [grade, _MAX_NOTICES],
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
        "SELECT n.id, n.title, n.content, n.source, n.category, n.target_grade, "
        "n.publish_time, d.score, d.reason, d.matched_tags, d.created_at AS delivered_at "
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
        "SELECT n.id, n.title, n.content, n.source, n.category, n.target_grade, "
        "n.publish_time, d.score, d.reason, d.matched_tags, d.is_read "
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
