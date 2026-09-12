"""AI Agent：任务创建/查询/取消、提醒。

契约：docs/api.md §4
编排（B5 + B7）三级链路：
1. 模型可用 → LLM Function Call（services/agent_executor.py）；
2. 模型不可用/未返回工具 → 规则执行器（services/rule_executor.py，**真写库**）；
3. 两者都未识别 → status=3 失败 + error_msg（**禁止假成功**）。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core.deps import get_current_user
from app.core.response import BizError, err_param, ok, paged
from app.db import cpp_bridge
from app.services import agent_executor, rule_executor

router = APIRouter(prefix="/agent", tags=["agent"])


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

    # 2) 三级链路：模型 Function Call → 规则执行器 → 明确失败
    calls = await agent_executor.plan_instruction(instruction)
    source = "model"
    if not calls:
        calls = rule_executor.parse(instruction)
        source = "rule"

    if calls:
        # 真实执行工具（写库）：add_reminder / reserve_seat / query_free_room / post_secondhand
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
        result_payload = {"source": source, "results": results}
        params = json.dumps(calls, ensure_ascii=False)
    else:
        # 3) 模型与规则都未识别 → 明确失败（不再出现"未执行任何工具却报成功"）
        task_type = "unknown"
        status = 3
        error_msg = "无法识别指令意图：模型未返回工具调用，规则也未匹配"
        plan = []
        result_payload = {"source": "none", "results": [], "note": error_msg}
        params = json.dumps({"instruction": instruction}, ensure_ascii=False)

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
