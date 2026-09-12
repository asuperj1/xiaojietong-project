"""Agent 任务执行器（B15 抽出）：解析编排 + 工具执行 + 任务状态回写。

被两处复用，避免逻辑重复：
- ``routers/agent.py``：同步路径（Celery eager 模式）；
- ``app/tasks.py``：Celery 异步任务 ``run_agent_task``。

三级链路（B7）：模型 Function Call → 规则执行器 → status=3 明确失败。
"""

from __future__ import annotations

import asyncio
import json

from app.db import cpp_bridge
from app.services import agent_executor, rule_executor


async def plan_and_execute(uid: int, task_id: int, instruction: str) -> dict:
    """异步版：FastAPI 端点内直接调用。"""
    calls = await agent_executor.plan_instruction(instruction)
    return _execute(uid, task_id, instruction, calls)


def plan_and_execute_sync(uid: int, task_id: int, instruction: str) -> dict:
    """同步版：Celery 任务内调用（内部自建事件循环）。"""
    calls = asyncio.run(agent_executor.plan_instruction(instruction))
    return _execute(uid, task_id, instruction, calls)


def _execute(uid: int, task_id: int, instruction: str, calls: list | None) -> dict:
    """执行工具调用并回写 agent_task，返回统一响应结构。"""
    source = "model"
    if not calls:
        calls = rule_executor.parse(instruction)
        source = "rule"

    if calls:
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
    return {"task_id": task_id, "status": status, "plan": plan, "result": result_payload}
