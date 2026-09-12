"""AI Agent：任务创建/查询/取消、提醒。

契约：docs/api.md §4
编排三级链路（B5 + B7）见 services/agent_runner.py：
1. 模型可用 → LLM Function Call；2. 模型不可用 → 规则执行器（真写库）；
3. 两者都未识别 → status=3 失败（禁止假成功）。
B15：任务经 Celery 异步执行（未启用时 eager 同步，行为与旧版一致）。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core.deps import get_current_user
from app.core.response import BizError, err_param, ok, paged
from app.db import cpp_bridge

router = APIRouter(prefix="/agent", tags=["agent"])


class TaskIn(BaseModel):
    instruction: str


@router.post("/tasks")
def create_task(body: TaskIn, user: dict = Depends(get_current_user)):
    """创建 Agent 任务（B15：Celery 异步执行）。

    - Celery 启用：投递后立即返回 `status=0`，前端轮询 `GET /agent/tasks/{id}`；
    - 未启用（无 Redis）：eager 就地同步执行，响应与旧版一致（含执行结果）。
    """
    from app.core.config import settings
    from app.tasks import run_agent_task

    instruction = body.instruction.strip()
    if not instruction:
        raise err_param("指令不能为空")
    uid = int(user["id"])

    # 1) 落任务（0 待执行）
    rows = cpp_bridge.execute(
        "INSERT INTO agent_task (user_id, task_type, title, status) VALUES (?, 'plan', ?, 0)",
        [uid, instruction],
    )
    task_id = rows[1]

    # 2) 投递 Celery 任务（B15）：eager 模式同步返回结果；异步模式由前端轮询
    task = run_agent_task.delay(uid, task_id, instruction)
    if settings.celery_enabled:
        return ok(
            {
                "task_id": task_id,
                "status": 0,
                "plan": [],
                "result": {
                    "async": True,
                    "celery_task_id": task.id,
                    "note": "任务已投递，请轮询 GET /agent/tasks/{task_id}",
                },
            }
        )
    return ok(task.result)


@router.get("/tasks")
def list_tasks(
    status: int | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    sql = "SELECT * FROM agent_task WHERE user_id = ? "
    params: list = [int(user["id"])]
    if status is not None:
        sql += "AND status = ? "
        params.append(status)
    sql += "ORDER BY id DESC LIMIT ? OFFSET ?"
    params += [size, (page - 1) * size]
    rows = cpp_bridge.query(sql, params)
    return ok(paged(rows, len(rows), page, size))


@router.get("/tasks/{task_id}")
def task_detail(task_id: int, user: dict = Depends(get_current_user)):
    rows = cpp_bridge.query(
        "SELECT * FROM agent_task WHERE id = ? AND user_id = ?",
        [task_id, int(user["id"])],
    )
    if not rows:
        raise BizError(1001, "任务不存在")
    return ok(rows[0])


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: int, user: dict = Depends(get_current_user)):
    cpp_bridge.execute(
        "UPDATE agent_task SET status = 4 WHERE id = ? AND user_id = ? AND status IN (0,1)",
        [task_id, int(user["id"])],
    )
    return ok({"task_id": task_id, "status": 4})


class ReminderIn(BaseModel):
    content: str
    remind_at: str


@router.get("/reminders")
def reminders(user: dict = Depends(get_current_user)):
    rows = cpp_bridge.query(
        "SELECT id, content, remind_at, is_done FROM reminder "
        "WHERE user_id = ? ORDER BY remind_at",
        [int(user["id"])],
    )
    return ok({"items": rows})


@router.post("/reminders")
def create_reminder(body: ReminderIn, user: dict = Depends(get_current_user)):
    if not body.content.strip():
        raise err_param("提醒内容不能为空")
    rows = cpp_bridge.execute(
        "INSERT INTO reminder (user_id, content, remind_at) VALUES (?, ?, ?)",
        [int(user["id"]), body.content, body.remind_at],
    )
    return ok({"reminder_id": rows[1]})


@router.put("/reminders/{reminder_id}/done")
def done_reminder(reminder_id: int, user: dict = Depends(get_current_user)):
    cpp_bridge.execute(
        "UPDATE reminder SET is_done = 1 WHERE id = ? AND user_id = ?",
        [reminder_id, int(user["id"])],
    )
    return ok({"reminder_id": reminder_id, "is_done": 1})
