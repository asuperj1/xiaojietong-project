"""健康检查：基础探针 + 可观测详情（B8）+ 一键自检。

- ``GET /health``            基础状态（保持兼容：status / db / cpp_ext / pool）
- ``GET /health/detail``     可观测详情：DB·C++ 扩展·Ollama 可达性与模型清单·
                             embedding 模型就绪·向量库类型与条数·
                             知识库文档/分块数·当前检索模式（vector/keyword）与降级原因
- ``GET /health/selfcheck``  一键自检：embed + 检索 + 生成，逐项 pass/fail
"""

from __future__ import annotations

import time

import httpx
from fastapi import APIRouter

from app.core.celery_app import celery_status
from app.core.config import settings
from app.db import cpp_bridge
from app.services import rag
from app.services.embedder import embedder
from app.services.vector_store import get_vector_store

router = APIRouter()


@router.get("/health")
def health():
    db_status = "not_ready"
    if cpp_bridge.pool_ready():
        try:
            db_status = "ok" if cpp_bridge.ping() else "error"
        except RuntimeError:
            db_status = "error"
    return {
        "status": "ok",
        "db": db_status,
        "cpp_ext": cpp_bridge.available(),
        "pool": cpp_bridge.pool_stats(),
    }


async def _ollama_state() -> dict:
    """探测 Ollama 可达性与已装模型清单（失败返回不可达）。"""
    names: list[str] = []
    reachable = False
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags")
        if resp.status_code == 200:
            reachable = True
            names = [m.get("name", "") for m in resp.json().get("models", [])]
    except Exception:  # noqa: BLE001 - 探测失败即视为不可达
        reachable = False
    base_names = {n.split(":")[0] for n in names}
    return {
        "reachable": reachable,
        "base_url": settings.ollama_base_url,
        "models": names,
        "chat_model": settings.ollama_model,
        "chat_model_ready": settings.ollama_model.split(":")[0] in base_names,
        "embed_model": settings.rag_embed_model,
        "embed_model_ready": settings.rag_embed_model.split(":")[0] in base_names,
    }


@router.get("/health/detail")
async def health_detail():
    """可观测详情：一眼判断当前是真 RAG 还是关键词降级。"""
    pool_ready = cpp_bridge.pool_ready()
    db_ping = False
    if pool_ready:
        try:
            db_ping = cpp_bridge.ping()
        except RuntimeError:
            db_ping = False

    ollama = await _ollama_state()

    store = get_vector_store()
    try:
        vector_count = store.count()
    except Exception:  # noqa: BLE001 - 向量库异常不阻塞探针
        vector_count = 0

    docs = chunks = 0
    if db_ping:
        try:
            rows = cpp_bridge.query(
                "SELECT COUNT(*) AS c FROM knowledge_doc WHERE status != 2", []
            )
            docs = int(rows[0]["c"]) if rows else 0
            rows = cpp_bridge.query("SELECT COUNT(*) AS c FROM knowledge_chunk", [])
            chunks = int(rows[0]["c"]) if rows else 0
        except Exception:  # noqa: BLE001
            docs = chunks = 0

    # 检索模式判定：向量可用 = Ollama 可达 + bge-m3 就绪 + 向量库非空 + 未熔断
    retrieval_mode = "vector"
    degrade_reason = ""
    if not ollama["embed_model_ready"]:
        retrieval_mode = "keyword"
        degrade_reason = "embedding 模型不可用（Ollama 未启动或 bge-m3 未安装）"
    elif vector_count == 0:
        retrieval_mode = "keyword"
        degrade_reason = "向量库为空（需执行 ai/rag/build_index.py 建索引）"
    elif embedder._in_cooldown():  # noqa: SLF001 - 探针读取熔断状态
        retrieval_mode = "keyword"
        degrade_reason = "embedder 熔断中（最近一次调用失败，冷却期内直接降级）"

    return {
        "status": "ok",
        "db": {"pool_ready": pool_ready, "ping": db_ping, "pool": cpp_bridge.pool_stats()},
        "cpp_ext": cpp_bridge.available(),
        "ollama": ollama,
        "vector_store": {"backend": type(store).__name__, "count": vector_count},
        "knowledge": {"docs": docs, "chunks": chunks},
        "retrieval_mode": retrieval_mode,
        "degrade_reason": degrade_reason,
        "celery": celery_status(),
    }


@router.get("/health/selfcheck")
async def health_selfcheck():
    """一键自检：embed 1 次 + 检索 1 次 + 生成 1 次，逐项 pass/fail。"""
    checks: list[dict] = []

    # 1) embedding
    t0 = time.perf_counter()
    try:
        vec = await embedder.embed_one("校捷通健康自检")
        ok = bool(vec)
        detail = f"向量维度 {len(vec)}" if ok else "返回空（Ollama 不可达 / bge-m3 缺失 / 熔断中）"
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, f"异常：{exc}"
    checks.append(
        {"name": "embed", "pass": ok, "ms": int((time.perf_counter() - t0) * 1000), "detail": detail}
    )

    # 2) RAG 检索
    t0 = time.perf_counter()
    try:
        hits = await rag.retrieve("图书馆开放时间", 1)
        ok = bool(hits)
        detail = (
            f"命中 {len(hits)} 条：{hits[0].get('title', '')}（score={hits[0].get('score')}）"
            if ok
            else "无命中（向量库为空或知识库无数据）"
        )
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, f"异常：{exc}"
    checks.append(
        {"name": "retrieve", "pass": ok, "ms": int((time.perf_counter() - t0) * 1000), "detail": detail}
    )

    # 3) 模型生成
    t0 = time.perf_counter()
    try:
        from app.services.model_client import model_client

        answer = await model_client.chat(
            [
                {"role": "system", "content": "你是校捷通校园助手，请用一句话回答。"},
                {"role": "user", "content": "你好，请用一句话自我介绍"},
            ]
        )
        bad_prefix = ("（模型未就绪", "（模型服务返回")
        ok = bool(answer) and not answer.startswith(bad_prefix)
        detail = (answer[:60] + "…") if ok else f"降级输出：{answer[:80]}"
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, f"异常：{exc}"
    checks.append(
        {"name": "generate", "pass": ok, "ms": int((time.perf_counter() - t0) * 1000), "detail": detail}
    )

    return {"all_pass": all(c["pass"] for c in checks), "checks": checks}
