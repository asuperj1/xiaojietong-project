#!/usr/bin/env python
"""知识库索引构建脚本（RAG 向量化）。

用法（在仓库根用 Git Bash，需先启动 MySQL 并设置环境变量）：
    cd backend && XJT_DB_PORT=3307 XJT_DB_PASSWORD=${XJT_DB_PASSWORD} \
        python ../ai/rag/build_index.py            # 增量：只处理待向量化文档
    python ../ai/rag/build_index.py --force        # 全量重建（覆盖式，不中断检索）
    python ../ai/rag/build_index.py --doc 1,2,3    # 仅重建指定文档

注意：`--force` 为**覆盖式**重建（审计 CAC-07）—— 不再先清空向量库与
chunk 表，而是逐篇覆盖写入，因此重建期间旧索引**始终可检索**，且单篇失败
不会影响其它文档；收尾会清理孤儿向量。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# 让脚本能 import backend 下的 app 包
_BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.config import settings  # noqa: E402
from app.db import cpp_bridge  # noqa: E402
from app.services import rag  # noqa: E402


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


def main() -> None:
    parser = argparse.ArgumentParser(description="构建 RAG 知识库索引")
    parser.add_argument("--force", action="store_true",
                        help="覆盖式全量重建：不清空向量库，重建期间检索不中断（CAC-07）")
    parser.add_argument("--doc", default="", help="仅处理指定文档，逗号分隔，如 1,2,3")
    args = parser.parse_args()

    _init_db()
    doc_ids = [int(x) for x in args.doc.split(",") if x.strip()] or None
    result = asyncio.run(rag.build_index(doc_ids=doc_ids, force=args.force))

    print(f"\n索引构建完成：total={result['total']} ok={result['ok']} failed={result['failed']}")
    if result.get("orphans_removed"):
        print(f"已清理孤儿向量：{result['orphans_removed']} 个文档")
    for d in result["details"]:
        print(f"  doc_id={d['doc_id']} chunks={d['chunks']} status={d['status']}")
    if result["failed"]:
        print("\n存在失败项：embed_failed = Ollama/bge-m3 未就绪，启动后重跑即可；"
              "vector_failed/db_failed 见后端日志。")
        sys.exit(1)


if __name__ == "__main__":
    main()
