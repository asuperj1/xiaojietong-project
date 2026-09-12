"""帖子个性化推荐（B11）：让 feedback / like_record / user_tag 的数据真正被消费。

替代原「纯时间序」占位。打分维度（可解释）：
1. **兴趣标签命中**：``user_tag`` 出现在标题/正文/分类 → 每个 +1.5（上限 3 个）；
2. **行为偏好**：用户点赞/收藏过的帖子分类命中候选帖分类 → +0.8；
3. **热度**：``like_count × 1.0 + comment_count × 1.5 + view_count × 0.05``，
   归一化后上限 +2.0；
4. **时效加成**：3 天内线性衰减 0.8 → 0（新帖优先）。

推荐理由 ``reason`` 取最高权重命中：
「因为你关注了{标签}」→「你常看{分类}类内容」→「校园热帖 / 新发布的帖子 / 综合推荐」。

**冷启动**：无标签且无行为数据时，自然回落为纯热度排序（reason=校园热帖），不报错。
"""

from __future__ import annotations

from datetime import datetime

from app.db import cpp_bridge

_MAX_TAG_HITS = 3
_CANDIDATE_LIMIT = 200  # 候选集上限（打分在内存完成，再切片分页）


def _user_tags(user_id: int) -> list[str]:
    rows = cpp_bridge.query("SELECT tag FROM user_tag WHERE user_id = ?", [user_id])
    return [str(r.get("tag") or "") for r in rows if r.get("tag")]


def _behavior_categories(user_id: int) -> set[str]:
    """用户互动过的帖子分类（点赞 + 收藏）。"""
    rows = cpp_bridge.query(
        "SELECT DISTINCT t.category FROM like_record l JOIN topic t ON t.id = l.target_id "
        "WHERE l.user_id = ? AND l.target_type = 'topic' "
        "UNION "
        "SELECT DISTINCT t.category FROM favorite f JOIN topic t ON t.id = f.target_id "
        "WHERE f.user_id = ? AND f.target_type = 'topic'",
        [user_id, user_id],
    )
    return {str(r.get("category") or "") for r in rows if r.get("category")}


def _candidates() -> list[dict]:
    """候选帖：已审核通过、未删除、未锁定，按发布时间取最近 N 条。"""
    return cpp_bridge.query(
        "SELECT t.id, t.title, t.content, t.category, t.like_count, t.comment_count, "
        "t.view_count, t.is_hot, t.created_at, u.nickname AS author_name "
        "FROM topic t JOIN user u ON t.author_id = u.id "
        "WHERE t.audit_status = 1 AND t.is_deleted = 0 AND t.status = 0 "
        "ORDER BY t.id DESC LIMIT ?",
        [_CANDIDATE_LIMIT],
    )


def score_topic(topic: dict, tags: list[str], behavior: set[str]) -> dict:
    """单条帖子对用户的推荐得分、命中标签与可读理由。"""
    text = (
        f"{topic.get('title') or ''}{topic.get('content') or ''}"
        f"{topic.get('category') or ''}"
    )
    score = 0.0
    matched: list[str] = []
    reason = ""

    # 1) 兴趣标签命中
    for tag in tags:
        if tag and tag in text and len(matched) < _MAX_TAG_HITS:
            matched.append(tag)
            score += 1.5
            if not reason:
                reason = f"因为你关注了{tag}"

    # 2) 行为偏好
    category = str(topic.get("category") or "")
    if category and category in behavior:
        score += 0.8
        if not reason:
            reason = f"你常看{category}类内容"

    # 3) 热度（归一化上限 2.0）
    hot = (
        int(topic.get("like_count") or 0) * 1.0
        + int(topic.get("comment_count") or 0) * 1.5
        + int(topic.get("view_count") or 0) * 0.05
    )
    score += min(2.0, hot / 10.0)

    # 4) 时效加成（3 天内线性 0.8 → 0）
    created = topic.get("created_at")
    if isinstance(created, str):
        try:
            created = datetime.strptime(created, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            created = None
    days = 999.0
    if isinstance(created, datetime):
        days = max(0.0, (datetime.now() - created).total_seconds() / 86400)
        score += max(0.0, 0.8 * (1 - days / 3))

    if not reason:
        if int(topic.get("is_hot") or 0) == 1 or hot >= 5:
            reason = "校园热帖"
        elif days <= 3:
            reason = "新发布的帖子"
        else:
            reason = "综合推荐"

    return {
        "score": round(score, 3),
        "matched_tags": matched,
        "reason": reason,
    }


def build_feed(user: dict, page: int, size: int) -> tuple[list[dict], int]:
    """个性化帖子流：打分排序（得分倒序，同分取新帖），返回 (slice, total)。"""
    uid = int(user["id"])
    tags = _user_tags(uid)
    behavior = _behavior_categories(uid)

    scored: list[dict] = []
    for topic in _candidates():
        r = score_topic(topic, tags, behavior)
        item = dict(topic)
        item["score"] = r["score"]
        item["reason"] = r["reason"]
        item["matched_tags"] = ",".join(r["matched_tags"])
        scored.append(item)

    # 冷启动兜底：候选为空也正常返回空列表（前端展示空态），不报错
    scored.sort(key=lambda x: (-float(x["score"]), -int(x["id"])))

    total = len(scored)
    start = (page - 1) * size
    return scored[start : start + size], total
