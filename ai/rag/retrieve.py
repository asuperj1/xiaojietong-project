#!/usr/bin/env python
"""RAG 检索测试脚本（向量检索 + 降级路径验证）。

用法（仓库根 / Git Bash，需先启动 MySQL）：
    cd backend && XJT_DB_PORT=3307 XJT_DB_PASSWORD=jhq000000 \
        python ../ai/rag/retrieve.py "图书馆几点关门"
    python ../ai/rag/retrieve.py "图书馆几点关门" 5   # 指定 top_k
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.config import settings  # noqa: E402
from app.db import cpp_bridge  # noqa: E402
from app.services.embedder import embedder  # noqa: E402
from app.services import rag  # noqa: E402
from app.services.vector_store import get_vector_store  # noqa: E402


def _init_db() -> None:
    cpp_bridge.init_db(
        settings.db_host,
        settings.db_port,
        settings.db_user,
        settings.db_password,
        settings.db_name,
        settings.db_min_conn,
        settings.db_max_conn,
    )


async def _run(question: str, top_k: int) -> None:
    store = get_vector_store()
    print(f"向量库后端：{type(store).__name__}，现有向量 {store.count()} 条")
    print(f"embedding 可用：{await embedder.available()}\n")

    hits = await rag.retrieve(question, top_k)
    print(f"检索「{question}」top_k={top_k}，命中 {len(hits)} 条：")
    for h in hits:
        print(json.dumps(h, ensure_ascii=False, indent=2))
    if not hits:
        print("（未命中：向量库为空 / 相似度低于阈值 / embedding 未就绪降级无结果）")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    question = sys.argv[1]
    top_k = int(sys.argv[2]) if len(sys.argv) > 2 else settings.rag_top_k
    _init_db()
    asyncio.run(_run(question, top_k))


if __name__ == "__main__":
    main()
