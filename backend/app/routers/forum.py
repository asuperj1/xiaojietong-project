"""论坛：帖子 / 评论 / 点赞 / 举报 / 热点。

契约：docs/api.md §8
审核（B6）：发帖 / 评论经 services/audit.py 判定（词库规则 + 模型二次判定）；
拒绝返回错误码 3003；通过立即可见；可疑内容转人工待审。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core.deps import get_current_user
from app.core.response import BizError, err_audit, err_param, ok, paged
from app.core.view_counter import view_counter
from app.db import cpp_bridge
from app.services.audit import audit_content, status_of

router = APIRouter(prefix="/topics", tags=["forum"])


@router.get("")
def list_topics(
    category: str = "",
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    items = cpp_bridge.forum_dao().page_topics(page, size, category, audited_only=True)
    return ok(paged(items, len(items), page, size))


class CreateTopicIn(BaseModel):
    title: str
    content: str
    category: str = "综合"


@router.post("")
async def create_topic(body: CreateTopicIn, user: dict = Depends(get_current_user)):
    if not body.title.strip():
        raise err_param("标题不能为空")
    # 先入库拿到 topic_id（审核留痕 audit_log 需要 target_id），再审核回写状态
    topic_id = cpp_bridge.forum_dao().create_topic(
        int(user["id"]), body.title, body.content, body.category
    )
    # B6 内容审核：规则 + 模型二次判定（模型不可用自动降级规则，不阻塞发帖）
    verdict = await audit_content(
        f"{body.title}\n{body.content}",
        target_type="topic",
        target_id=topic_id,
        user_id=int(user["id"]),
    )
    audit_status = status_of(verdict["level"])
    cpp_bridge.execute(
        "UPDATE topic SET audit_status = ?, ai_summary = ? WHERE id = ?",
        [audit_status, verdict["summary"], topic_id],
    )
    if verdict["level"] == "block":
        # 拒绝：入库标记 audit_status=2（发布者可在"我的帖子"看到），返回 3003
        raise err_audit(verdict["reason"] or "内容未通过审核")
    return ok(
        {
            "topic_id": topic_id,
            "audit_status": audit_status,
            "ai_summary": verdict["summary"],
            "source": verdict["source"],
        }
    )


@router.get("/hot")
def hot_topics(limit: int = Query(20, ge=1, le=50), user: dict = Depends(get_current_user)):
    return ok({"items": cpp_bridge.forum_dao().hot_topics(limit)})


@router.get("/feed")
def topic_feed(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    """个性化推荐（占位）：先按时间序，后续接入用户标签/浏览历史。"""
    items = cpp_bridge.forum_dao().page_topics(page, size, "", audited_only=True)
    return ok(paged(items, len(items), page, size))


@router.get("/mine")
def my_topics(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    """我的帖子（含待审/被拒状态，便于前端展示审核进度）。"""
    uid = int(user["id"])
    offset = (page - 1) * size
    rows = cpp_bridge.query(
        "SELECT t.id, t.title, t.category, t.like_count, t.comment_count, t.view_count, "
        "t.audit_status, t.ai_summary, t.is_hot, t.status, t.created_at "
        "FROM topic t WHERE t.author_id = ? AND t.is_deleted = 0 AND t.status != 1 "
        "ORDER BY t.id DESC LIMIT ? OFFSET ?",
        [uid, size, offset],
    )
    total = cpp_bridge.query(
        "SELECT COUNT(*) AS total FROM topic t "
        "WHERE t.author_id = ? AND t.is_deleted = 0 AND t.status != 1",
        [uid],
    )
    for r in rows:
        r["author_name"] = user.get("nickname", "")
    return ok(paged(rows, int(total[0]["total"]) if total else 0, page, size))


@router.get("/{topic_id}")
def topic_detail(topic_id: int, user: dict = Depends(get_current_user)):
    rows = cpp_bridge.query(
        "SELECT t.*, u.nickname AS author_name FROM topic t "
        "JOIN user u ON t.author_id = u.id "
        "WHERE t.id = ? AND t.status = 0 AND t.is_deleted = 0",
        [topic_id],
    )
    if not rows:
        raise BizError(1001, "帖子不存在")
    topic = rows[0]
    comments = cpp_bridge.query(
        "SELECT c.id, c.content, c.created_at, u.nickname AS author_name "
        "FROM comment c JOIN user u ON c.author_id = u.id "
        "WHERE c.topic_id = ? AND c.is_deleted = 0 ORDER BY c.id",
        [topic_id],
    )
    liked_rows = cpp_bridge.query(
        "SELECT id FROM like_record WHERE user_id = ? AND target_type = 'topic' AND target_id = ?",
        [int(user["id"]), topic_id],
    )
    topic["liked"] = bool(liked_rows)
    fav_rows = cpp_bridge.query(
        "SELECT id FROM favorite WHERE user_id = ? AND target_type = 'topic' AND target_id = ?",
        [int(user["id"]), topic_id],
    )
    topic["favorited"] = bool(fav_rows)
    topic["comments"] = comments
    # 浏览量 +1（审计 CAC-03）：读路径只做内存自增，由后台任务周期批量落库，
    # 避免热门帖被并发浏览时所有请求争用同一行的排他锁（write hotspot）。
    view_counter.bump(topic_id)
    return ok(topic)


@router.get("/{topic_id}/audit-status")
def topic_audit_status(topic_id: int, user: dict = Depends(get_current_user)):
    """审核状态轮询（B6）：供前端发帖后确认是否可见。"""
    rows = cpp_bridge.query(
        "SELECT id, audit_status, ai_summary FROM topic WHERE id = ? AND is_deleted = 0",
        [topic_id],
    )
    if not rows:
        raise BizError(1001, "帖子不存在")
    row = rows[0]
    status = int(row["audit_status"])
    return ok(
        {
            "topic_id": topic_id,
            "audit_status": status,
            "ai_summary": row["ai_summary"],
            "passed": status == 1,
        }
    )


class CommentIn(BaseModel):
    content: str


@router.post("/{topic_id}/comments")
async def add_comment(topic_id: int, body: CommentIn, user: dict = Depends(get_current_user)):
    if not body.content.strip():
        raise err_param("评论不能为空")
    verdict = await audit_content(
        body.content, target_type="comment", target_id=0, user_id=int(user["id"])
    )
    if verdict["level"] == "block":
        raise err_audit(verdict["reason"] or "评论未通过审核")
    comment_id = cpp_bridge.forum_dao().add_comment(topic_id, int(user["id"]), body.content)
    return ok({"comment_id": comment_id, "audit_status": status_of(verdict["level"])})


@router.post("/{topic_id}/like")
def like(topic_id: int, user: dict = Depends(get_current_user)):
    liked = cpp_bridge.forum_dao().toggle_like(int(user["id"]), "topic", topic_id)
    rows = cpp_bridge.query(
        "SELECT like_count FROM topic WHERE id = ?", [topic_id]
    )
    like_count = int(rows[0]["like_count"]) if rows else 0
    return ok({"topic_id": topic_id, "liked": liked, "like_count": like_count})


class ReportIn(BaseModel):
    reason: str = ""


@router.post("/{topic_id}/report")
def report(topic_id: int, body: ReportIn, user: dict = Depends(get_current_user)):
    with cpp_bridge.begin():
        # C8 修复：last_insert_id 必须取自本次 execute 的返回值。
        # 原写法在事务提交后用 query("SELECT LAST_INSERT_ID()") 取值，而 query() 会从
        # 连接池取到「另一条连接」——该连接的 LAST_INSERT_ID 与本次插入无关
        # （通常为 0 或他人最近一次插入的 id），导致返回值不可信。
        _, report_id = cpp_bridge.execute(
            "INSERT INTO report (reporter_id, target_type, target_id, reason) VALUES (?, 'topic', ?, ?)",
            [int(user["id"]), topic_id, body.reason],
        )
    return ok({"report_id": report_id})
