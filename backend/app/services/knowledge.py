"""知识库入库服务（B16/B17 共用）：写 knowledge_doc → 分块向量化。

被两处复用：
- ``routers/admin.py`` 的 ``POST /admin/knowledge/ingest``（单篇，JSON 或文件上传）；
- ``app/cli/kb_import.py``（B17 批量导入管道，命令行调用同一套函数，保证口径一致）。

关键约定：
- **去重口径**：以 ``source_url`` 为稳定业务键（批量导入时填文件的相对路径/URL）。
  ``dedup=True`` 时同键文档按正文比对——内容一致直接跳过（断点续传的基础），
  内容变化需 ``overwrite=True`` 才覆盖，否则报 ``conflict`` 让人工确认。
- **索引解耦**：``index=False`` 只入库不向量化（status=0 待向量化），
  适合先批量灌数据、再统一跑 ``POST /admin/knowledge/index``；
  这样批量导入的耗时与 embedding 服务可用性解耦。
"""

from __future__ import annotations

from typing import Optional

from app.core.config import settings
from app.db import cpp_bridge
from app.services import rag
from app.services.parser import ParsedDoc

MAX_TITLE_CHARS = 200
_DOC_COLUMNS = "id, title, category, content, source_url, chunk_count, status, updated_at"


def normalize_source(source: str) -> str:
    """归一化来源标识（统一分隔符，去掉过长内容）。"""
    return (source or "").strip().replace("\\", "/")[:255]


# 批量删除护栏（PR #60 审查 P1）：
#   · 前缀里的 % / _ / \ 必须转义，否则 `a_b` 会误伤 `aXb`、`%` 会**匹配全库**；
#   · 前缀过短（如 "/" "a"）等价于无差别删除，直接拒绝。
MIN_PURGE_PREFIX_CHARS = 3


