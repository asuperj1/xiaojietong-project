"""RAG 检索服务：向量检索（bge-m3 + 向量库），失败降级关键词匹配。

职责：
- 索引构建：build_index() 全量 / index_doc(doc_id) 单篇，把 knowledge_doc
  分块（写入 knowledge_chunk）→ 向量化 → 写入向量库，并更新文档状态。
- 在线检索：retrieve() 优先向量检索（相似度阈值过滤），embedding/向量库
  不可用或未命中时降级为关键词 LIKE 匹配，保证链路始终可用。
- build_system_prompt() 供 chat 模块拼接 RAG 增强的 system prompt。
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from app.core.config import settings
from app.db import cpp_bridge
from app.services.chunker import chunk_hash, chunk_text, summarize
from app.services.embedder import embedder
from app.services.vector_store import get_vector_store

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- 检索 ----

_TERM_SPLIT_RE = re.compile(r"[\s,，、;；/]+")


async def _keyword_retrieve(question: str, top_k: int) -> list[dict]:
    """降级实现：关键词 LIKE 匹配（拆分关键词，任一命中即可）。"""
    if not question:
        return []
    # 拆词：按空白/常见分隔符；无分隔时取整串（兼容中文长句）
    terms = [t for t in _TERM_SPLIT_RE.split(question.strip()) if t][:5]
    if not terms:
        terms = [question[:20]]
    cond = " OR ".join("(title LIKE ? OR content LIKE ?)" for _ in terms)
    params: list[str] = []
    for t in terms:
        params += [f"%{t}%", f"%{t}%"]
    params.append(top_k)
    rows = cpp_bridge.query(
        f"SELECT title, category, LEFT(content, 200) AS content, source_url "
        f"FROM knowledge_doc WHERE status != 2 AND ({cond}) "
        f"ORDER BY updated_at DESC LIMIT ?",
        params,
    )
    return rows


def _format_hits(hits: list[dict]) -> list[dict]:
    """把向量命中统一为返回契约：{title, category, content, source_url, score}。"""
    return [
        {
            "title": h.get("title", ""),
            "category": h.get("category", ""),
            "content": summarize(h.get("content", "")),
            "source_url": h.get("source_url", ""),
            "score": h.get("score", 0.0),
        }
        for h in hits
    ]


async def retrieve(question: str, top_k: Optional[int] = None) -> list[dict]:
    """检索相关知识片段，返回 [{title, category, content, source_url, score?}]。

    优先向量检索；embedding/向量库不可用或未命中时降级关键词匹配。
    """
    if not question:
        return []
    top_k = top_k or settings.rag_top_k
    emb = await embedder.embed_one(question)
    if emb is not None:
        try:
            store = get_vector_store()
            hits = store.search(emb, top_k, settings.rag_score_threshold)
            if hits:
                return _format_hits(hits)
        except Exception as exc:  # noqa: BLE001 - 向量库异常降级
            logger.warning("向量检索异常，降级关键词：%s", exc)
    return await _keyword_retrieve(question, top_k)


async def build_system_prompt(question: str) -> tuple[str, list[dict]]:
    """构造 RAG 增强的 system prompt。返回 (prompt, sources)。"""
    sources = await retrieve(question)
    if not sources:
        return (
            "你是校捷通的校园智能助手，请用中文简洁回答。",
            [],
        )
    knowledge = "\n".join(
        f"[{s['category']}] {s['title']}: {s['content']}" for s in sources
    )
    prompt = (
        "你是校捷通的校园智能助手。请仅依据以下校园知识库回答，"
        "不要编造；若知识不足请明确说明建议咨询官方。\n\n"
        f"知识库：\n{knowledge}"
    )
    return prompt, sources


# ------------------------------------------------------------ 索引构建 ----

def _read_doc(doc_id: int) -> Optional[dict]:
    rows = cpp_bridge.query(
        "SELECT id, title, category, content, source_url, status "
        "FROM knowledge_doc WHERE id = ? AND status != 2",
        [doc_id],
    )
    return rows[0] if rows else None


async def index_doc(doc_id: int) -> dict:
    """单篇文档向量化。返回 {"doc_id", "chunks", "status"}。

    status 取值：ok（完成）/ not_found / embed_failed（embedding 不可用）/
    vector_failed（向量库写入失败）/ empty（无文本，直接标记完成）。
    """
    doc = _read_doc(doc_id)
    if doc is None:
        return {"doc_id": doc_id, "chunks": 0, "status": "not_found"}

    chunks = chunk_text(
        doc.get("content") or "",
        settings.rag_chunk_size,
        settings.rag_chunk_overlap,
    )
    if not chunks:
        # 无可分块文本：直接标记完成（避免卡在待向量化）
        cpp_bridge.execute(
            "UPDATE knowledge_doc SET status = 1, chunk_count = 0 WHERE id = ?",
            [doc_id],
        )
        return {"doc_id": doc_id, "chunks": 0, "status": "empty"}

    # 1) embedding（前置检查：失败不动 DB，保持 status=0 待重试）
    embeddings = await embedder.embed(chunks)
    if embeddings is None:
        return {"doc_id": doc_id, "chunks": len(chunks), "status": "embed_failed"}

    # 2) 向量库：先删旧向量，再写新向量（保证 status=1 时向量一定存在）
    ids = [f"{doc_id}_{i}" for i in range(len(chunks))]
    metas = [
        {
            "doc_id": doc_id,
            "seq": i,
            "title": doc.get("title", ""),
            "category": doc.get("category", ""),
            "source_url": doc.get("source_url", ""),
        }
        for i in range(len(chunks))
    ]
    try:
        store = get_vector_store()
        store.delete_by_doc_id(doc_id)
        store.add(ids, embeddings, metas, chunks)
    except Exception as exc:  # noqa: BLE001 - 向量库失败记录并返回
        logger.warning("向量库写入失败 doc_id=%s：%s", doc_id, exc)
        return {"doc_id": doc_id, "chunks": len(chunks), "status": "vector_failed"}

    # 3) DB 事务：重建 knowledge_chunk + 更新文档状态
    try:
        with cpp_bridge.begin():
            cpp_bridge.execute("DELETE FROM knowledge_chunk WHERE doc_id = ?", [doc_id])
            for i, c in enumerate(chunks):
                cpp_bridge.execute(
                    "INSERT INTO knowledge_chunk (doc_id, seq, content, chunk_hash) "
                    "VALUES (?, ?, ?, ?)",
                    [doc_id, i, c, chunk_hash(c)],
                )
            cpp_bridge.execute(
                "UPDATE knowledge_doc SET status = 1, chunk_count = ? WHERE id = ?",
                [len(chunks), doc_id],
            )
    except Exception as exc:  # noqa: BLE001 - 事务失败回滚
        logger.error("索引落库失败 doc_id=%s：%s", doc_id, exc)
        return {"doc_id": doc_id, "chunks": len(chunks), "status": "db_failed"}

    return {"doc_id": doc_id, "chunks": len(chunks), "status": "ok"}


async def build_index(
    doc_ids: Optional[list[int]] = None,
    force: bool = False,
) -> dict:
    """构建/重建知识库索引。

    Args:
        doc_ids: 仅处理指定文档；为空则处理全部 status=0 的待向量化文档。
        force: 清空向量库与 knowledge_chunk 后，把所有启用文档重置为待向量化重建。

    Returns:
        {"total", "ok", "failed", "details": [...]}
    """
    if force:
        try:
            get_vector_store().clear()
        except Exception as exc:  # noqa: BLE001
            logger.warning("清空向量库失败：%s", exc)
        cpp_bridge.execute("DELETE FROM knowledge_chunk")
        cpp_bridge.execute(
            "UPDATE knowledge_doc SET status = 0, chunk_count = 0 WHERE status = 1"
        )

    if doc_ids:
        placeholders = ",".join("?" for _ in doc_ids)
        rows = cpp_bridge.query(
            f"SELECT id FROM knowledge_doc WHERE status = 0 AND id IN ({placeholders})",
            [int(i) for i in doc_ids],
        )
    else:
        rows = cpp_bridge.query("SELECT id FROM knowledge_doc WHERE status = 0")

    details: list[dict] = []
    for r in rows:
        details.append(await index_doc(int(r["id"])))

    ok_count = sum(1 for d in details if d["status"] == "ok")
    return {
        "total": len(details),
        "ok": ok_count,
        "failed": len(details) - ok_count,
        "details": details,
    }
