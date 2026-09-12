"""管理端：指标 / 知识库 / 论坛审核 / 训练语料。

契约：docs/api.md §11（需管理员角色）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from app.core.deps import get_current_admin
from app.core.response import err_param, ok, paged
from app.db import cpp_bridge
from app.services import rag

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/metrics")
def metrics(_admin: dict = Depends(get_current_admin)):
    stats = cpp_bridge.pool_stats()
    users = cpp_bridge.query("SELECT COUNT(*) AS c FROM user WHERE is_deleted = 0")
    topics = cpp_bridge.query("SELECT COUNT(*) AS c FROM topic WHERE is_deleted = 0")
    return ok(
        {
            "users": int(users[0]["c"]),
            "topics": int(topics[0]["c"]),
            "db": {"pool": stats},
        }
    )


class KnowledgeIn(BaseModel):
    title: str
    category: str = ""
    content: str
    source_url: str = ""


@router.post("/knowledge/ingest")
async def ingest(body: KnowledgeIn, _admin: dict = Depends(get_current_admin)):
    """知识入库：写 knowledge_doc → 自动分块向量化（embedding 不可用则待重建）。"""
    if not body.title.strip() or not body.content.strip():
        raise err_param("标题/内容不能为空")
    rows = cpp_bridge.execute(
        "INSERT INTO knowledge_doc (title, category, content, source_url, status) "
        "VALUES (?, ?, ?, ?, 0)",
        [body.title, body.category, body.content, body.source_url],
    )
    doc_id = rows[1]
    result = await rag.index_doc(doc_id)
    return ok({"doc_id": doc_id, "chunks": result["chunks"], "status": result["status"]})


@router.post("/knowledge/index")
def knowledge_index(force: bool = False, _admin: dict = Depends(get_current_admin)):
    """重建知识库索引（B15：Celery 异步执行）。

    - Celery 启用：立即返回 `{async, celery_task_id}`，用
      `GET /admin/knowledge/index-status/{task_id}` 查询进度；
    - 未启用（无 Redis）：eager 同步执行并直接返回构建结果（与旧版一致）。
    """
    from app.core.config import settings
    from app.tasks import build_knowledge_index

    task = build_knowledge_index.delay(force)
    if settings.celery_enabled:
        return ok(
            {
                "async": True,
                "celery_task_id": task.id,
                "note": "索引构建已投递，可用 /admin/knowledge/index-status/{task_id} 查询",
            }
        )
    return ok(task.result)


@router.get("/knowledge/index-status/{task_id}")
def knowledge_index_status(task_id: str, _admin: dict = Depends(get_current_admin)):
    """查询异步索引构建状态（B15）。"""
    from app.tasks import task_state

    return ok(task_state(task_id))


@router.get("/forum/audit")
def pending_audit(
    limit: int = Query(50, ge=1, le=200),
    _admin: dict = Depends(get_current_admin),
):
    rows = cpp_bridge.forum_dao().pending_audit(limit)
    return ok({"items": rows})


class AuditBatchIn(BaseModel):
    """批量审核入参（topic_ids + 通过与否）。"""

    model_config = ConfigDict(populate_by_name=True)

    topic_ids: list[int]
    pass_flag: bool = Field(default=True, alias="pass")
    summary: str = ""


@router.post("/forum/audit/batch")
def audit_batch(body: AuditBatchIn, _admin: dict = Depends(get_current_admin)):
    """批量审核（B6）：一次通过/拒绝多个帖子；summary 非空时统一写入。"""
    if not body.topic_ids:
        raise err_param("topic_ids 不能为空")
    status = 1 if body.pass_flag else 2
    with cpp_bridge.begin():
        for tid in body.topic_ids:
            cpp_bridge.execute(
                "UPDATE topic SET audit_status = ?, "
                "ai_summary = IF(? = '', ai_summary, ?) WHERE id = ?",
                [status, body.summary, body.summary, tid],
            )
    return ok({"processed": len(body.topic_ids), "audit_status": status})


class AuditIn(BaseModel):
    """人工审核入参（字段名 pass 与契约一致，内部用 pass_flag 承载）。"""

    model_config = ConfigDict(populate_by_name=True)

    pass_flag: bool = Field(default=True, alias="pass")
    summary: str = ""


@router.post("/forum/audit/{topic_id}")
def audit(topic_id: int, body: AuditIn, _admin: dict = Depends(get_current_admin)):
    status = 1 if body.pass_flag else 2
    cpp_bridge.execute(
        "UPDATE topic SET audit_status = ?, ai_summary = ? WHERE id = ?",
        [status, body.summary, topic_id],
    )
    return ok({"topic_id": topic_id, "audit_status": status})


@router.get("/train/corpus")
def train_corpus(
    source_type: str = "",
    is_cleaned: int | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _admin: dict = Depends(get_current_admin),
):
    sql = "SELECT * FROM train_corpus WHERE 1=1 "
    params: list = []
    if source_type:
        sql += "AND source_type = ? "
        params.append(source_type)
    if is_cleaned is not None:
        sql += "AND is_cleaned = ? "
        params.append(is_cleaned)
    sql += "ORDER BY id DESC LIMIT ? OFFSET ?"
    params += [size, (page - 1) * size]
    rows = cpp_bridge.query(sql, params)
    return ok(paged(rows, len(rows), page, size))
