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
from celery.schedules import crontab

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

# ---------- 定时任务（B18 分层推送调度）----------
# 每天 XJT_NOTICE_PUSH_HOUR:XJT_NOTICE_PUSH_MINUTE（默认 08:00，Asia/Shanghai）扫描待办，
# 对待办到期前 7 天 / 2 天生成投递记录（notice_delivery）。
# ⚠️ beat 与 worker 都需要运行才会真正定时：
#     celery -A app.core.celery_app:celery_app worker -l info -P solo -B
#     （生产建议把 beat 拆成独立进程/单实例，避免多副本重复调度——调度本身幂等）
# 未启用 Celery（eager 模式）时没有 beat，可手动触发：
#     POST /api/v1/admin/notices/dispatch
if settings.celery_enabled and settings.notice_push_enabled:
    celery_app.conf.beat_schedule = {
        "xjt-notice-push-daily": {
            "task": "xjt.dispatch_notice_push",
            "schedule": crontab(hour=settings.notice_push_hour, minute=settings.notice_push_minute),
            "options": {"expires": 3600},
        }
    }
    celery_app.conf.beat_schedule_filename = "data/celerybeat-schedule"


def celery_status() -> dict:
    """供 /health/detail 观测（B15）。"""
    return {
        "enabled": settings.celery_enabled,
        "mode": "async(celery+redis)" if settings.celery_enabled else "eager(sync)",
        "broker": settings.celery_broker_url,
    }
