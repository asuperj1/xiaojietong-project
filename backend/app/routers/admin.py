"""管理端：指标 / 知识库 / 论坛审核 / 训练语料。

契约：docs/api.md §11（需管理员角色）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

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
async def knowledge_index(
    force: bool = False,
    _admin: dict = Depends(get_current_admin),
):
    """重建知识库索引（force=True 全量重建；默认只处理待向量化文档）。"""
    result = await rag.build_index(force=force)
    return ok(result)


@router.get("/forum/audit")
def pending_audit(
    limit: int = Query(50, ge=1, le=200),
    _admin: dict = Depends(get_current_admin),
):
    rows = cpp_bridge.forum_dao().pending_audit(limit)
    return ok({"items": rows})


class AuditIn(BaseModel):
    pass_flag: bool = True
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
