"""向量化客户端：通过 Ollama /api/embed 调用 bge-m3 生成文本向量。

- 批量嵌入（支持 0/多条），内部自动分批控制并发。
- Ollama 未就绪或模型缺失时返回 None，由调用方（rag.py）优雅降级到关键词检索。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class OllamaEmbedder:
    """bge-m3 embedding 客户端（Ollama OpenAI/原生 /api/embed 接口）。"""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        batch: int = 16,
        timeout: float = 60.0,
        fail_cooldown: float = 30.0,
    ) -> None:
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.rag_embed_model
        self.batch = batch or settings.rag_embed_batch
        self.timeout = timeout
        self.fail_cooldown = fail_cooldown
        # 简单内存缓存：{text: embedding}，避免重复向量化
        self._cache: dict[str, list[float]] = {}
        # 熔断：记录最近失败时间，冷却期内直接返回 None
        self._last_fail_at: float = 0.0

    def _in_cooldown(self) -> bool:
        import time
        return (time.monotonic() - self._last_fail_at) < self.fail_cooldown

    async def _request(self, texts: list[str]) -> list[list[float]] | None:
        """请求 Ollama /api/embed。失败返回 None（不抛异常）。"""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout)) as client:
                resp = await client.post(
                    f"{self.base_url}/api/embed",
                    json={"model": self.model, "input": texts},
                )
                if resp.status_code != 200:
                    logger.warning("Ollama embed 失败 status=%s body=%s",
                                   resp.status_code, resp.text[:200])
                    return None
                data = resp.json()
                embeddings = data.get("embeddings") or []
                if len(embeddings) != len(texts):
                    logger.warning("Ollama embed 返回条数不匹配 %d != %d",
                                   len(embeddings), len(texts))
                    return None
                return embeddings
        except Exception as exc:  # noqa: BLE001 - 连接失败等统一降级
            logger.warning("Ollama embed 异常：%s", exc)
            return None

    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        """批量向量化。任一失败返回 None。空输入返回 []。"""
        if not texts:
            return []
        if self._in_cooldown():
            return None  # 熔断：冷却期内不再请求 Ollama
        texts = [t or "" for t in texts]
        results: list[list[float] | None] = [None] * len(texts)
        missing: list[int] = []

        # 1) 命中缓存
        for i, t in enumerate(texts):
            if t in self._cache:
                results[i] = self._cache[t]
            else:
                missing.append(i)

        # 2) 分批请求未命中的文本
        for start in range(0, len(missing), self.batch):
            idx = missing[start:start + self.batch]
            batch_texts = [texts[i] for i in idx]
            got = await self._request(batch_texts)
            if got is None:
                import time
                self._last_fail_at = time.monotonic()
                return None  # 任一请求失败 → 整体降级 + 进入冷却
            for i, emb in zip(idx, got):
                self._cache[texts[i]] = emb
                results[i] = emb

        # 类型收窄：此时不可能为 None
        return [r for r in results if r is not None]  # type: ignore[misc]

    async def embed_one(self, text: str) -> list[float] | None:
        """单条向量化。"""
        got = await self.embed([text])
        if not got:
            return None
        return got[0]

    async def available(self) -> bool:
        """探测 Ollama 是否可用且模型存在。"""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                if resp.status_code != 200:
                    return False
                models = resp.json().get("models", [])
                names = {m.get("name", "").split(":")[0] for m in models}
                return self.model.split(":")[0] in names
        except Exception:  # noqa: BLE001
            return False


embedder = OllamaEmbedder()