def like_prefix_pattern(prefix: str) -> str:
    """把来源前缀转成**字面** LIKE 模式（配合 ``ESCAPE '\\\\'`` 使用）。"""
    escaped = (
        prefix.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return escaped + "%"


def find_by_source(source_url: str) -> Optional[dict]:
    """按 source_url 查已入库文档（去重/续传判定用）。"""
    src = normalize_source(source_url)
    if not src:
        return None
    rows = cpp_bridge.query(
        f"SELECT {_DOC_COLUMNS} FROM knowledge_doc WHERE source_url = ? AND status != 2 LIMIT 1",
        [src],
    )
    return rows[0] if rows else None


def get_doc(doc_id: int) -> Optional[dict]:
    """按 id 取文档。"""
    rows = cpp_bridge.query(
        f"SELECT {_DOC_COLUMNS} FROM knowledge_doc WHERE id = ? LIMIT 1", [int(doc_id)]
    )
    return rows[0] if rows else None


def _same_content(existing: Optional[dict], content: str) -> bool:
    """正文是否一致（忽略首尾空白差异）。"""
    if not existing:
        return False
    return (existing.get("content") or "").strip() == (content or "").strip()


async def ingest_document(
    doc: ParsedDoc,
    *,
    title: str = "",
    category: str = "",
    source_url: str = "",
    index: bool = True,
    dedup: bool = False,
    overwrite: bool = False,
) -> dict:
    """把解析结果写入知识库（可选立即向量化）。

    Args:
        doc: 解析器产出。
        title: 覆盖标题（留空用解析出的标题）。
        category: 分类（如「图书馆」「校历」「办事流程」）。
        source_url: 稳定业务键（相对路径 / 原始链接）。
        index: 是否立即分块向量化（False 则 status=0 待向量化）。
        dedup: 是否按 source_url 去重。
        overwrite: 同 source_url 且内容变化时是否覆盖（dedup=True 时生效）。

    Returns:
        ``{doc_id, action, status, chunks, title, source_url}``；
        action ∈ created / updated / skipped / conflict。
    """
    final_title = (title or doc.title or "").strip()[:MAX_TITLE_CHARS]
    if not final_title:
        final_title = "未命名文档"
    content = doc.content or ""
    if not content.strip():
        return {
            "doc_id": 0,
            "action": "empty",
            "status": "empty",
            "chunks": 0,
            "title": final_title,
            "source_url": normalize_source(source_url),
            "warnings": ["解析结果为空文本，未入库"],
        }

    src = normalize_source(source_url)
    existing = find_by_source(src) if (dedup and src) else None
    if existing and dedup:
        if _same_content(existing, content):
            return {
                "doc_id": int(existing["id"]),
                "action": "skipped",
                "status": "unchanged",
                "chunks": int(existing.get("chunk_count") or 0),
                "title": str(existing.get("title") or final_title),
                "source_url": src,
                "warnings": [],
            }
        if not overwrite:
            return {
                "doc_id": int(existing["id"]),
                "action": "conflict",
                "status": "conflict",
                "chunks": int(existing.get("chunk_count") or 0),
                "title": str(existing.get("title") or final_title),
                "source_url": src,
                "warnings": ["同来源文档内容已变化，未覆盖（需 overwrite=True）"],
            }
        doc_id = int(existing["id"])
        cpp_bridge.execute(
            "UPDATE knowledge_doc SET title = ?, category = ?, content = ?, status = 0, "
            "chunk_count = 0 WHERE id = ?",
            [final_title, category, content, doc_id],
        )
        action = "updated"
    else:
        _, doc_id = cpp_bridge.execute(
            "INSERT INTO knowledge_doc (title, category, content, source_url, status) "
            "VALUES (?, ?, ?, ?, 0)",
            [final_title, category, content, src],
        )
        doc_id = int(doc_id)
        action = "created"

    result = {
        "doc_id": doc_id,
        "action": action,
        "status": "pending" if not index else "",
        "chunks": 0,
        "title": final_title,
        "source_url": src,
        "warnings": list(doc.warnings),
    }
    if index:
        indexed = await rag.index_doc(doc_id)
        result["status"] = indexed.get("status", "")
        result["chunks"] = int(indexed.get("chunks") or 0)
    return result


async def ingest_text(
    *,
    title: str,
    content: str,
    category: str = "",
    source_url: str = "",
    index: bool = True,
    dedup: bool = False,
    overwrite: bool = False,
) -> dict:
    """直接入库一段文本（管理端 JSON 路径使用）。"""
    from app.services.parser.base import finalize

    doc = finalize(
        content,
        title=title,
        fmt="text",
        fallback_title=title or "未命名文档",
        meta={"engine": "inline-json"},
    )
    return await ingest_document(
        doc,
        title=title,
        category=category,
        source_url=source_url,
        index=index,
        dedup=dedup,
        overwrite=overwrite,
    )


# ------------------------------------------------------------ 维护操作 ----

def list_docs(
    *,
    page: int = 1,
    size: int = 20,
    status: Optional[int] = None,
    category: str = "",
    keyword: str = "",
) -> tuple[list[dict], int]:
    """分页列出知识文档（管理端 / B17 进度核对）。返回 (items, total)。"""
    where = "WHERE status != 2 "
    params: list = []
    if status is not None:
        where += "AND status = ? "
        params.append(int(status))
    if category:
        where += "AND category = ? "
        params.append(category)
    if keyword:
        where += "AND (title LIKE ? OR content LIKE ?) "
        params += [f"%{keyword}%", f"%{keyword}%"]
    total_rows = cpp_bridge.query(f"SELECT COUNT(*) AS c FROM knowledge_doc {where}", params)
    rows = cpp_bridge.query(
        f"SELECT id, title, category, source_url, chunk_count, status, updated_at "
        f"FROM knowledge_doc {where} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [int(size), int((max(1, page) - 1) * size)],
    )
    # jt_db 可能把 BIGINT 列返回为字符串：显式转成 JSON 数字，
    # 否则接口契约（id/chunk_count/status 为数字）与前端比较都会出问题。
    for r in rows:
        r["id"] = int(r.get("id") or 0)
        r["chunk_count"] = int(r.get("chunk_count") or 0)
        r["status"] = int(r.get("status") or 0)
    return rows, int(total_rows[0]["c"]) if total_rows else 0


def delete_doc(doc_id: int, *, purge_vectors: bool = True) -> bool:
    """删除文档（含分块与向量）。返回是否命中。"""
    doc = get_doc(doc_id)
    if not doc:
        return False
    if purge_vectors:
        try:
            from app.services.vector_store import get_vector_store

            get_vector_store().delete_by_doc_id(int(doc_id))
        except Exception:  # noqa: BLE001 - 向量库不可用时仍要删库内记录
            pass
    with cpp_bridge.begin():
        cpp_bridge.execute("DELETE FROM knowledge_chunk WHERE doc_id = ?", [int(doc_id)])
        cpp_bridge.execute("DELETE FROM knowledge_doc WHERE id = ?", [int(doc_id)])
    return True


def purge_by_source_prefix(
    prefix: str, *, purge_vectors: bool = True, dry_run: bool = False
) -> dict:
    """按 source_url 前缀批量清理（B17 基准测试/误导入回滚）。

    Args:
        prefix: 来源前缀（**按字面匹配**：``%``/``_``/``\\`` 会被转义，不会当通配符）。
        purge_vectors: 是否同时清理向量库。
        dry_run: 只统计不删除（先看清要删什么再动手）。

    Raises:
        ValueError: 前缀为空或短于 ``MIN_PURGE_PREFIX_CHARS``（防止误删全库）。

    Returns:
        ``{"matched": n, "deleted": n, "doc_ids": [...], "pattern": "...", "dry_run": bool}``
    """
    p = normalize_source(prefix)
    if len(p) < MIN_PURGE_PREFIX_CHARS:
        raise ValueError(
            f"source_prefix 至少需要 {MIN_PURGE_PREFIX_CHARS} 个字符（防止误删全库），"
            f"当前为 {p!r}"
        )
    pattern = like_prefix_pattern(p)
    rows = cpp_bridge.query(
        "SELECT id FROM knowledge_doc WHERE source_url LIKE ? ESCAPE '\\\\' AND status != 2",
        [pattern],
    )
    ids = [int(r["id"]) for r in rows]
    if dry_run:
        return {
            "matched": len(ids), "deleted": 0, "doc_ids": ids,
            "pattern": pattern, "dry_run": True,
        }
    deleted = 0
    for doc_id in ids:
        if delete_doc(doc_id, purge_vectors=purge_vectors):
            deleted += 1
    return {
        "matched": len(ids), "deleted": deleted, "doc_ids": ids,
        "pattern": pattern, "dry_run": False,
    }


def stats() -> dict:
    """知识库规模概览（/health/detail 与批量导入汇总用）。"""
    rows = cpp_bridge.query(
        "SELECT status, COUNT(*) AS c, SUM(chunk_count) AS chunks, "
        "SUM(CHAR_LENGTH(content)) AS chars FROM knowledge_doc GROUP BY status"
    )
    out = {
        "total": 0,
        "ready": 0,
        "pending": 0,
        "chunks": 0,
        "chars": 0,
        "chunk_size": settings.rag_chunk_size,
    }
    for r in rows:
        status = int(r.get("status") or 0)
        count = int(r.get("c") or 0)
        if status == 2:      # 停用不计入规模
            continue
        out["total"] += count
        if status == 1:
            out["ready"] += count
        elif status == 0:
            out["pending"] += count
        out["chunks"] += int(r.get("chunks") or 0)
        out["chars"] += int(r.get("chars") or 0)
    return out
