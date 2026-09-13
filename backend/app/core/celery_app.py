"""Celery 应用（B15）：异步任务队列（Redis broker，可降级 eager 模式）。

**运行模式**
- ``XJT_CELERY_ENABLED=false``（默认，本机无 Redis 时）→ ``task_always_eager=True``：
  ``.delay()`` 就地同步执行，行为与旧同步实现完全一致（代码链路已接 Celery，
  具备一键切换能力）；
- ``XJT_CELERY_ENABLED=true`` → 真异步，需先启动 Redis 与 worker：

  .. code-block:: bash

      # 1) 启动 Redis（Windows 可用 redis-server.exe）
      redis-server
      # 2) 启动 worker（Windows 必须用 -P solo，避免 fork 不可用）
      cd backend
      ..\backend\.venv\Scripts\celery.exe -A app.core.celery_app:celery_app worker -l info -P solo

任务定义见 ``app/tasks.py``（Agent 长任务、知识库索引构建）。
"""

from __future__ import annotations

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "xjt",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks"],
)

celery_app.conf.update(
    # 未启用（无 Redis 环境）时就地执行，保证接口链路不中断
    task_always_eager=not settings.celery_enabled,
    task_eager_propagates=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=False,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
)


def celery_status() -> dict:
    """供 /health/detail 观测（B15）。"""
    return {
        "enabled": settings.celery_enabled,
        "mode": "async(celery+redis)" if settings.celery_enabled else "eager(sync)",
        "broker": settings.celery_broker_url,
    }
