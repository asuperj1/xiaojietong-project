"""AI Agent：任务创建/查询/取消、提醒。

契约：docs/api.md §4
说明：任务编排（意图解析→工具调用）为接入点，当前以关键词规则占位；
     后续由用户的模型 Function Call 能力接管，入 agent_task 异步执行。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core.deps import get_current_user
from app.core.response import BizError, err_param, ok, paged
from app.db import cpp_bridge

router = APIRouter(prefix="/agent", tags=["agent"])

# 意图→任务类型 关键词规则（占位；可替换为模型 Function Call）
_INTENT_RULES = {
    "reserve": ("预约", "reserve_seat"),
    "remind": ("提醒", "add_reminder"),
    "query": ("查询", "query_free_room"),
    "publish": ("发布", "post_secondhand"),
}


class TaskIn(BaseModel):
    instruction: str


@router.post("/tasks")
def create_task(body: TaskIn, user: dict = Depends(get_current_user)):
    instruction = body.instruction.strip()
    if not instruction:
        raise err_param("指令不能为空")

    # 占位意图识别：匹配关键词确定 task_type
    task_type, tool = "query", ""
    for key, (kw, t) in _INTENT_RULES.items():
        if kw in instruction:
            task_type, tool = key, t
            break

    rows = cpp_bridge.execute(
        "INSERT INTO agent_task (user_id, task_type, title, status) VALUES (?, ?, ?, 0)",
        [int(user["id"]), task_type, instruction],
    )
    task_id = rows[1]
    plan = [{"tool": tool, "desc": f"执行{task_type}任务"}] if tool else []
    # TODO: 异步执行（Celery）+ 工具调用；当前置为成功占位
    cpp_bridge.execute(
        "UPDATE agent_task SET status = 2, result_json = ?, finished_at = NOW() WHERE id = ?",
        [json.dumps({"task_id": task_id}), task_id],
    )
    return ok({"task_id": task_id, "status": 2, "plan": plan})


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
