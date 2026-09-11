"""AI Agent：任务创建/查询/取消、提醒。

契约：docs/api.md §4
说明：任务编排走 LLM Function Call（services/agent_executor.py），意图由模型解析为
      工具调用后真实执行（写提醒/预约/发布）；模型不可用或未识别工具时降级为
      关键词规则占位，链路不中断。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core.deps import get_current_user
from app.core.response import BizError, err_param, ok, paged
from app.db import cpp_bridge
from app.services import agent_executor

router = APIRouter(prefix="/agent", tags=["agent"])

# 意图→任务类型 关键词规则（兜底；主路径为模型 Function Call）
_INTENT_RULES = {
    "reserve": ("预约", "reserve_seat"),
    "remind": ("提醒", "add_reminder"),
    "query": ("查询", "query_free_room"),
    "publish": ("发布", "post_secondhand"),
}


class TaskIn(BaseModel):
    instruction: str


@router.post("/tasks")
async def create_task(body: TaskIn, user: dict = Depends(get_current_user)):
    instruction = body.instruction.strip()
    if not instruction:
        raise err_param("指令不能为空")
    uid = int(user["id"])

    # 1) 落任务（0 待执行，随后立即编排执行；Celery 异步化前保持同步）
    rows = cpp_bridge.execute(
        "INSERT INTO agent_task (user_id, task_type, title, status) VALUES (?, 'plan', ?, 0)",
        [uid, instruction],
    )
    task_id = rows[1]

    # 2) LLM Function Call 意图解析（None=模型不可用；[]=未识别到工具）
    calls = await agent_executor.plan_instruction(instruction)

    if calls:
        # 3a) 主路径：真实执行工具
        results, all_ok = agent_executor.execute_calls(uid, task_id, calls)
        task_type = agent_executor.TOOL_TASK_TYPE.get(calls[0]["name"], "query")
        plan = []
        for r in results:
            item = {"tool": r["tool"], "desc": r["desc"]}
            if r.get("ok") and r.get("result"):
                item["result"] = r["result"]
            else:
                item["error"] = r.get("error", "执行失败")
            plan.append(item)
        error_msg = "；".join(r.get("error", "") for r in results if not r.get("ok"))
        status = 2 if all_ok else 3
        result_payload = {"results": results}
        params = json.dumps(calls, ensure_ascii=False)
    else:
        # 3b) 降级：关键词规则占位（保链路可用）
        task_type, tool = "query", ""
        for key, (kw, t) in _INTENT_RULES.items():
            if kw in instruction:
                task_type, tool = key, t
                break
        note = (
            "模型服务不可用，任务按关键词占位完成"
            if calls is None
            else "未识别到可执行工具，任务已记录"
        )
        plan = [{"tool": tool, "desc": f"执行{task_type}任务"}] if tool else []
        error_msg = "" if tool else note
        result_payload = {"task_id": task_id, "note": note}
        params = json.dumps({"instruction": instruction}, ensure_ascii=False)
        status = 2

    cpp_bridge.execute(
        "UPDATE agent_task SET task_type = ?, status = ?, params_json = ?, result_json = ?, "
        "error_msg = ?, started_at = NOW(), finished_at = NOW() WHERE id = ?",
        [
            task_type,
            status,
            params,
            json.dumps(result_payload, ensure_ascii=False),
            error_msg,
            task_id,
        ],
    )
    return ok({"task_id": task_id, "status": status, "plan": plan, "result": result_payload})


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
