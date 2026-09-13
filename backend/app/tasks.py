"""Celery 异步任务（B15）：Agent 长任务 + 知识库索引构建。

原同步阻塞点（阻塞请求线程）：
- **Agent 任务**：模型意图解析可达数十秒 + 工具执行 → ``run_agent_task``；
- **知识库索引构建**：批量 embedding 调用可达数分钟 → ``build_knowledge_index``。

调用方（``routers/agent.py``、``routers/admin.py``）通过 ``.delay()`` 投递；
``XJT_CELERY_ENABLED=false``（无 Redis 环境）时自动降级 eager 同步执行，
接口行为与旧实现一致，不中断演示。

⚠️ worker 是独立进程，**不会执行 FastAPI 的 lifespan**，因此任务内必须先
惰性初始化 ``jt_db`` 连接池（``_ensure_db()``），否则所有 cpp_bridge 调用
都会抛「连接池未初始化」。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.celery_app import celery_app


def _ensure_db() -> None:
    """惰性初始化 jt_db 连接池（幂等；worker 独立进程必需）。

    - 扩展不可用 → 明确报错，避免任务静默失败；
    - 池未就绪 → 按 settings 初始化（连接失败会抛错，由 Celery 记录为 FAILURE）。
    """
    from app.core.config import settings
    from app.db import cpp_bridge

    if not cpp_bridge.available():
        raise RuntimeError("jt_db C++ 扩展不可用，Celery 任务无法访问数据库")
    if not cpp_bridge.pool_ready():
        cpp_bridge.init_db(
            settings.db_host,
            settings.db_port,
            settings.db_user,
            settings.db_password,
            settings.db_name,
            settings.db_min_conn,
            settings.db_max_conn,
        )


@celery_app.task(name="xjt.run_agent_task")
def run_agent_task(user_id: int, task_id: int, instruction: str) -> dict:
    """异步执行 Agent 任务（三级链路 + 工具真实执行 + 状态回写）。"""
    from app.services.agent_runner import plan_and_execute_sync

    _ensure_db()
    return plan_and_execute_sync(user_id, task_id, instruction)


@celery_app.task(name="xjt.build_knowledge_index")
def build_knowledge_index(force: bool = False, doc_ids: list[int] | None = None) -> dict:
    """异步构建知识库向量索引（返回 build_index 的结果结构）。"""
    from app.services import rag

    _ensure_db()
    return asyncio.run(rag.build_index(doc_ids=doc_ids, force=force))


def task_state(task_id: str) -> dict[str, Any]:
    """查询异步任务状态（供管理端索引状态接口使用）。"""
    result = celery_app.AsyncResult(task_id)
    return {
        "task_id": task_id,
        "state": result.state,
        "result": result.result if result.ready() else None,
    }
