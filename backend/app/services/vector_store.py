"""向量存储：RAG 检索后端。

后端选择（自动降级）：
1. ChromaVectorStore —— ChromaDB PersistentClient 持久化（主推，开发环境）。
2. NumpyVectorStore —— 纯 numpy 余弦相似度 + pickle 本地持久化（ChromaDB
   未安装/初始化失败时的降级，保证 RAG 链路始终可用）。

统一接口：add / search / count / clear / available / dimension。
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any, Optional

import numpy as np

from app.core.config import settings

logger = logging.getLogger(__name__)

_COLLECTION_NAME = "knowledge_chunk"


class VectorStore:
    """向量存储基类（接口约束）。"""

    def available(self) -> bool:  # pragma: no cover - 接口
        raise NotImplementedError

    def count(self) -> int:  # pragma: no cover - 接口
        raise NotImplementedError

    def add(self, ids: list[str], embeddings: list[list[float]],
            metadatas: list[dict], documents: list[str]) -> None:
        raise NotImplementedError  # pragma: no cover - 接口

    def search(self, query_emb: list[float], top_k: int,
               score_threshold: float) -> list[dict]:
        raise NotImplementedError  # pragma: no cover - 接口

    def delete_by_doc_id(self, doc_id: int) -> None:
        """删除某文档的全部向量（重建/停用用）。"""
        raise NotImplementedError  # pragma: no cover - 接口

    def clear(self) -> None:
        raise NotImplementedError  # pragma: no cover - 接口

    def close(self) -> None:
        """释放底层资源（进程退出 / 重建索引时调用）。"""
        pass


class ChromaVectorStore(VectorStore):
    """ChromaDB 持久化后端。"""

    def __init__(self, persist_dir: Path, dimension: int) -> None:
        import chromadb  # 惰性导入，依赖缺失时由工厂捕获

        self.persist_dir = persist_dir
        self.dimension = dimension
        self._client = chromadb.PersistentClient(path=str(persist_dir))
        self._collection = self._client.get_or_create_collection(
            _COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        self._lock = threading.Lock()

    def available(self) -> bool:
        return True

    def count(self) -> int:
        with self._lock:
            return self._collection.count()

    def add(self, ids: list[str], embeddings: list[list[float]],
            metadatas: list[dict], documents: list[str]) -> None:
        with self._lock:
            self._collection.add(
                ids=ids,
                embeddings=embeddings,
                metadatas=metadatas,
                documents=documents,
            )

    def search(self, query_emb: list[float], top_k: int,
               score_threshold: float) -> list[dict]:
        with self._lock:
            res = self._collection.query(
                query_embeddings=[query_emb],
                n_results=top_k,
                include=["metadatas", "documents", "distances"],
            )
        # 空库
        if not res or not res.get("ids") or not res["ids"][0]:
            return []
        items: list[dict] = []
        ids = res["ids"][0]
        dists = res["distances"][0]
        metas = res["metadatas"][0]
        docs = res["documents"][0]
        for i, _id in enumerate(ids):
            score = 1.0 - float(dists[i])  # cosine 距离 → 相似度
            if score < score_threshold:
                continue
            m = metas[i] or {}
            items.append(
                {
                    "id": _id,
                    "doc_id": int(m.get("doc_id", 0)),
                    "seq": int(m.get("seq", 0)),
                    "title": m.get("title", ""),
                    "category": m.get("category", ""),
                    "content": docs[i] or "",
                    "score": round(score, 4),
                }
            )
        return items

    def delete_by_doc_id(self, doc_id: int) -> None:
        with self._lock:
            try:
                self._collection.delete(where={"doc_id": doc_id})
            except Exception as exc:  # noqa: BLE001 - 无匹配也正常
                logger.warning("Chroma 删除 doc_id=%s 失败：%s", doc_id, exc)

    def clear(self) -> None:
        with self._lock:
            self._client.delete_collection(_COLLECTION_NAME)
            self._collection = self._client.get_or_create_collection(
                _COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )

    def close(self) -> None:
        """释放 Chroma 客户端句柄（Windows 下释放文件锁）。"""
        with self._lock:
            self._collection = None  # type: ignore[assignment]
            self._client = None  # type: ignore[assignment]


class NumpyVectorStore(VectorStore):
    """纯 numpy 余弦相似度后端（内存 + pickle 持久化降级方案）。"""

    def __init__(self, persist_dir: Path, dimension: int) -> None:
        self.persist_dir = persist_dir
        self.dimension = dimension
        self._lock = threading.Lock()
        self._matrix: Optional[np.ndarray] = None  # (N, dim) 单位向量
        self._meta: list[dict] = []                # 与行一一对应
        self._load()

    # ---- 持久化 ----
    def _data_file(self) -> Path:
        return self.persist_dir / "numpy_store.pkl"

    def _load(self) -> None:
        f = self._data_file()
        if not f.exists():
            return
        try:
            import pickle
            with f.open("rb") as fp:
                self._matrix = pickle.load(fp)
                self._meta = pickle.load(fp)
        except Exception as exc:  # noqa: BLE001 - 损坏则重置
            logger.warning("numpy 向量库加载失败，重建：%s", exc)
            self._matrix = None
            self._meta = []

    def _save(self) -> None:
        import pickle
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        with self._data_file().open("wb") as fp:
            pickle.dump(self._matrix, fp)
            pickle.dump(self._meta, fp)

    # ---- 接口 ----
    def available(self) -> bool:
        return True

    def count(self) -> int:
        with self._lock:
            return 0 if self._matrix is None else len(self._meta)

    def add(self, ids: list[str], embeddings: list[list[float]],
            metadatas: list[dict], documents: list[str]) -> None:
        arr = np.asarray(embeddings, dtype=np.float32)
        if arr.ndim != 2 or arr.shape[1] != self.dimension:
            raise ValueError(
                f"向量维度不匹配 {arr.shape[1]} != {self.dimension}，请重建索引"
            )
        # 归一化为单位向量（余弦相似度 = 点积）
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        arr = arr / norms
        with self._lock:
            if self._matrix is None:
                self._matrix = arr
            else:
                if self._matrix.shape[1] != arr.shape[1]:
                    raise ValueError("向量维度变化，请重建索引")
                self._matrix = np.vstack([self._matrix, arr])
            for i, _id in enumerate(ids):
                m = dict(metadatas[i] or {})
                m.update({"_id": _id, "content": documents[i] or ""})
                self._meta.append(m)
            self._save()

    def search(self, query_emb: list[float], top_k: int,
               score_threshold: float) -> list[dict]:
        q = np.asarray(query_emb, dtype=np.float32).reshape(1, -1)
        nq = np.linalg.norm(q)
        if nq == 0:
            return []
        q = q / nq
        with self._lock:
            if self._matrix is None or len(self._meta) == 0:
                return []
            sims = (self._matrix @ q.T).ravel()  # (N,)
            idx = np.argsort(-sims)[:top_k]
        items: list[dict] = []
        for i in idx:
            score = float(sims[i])
            if score < score_threshold:
                continue
            m = self._meta[int(i)]
            items.append(
                {
                    "id": m.get("_id", ""),
                    "doc_id": int(m.get("doc_id", 0)),
                    "seq": int(m.get("seq", 0)),
                    "title": m.get("title", ""),
                    "category": m.get("category", ""),
                    "content": m.get("content", ""),
                    "score": round(score, 4),
                }
            )
        return items

    def delete_by_doc_id(self, doc_id: int) -> None:
        with self._lock:
            keep_meta: list[dict] = []
            keep_rows: list[int] = []
            for i, m in enumerate(self._meta):
                if int(m.get("doc_id", 0)) != doc_id:
                    keep_meta.append(m)
                    keep_rows.append(i)
            if len(keep_rows) != len(self._meta):
                self._matrix = self._matrix[keep_rows] if keep_rows else None
                self._meta = keep_meta
                self._save()

    def clear(self) -> None:
        with self._lock:
            self._matrix = None
            self._meta = []
            f = self._data_file()
            if f.exists():
                f.unlink()


# ---- 工厂（自动降级） ----

_vector_store: Optional[VectorStore] = None
_vector_store_lock = threading.Lock()


def _persist_dir() -> Path:
    return Path(settings.rag_vector_dir).resolve()


def get_vector_store(force_reload: bool = False) -> VectorStore:
    """返回最佳可用向量库（单例）。ChromaDB 不可用则降级 numpy。"""
    global _vector_store
    with _vector_store_lock:
        if _vector_store is not None and not force_reload:
            return _vector_store
        dim = settings.rag_embed_dim
        pdir = _persist_dir()
        try:
            store = ChromaVectorStore(pdir, dim)
            logger.info("RAG 向量库：ChromaDB（%s）", pdir)
        except Exception as exc:  # noqa: BLE001 - 降级 numpy
            logger.warning("ChromaDB 不可用，降级 numpy 向量库：%s", exc)
            store = NumpyVectorStore(pdir, dim)
        _vector_store = store
        return store


def reset_vector_store() -> None:
    """清空向量库单例（测试/重建用）。"""
    global _vector_store
    with _vector_store_lock:
        _vector_store = None
