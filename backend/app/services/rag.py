"""RAG 检索服务：向量检索（bge-m3 + 向量库），失败降级关键词匹配。

职责：
- 索引构建：build_index() 全量 / index_doc(doc_id) 单篇，把 knowledge_doc
  分块（写入 knowledge_chunk）→ 向量化 → 写入向量库，并更新文档状态。
- 在线检索：retrieve() 优先向量检索（相似度阈值过滤），embedding/向量库
  不可用或未命中时降级为关键词 LIKE 匹配，保证链路始终可用。
- build_system_prompt() 供 chat 模块拼接 RAG 增强的 system prompt。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Optional

from app.core.config import settings
from app.db import cpp_bridge
from app.services.chunker import chunk_hash, chunk_text, summarize
from app.services.embedder import embedder
from app.services.vector_store import get_vector_store

logger = logging.getLogger(__name__)

# ---------- 轻量进程内缓存与并发保护（审计 CAC-01 / CAC-02 / CAC-04；无需引入 Redis） ----------
# 检索结果缓存：热点问题（如「图书馆几点关门」）在 TTL 内直接命中，避免重复
# embedding + 向量检索 + 降级全表扫。
_RETRIEVE_CACHE: dict[tuple[str, int], tuple[float, list[dict]]] = {}
_CACHE_TTL = 60.0      # 秒（有结果的缓存时长）
_EMPTY_TTL = 15.0      # 秒（空结果用更短 TTL：知识刚入库时不应长时间查不到）
_CACHE_MAX = 512       # 条目上限（超限时清掉前半，简单且零依赖）

# 缓存击穿（single-flight）合并表：记录「正在回源中」的 key → Future。
# 审计 CAC-02：缓存未命中或刚过期的瞬间，N 个并发相同提问会同时回源，
# 每个都跑一次 embedding + 向量检索 + （降级时）LONGTEXT 全表扫。
# 热点问题在演示/答辩场景下极易出现，必须把并发回源合并为一次。
_INFLIGHT: dict[tuple[str, int], "asyncio.Future[list[dict]]"] = {}

# 降级路径并发闸门：关键词检索是对 knowledge_doc.content(LONGTEXT) 的全表扫描，
# 必须限流。否则 Ollama 抖动时（熔断 30s）所有对话请求会同时全表扫并打满连接池，
# 把 AI 故障放大成全站雪崩。
_KEYWORD_SEM = asyncio.Semaphore(2)


def _cache_get(key: tuple[str, int]) -> Optional[list[dict]]:
    hit = _RETRIEVE_CACHE.get(key)
    if not hit:
        return None
    ts, val = hit
    # 空结果用更短 TTL：否则「先问了没收录的问题 → 知识刚录入 → 仍查不到」
    ttl = _CACHE_TTL if val else _EMPTY_TTL
    if time.monotonic() - ts > ttl:
        _RETRIEVE_CACHE.pop(key, None)
        return None
    return val


def _cache_put(key: tuple[str, int], val: list[dict]) -> None:
    if len(_RETRIEVE_CACHE) >= _CACHE_MAX:
        for k in list(_RETRIEVE_CACHE)[: _CACHE_MAX // 2]:
            _RETRIEVE_CACHE.pop(k, None)
    _RETRIEVE_CACHE[key] = (time.monotonic(), val)


# ---------------------------------------------------------------- 检索 ----

_TERM_SPLIT_RE = re.compile(r"[\s,，、;；/]+")


async def _keyword_retrieve(question: str, top_k: int) -> list[dict]:
    """降级实现：关键词 LIKE 匹配（拆分关键词，任一命中即可）。

    审计 CAC-01 修复点：
      ① 原实现在 `async def` 内**同步**调用 `cpp_bridge.query`，会直接阻塞事件循环；
         现改为 `asyncio.to_thread` 丢到工作线程执行。
      ② 原实现无并发限制；现用 `_KEYWORD_SEM` 把降级查询并发限制在 2，
         避免故障期间打满连接池拖垮全站。
    """
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

    def _run() -> list[dict]:
        return cpp_bridge.query(
            f"SELECT title, category, LEFT(content, 200) AS content, source_url "
            f"FROM knowledge_doc WHERE status != 2 AND ({cond}) "
            f"ORDER BY updated_at DESC LIMIT ?",
            params,
        )

    async with _KEYWORD_SEM:
        return await asyncio.to_thread(_run)


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


async def _retrieve_uncached(question: str, top_k: int) -> list[dict]:
    """实际回源逻辑（不读缓存）。

    优先向量检索；embedding/向量库不可用或未命中时降级关键词匹配。
    """
    result: list[dict] = []
    emb = await embedder.embed_one(question)
    if emb is not None:
        try:
            store = get_vector_store()
            hits = await asyncio.to_thread(
                store.search, emb, top_k, settings.rag_score_threshold
            )
            if hits:
                result = _format_hits(hits)
        except Exception as exc:  # noqa: BLE001 - 向量库异常降级
            logger.warning("向量检索异常，降级关键词：%s", exc)
    if not result:
        result = await _keyword_retrieve(question, top_k)
    return result


async def retrieve(question: str, top_k: Optional[int] = None) -> list[dict]:
    """检索相关知识片段，返回 [{title, category, content, source_url, score?}]。

    • 结果带进程内 TTL 缓存（CAC-04：热点问题不再重复回源；空值短 TTL）
    • **single-flight 合并**（CAC-02）：同一提问的并发请求只回源一次，
      避免缓存未命中/刚过期瞬间 N 个请求同时打向量库与全表扫
    • 向量库检索为同步 CPU/IO，丢到线程池执行，不阻塞事件循环
    """
    if not question:
        return []
    top_k = top_k or settings.rag_top_k

    key = (question.strip().lower(), top_k)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    # 已有同 key 的回源在进行中 → 直接等它的结果（请求合并）
    pending = _INFLIGHT.get(key)
    if pending is not None:
        # shield：等待方被取消时不影响其他等待者
        return await asyncio.shield(pending)

    loop = asyncio.get_running_loop()
    fut: "asyncio.Future[list[dict]]" = loop.create_future()
    _INFLIGHT[key] = fut
    try:
        result = await _retrieve_uncached(question, top_k)
    except BaseException as exc:  # noqa: BLE001 - 含 CancelledError，保证不遗留 inflight
        if not fut.done():
            fut.set_exception(exc)
            fut.exception()  # 无人 await 时消费掉，避免 "exception was never retrieved"
        raise
    finally:
        _INFLIGHT.pop(key, None)

    _cache_put(key, result)
    if not fut.done():
        fut.set_result(result)
    return result


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
        doc_ids: 仅处理指定文档；为空则按 status 筛选待处理文档。
        force: 强制重建（含 status=1 的已索引文档），**覆盖式**，见下方说明。

    Returns:
        {"total", "ok", "failed", "details": [...], "orphans_removed"}

    ── 审计 CAC-07 修复说明 ──
    原实现 `force` 会先 `store.clear()` + `DELETE FROM knowledge_chunk`
    + 把所有文档 status 重置为 0，再逐篇重建，带来两个严重问题：

      ① **检索空窗**：从清空到全部重建完成可能持续数分钟（每篇都要 embedding），
         期间 `/chat` 的 RAG 检索查不到任何知识，AI 只能答"知识库无相关信息"；
      ② **不可回滚**：若中途 embedding 失败（Ollama 未就绪），旧索引已删除，
         系统停在"半空"状态且无法恢复。

    现改为**覆盖式重建**：不清空，直接对目标文档重跑 `index_doc` —— 其内部
    本就按 doc_id 覆盖写入（向量库 `delete_by_doc_id` + `add`；`knowledge_chunk`
    亦为 `DELETE WHERE doc_id` 后重插），因此：

      • 重建全程旧向量仍可检索，**无空窗**；
      • 某篇失败只影响该篇，其它文档索引保持完好，**可重入**；
      • 收尾统一清理**孤儿向量**（DB 中已删除文档的残留）。
    """
    placeholders = ",".join("?" for _ in doc_ids) if doc_ids else ""
    if doc_ids and force:
        # 指定文档强制重建：忽略 status
        target_rows = cpp_bridge.query(
            f"SELECT id FROM knowledge_doc WHERE status != 2 AND id IN ({placeholders})",
            [int(i) for i in doc_ids],
        )
    elif doc_ids:
        target_rows = cpp_bridge.query(
            f"SELECT id FROM knowledge_doc WHERE status = 0 AND id IN ({placeholders})",
            [int(i) for i in doc_ids],
        )
    elif force:
        target_rows = cpp_bridge.query("SELECT id FROM knowledge_doc WHERE status != 2")
    else:
        target_rows = cpp_bridge.query("SELECT id FROM knowledge_doc WHERE status = 0")

    details: list[dict] = []
    for r in target_rows:
        details.append(await index_doc(int(r["id"])))

    # 仅全量重建时才清理孤儿向量：只重建部分文档时不应动其他文档的向量
    orphans_removed = _cleanup_orphan_vectors() if (force and not doc_ids) else 0

    ok_count = sum(1 for d in details if d["status"] == "ok")
    return {
        "total": len(details),
        "ok": ok_count,
        "failed": len(details) - ok_count,
        "details": details,
        "orphans_removed": orphans_removed,
    }


def _cleanup_orphan_vectors() -> int:
    """清理向量库中已不存在于 DB 的文档向量（覆盖式重建的收尾动作）。

    覆盖式重建不再先 `clear()`，因此已删除文档的残留向量需要显式回收。
    """
    try:
        store = get_vector_store()
        vector_doc_ids = store.list_doc_ids()
        if not vector_doc_ids:
            return 0
        alive = {
            int(r["id"])
            for r in cpp_bridge.query("SELECT id FROM knowledge_doc WHERE status != 2")
        }
        orphans = vector_doc_ids - alive
        for doc_id in orphans:
            store.delete_by_doc_id(doc_id)
        if orphans:
            logger.info(
                "清理孤儿向量：%d 个文档 %s", len(orphans), sorted(orphans)[:20]
            )
        return len(orphans)
    except Exception as exc:  # noqa: BLE001 - 收尾失败不影响重建结果
        logger.warning("清理孤儿向量失败：%s", exc)
        return 0
