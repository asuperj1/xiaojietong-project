"""Agent 执行器（B5）：LLM Function Call → 工具真实执行。

替代 routers/agent.py 的关键词规则占位。流程：
1. 读取 `agent_tool` 注册表（enabled=1）→ 组装 OpenAI 风格 tools
2. 调 Ollama /api/chat（带 tools + 当前时间提示）→ 模型返回 tool_calls
3. 逐工具真实执行（写库）：
   - add_reminder     → INSERT reminder（关联 agent_task.task_id）
   - reserve_seat     → 事务内冲突校验 + LibraryDAO.reserve（真实预约落库）
   - query_free_room  → LibraryDAO.find_free_rooms（返回空教室）
   - post_secondhand  → SecondhandDAO.publish（真实发布闲置）
4. 汇总执行结果（ok/fail 明细）

Ollama 未就绪 / 模型未返回工具调用时返回 None，由路由层回退关键词规则，
保证接口链路不中断（降级兼容）。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable, Optional

import httpx

from app.core.config import settings
from app.db import cpp_bridge

_MODEL_TIMEOUT = 120.0

# 工具名 → agent_task.task_type（沿用既有枚举）
TOOL_TASK_TYPE = {
    "reserve_seat": "reserve",
    "add_reminder": "remind",
    "query_free_room": "query",
    "post_secondhand": "publish",
}

_DEFAULT_DESC = {
    "reserve_seat": "预约图书馆座位",
    "add_reminder": "添加提醒",
    "query_free_room": "查询空教室",
    "post_secondhand": "发布闲置",
}


# ------------------------------------------------------------ schema ----

def load_tools() -> list[dict]:
    """读取启用的 agent_tool，转 OpenAI tools 格式（供 /api/chat 使用）。"""
    rows = cpp_bridge.query(
        "SELECT name, description, params_schema FROM agent_tool "
        "WHERE enabled = 1 ORDER BY id",
        [],
    )
    tools = []
    for r in rows:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": r["name"],
                    "description": r.get("description") or "",
                    "parameters": _to_json_schema(r.get("params_schema")),
                },
            }
        )
    return tools


def _to_json_schema(raw) -> dict:
    """把 params_schema 规整为 JSON Schema。

    兼容两种存量形态：正规 schema（含 type/properties）直接使用；
    种子数据的平面占位（如 {"seat_id": 0, "date": ""}）按值类型推断。
    """
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except Exception:
            data = {}
    else:
        data = raw or {}
    if not isinstance(data, dict):
        data = {}
    if "type" in data or "properties" in data:
        return data
    props: dict[str, Any] = {}
    required: list[str] = []
    for key, val in data.items():
        if isinstance(val, bool):
            t = "boolean"
        elif isinstance(val, (int, float)):
            t = "number"
        else:
            t = "string"
        props[key] = {"type": t}
        required.append(key)
    return {"type": "object", "properties": props, "required": required}


# ------------------------------------------------------------ 解析 ----

async def plan_instruction(instruction: str) -> Optional[list[dict]]:
    """LLM 意图解析。

    返回 [{"name": 工具名, "arguments": {...}}, ...]；
    模型/服务不可用返回 None；模型未调用工具返回 []。
    """
    tools = load_tools()
    if not tools:
        return None
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    system = (
        "你是校捷通的 AI Agent，把用户的自然语言指令解析为工具调用。"
        f"当前时间：{now}。\n"
        "规则：1) 相对时间（今天/明天/后天/今晚/下午4点等）一律换算为绝对时间，"
        "格式 YYYY-MM-DD HH:MM；2) 只调用提供的工具，arguments 必须是合法 JSON 且参数齐全；"
        "3) 日期取未来最近的自然日；4) 指令无法匹配任何工具时不要调用工具。"
    )
    payload = {
        "model": settings.ollama_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": instruction},
        ],
        "tools": tools,
        "stream": False,
        "options": {"temperature": 0.1},
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(_MODEL_TIMEOUT)) as client:
            resp = await client.post(f"{settings.ollama_base_url}/api/chat", json=payload)
        if resp.status_code != 200:
            return None
        message = resp.json().get("message", {})
        calls = message.get("tool_calls") or []
    except Exception:
        return None

    parsed: list[dict] = []
    for tc in calls:
        fn = tc.get("function", {})
        name = fn.get("name", "")
        # Ollama 不同版本 arguments 可能是对象或 JSON 字符串，两种都兼容
        raw = fn.get("arguments")
        if isinstance(raw, dict):
            args = raw
        elif isinstance(raw, str):
            try:
                args = json.loads(raw or "{}")
            except Exception:
                args = {}
        else:
            args = {}
        parsed.append(
            {"name": name, "arguments": args if isinstance(args, dict) else {}}
        )
    return parsed


# ------------------------------------------------------------ 执行 ----

def execute_calls(user_id: int, task_id: int, calls: list[dict]) -> tuple[list[dict], bool]:
    """依次执行工具调用。返回 (results, all_ok)。

    results: [{"tool", "ok", "desc", "result"|"error"}]（desc 供 plan 展示）
    """
    results = []
    all_ok = True
    for call in calls:
        name = call.get("name", "")
        args = call.get("arguments") or {}
        handler: Optional[Callable[[int, int, dict], dict]] = _EXECUTORS.get(name)
        if handler is None:
            results.append(
                {
                    "tool": name,
                    "ok": False,
                    "desc": _DEFAULT_DESC.get(name, name),
                    "error": f"工具 {name} 未接入执行器",
                }
            )
            all_ok = False
            continue
        try:
            outcome = handler(user_id, task_id, args)
        except Exception as exc:  # noqa: BLE001 - 执行异常计入失败明细
            outcome = {"ok": False, "error": f"执行异常：{exc}"}
        item: dict[str, Any] = {
            "tool": name,
            "ok": bool(outcome.get("ok")),
            "desc": _DEFAULT_DESC.get(name, name),
        }
        if outcome.get("ok"):
            item["result"] = outcome.get("result", {})
        else:
            item["error"] = outcome.get("error", "执行失败")
            all_ok = False
        results.append(item)
    return results, all_ok


def _exec_add_reminder(user_id: int, task_id: int, args: dict) -> dict:
    content = str(args.get("content") or "").strip()
    remind_at = str(args.get("remind_at") or "").strip().replace("T", " ")
    if not content or not remind_at:
        return {"ok": False, "error": "提醒内容与时间不能为空"}
    # 归一化到 YYYY-MM-DD HH:MM(:SS)
    try:
        datetime.strptime(remind_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            datetime.strptime(remind_at, "%Y-%m-%d %H:%M")
            remind_at += ":00"
        except ValueError:
            return {
                "ok": False,
                "error": f"提醒时间无法解析：{remind_at}（应为 YYYY-MM-DD HH:MM）",
            }
    rows = cpp_bridge.execute(
        "INSERT INTO reminder (user_id, content, remind_at, task_id) VALUES (?, ?, ?, ?)",
        [user_id, content, remind_at, task_id],
    )
    return {
        "ok": True,
        "result": {"reminder_id": rows[1], "content": content, "remind_at": remind_at},
    }


def _exec_reserve_seat(user_id: int, task_id: int, args: dict) -> dict:
    try:
        seat_id = int(args.get("seat_id") or 0)
        date = str(args.get("date") or "").strip()
        begin = str(args.get("begin_time") or "").strip()
        end = str(args.get("end_time") or "").strip()
    except (TypeError, ValueError):
        return {"ok": False, "error": "预约参数格式错误"}
    if not seat_id or not date or not begin or not end:
        return {
            "ok": False,
            "error": "缺少预约参数：需 seat_id、date(YYYY-MM-DD)、begin_time/end_time(HH:MM)",
        }
    if begin >= end:
        return {"ok": False, "error": "结束时间需晚于开始时间"}
    # 与 routers/library.py reserve 相同的冲突校验 + 落库
    with cpp_bridge.begin():
        rows = cpp_bridge.query(
            "SELECT COUNT(*) AS c FROM seat_reservation "
            "WHERE seat_id = ? AND reserve_date = ? AND status IN (0,1) "
            "AND begin_time < ? AND end_time > ?",
            [seat_id, date, end, begin],
        )
        if int(rows[0]["c"]) > 0:
            return {"ok": False, "error": f"座位 {seat_id} 在 {date} {begin}-{end} 已被预约"}
        reservation_id = cpp_bridge.library_dao().reserve(
            seat_id, user_id, date, begin, end
        )
    return {
        "ok": True,
        "result": {
            "reservation_id": reservation_id,
            "seat_id": seat_id,
            "date": date,
            "begin_time": begin,
            "end_time": end,
        },
    }


def _exec_query_free_room(user_id: int, task_id: int, args: dict) -> dict:
    campus = str(args.get("campus") or "")
    floor = str(args.get("floor") or "")
    rooms = cpp_bridge.library_dao().find_free_rooms(campus, floor, "")
    return {"ok": True, "result": {"count": len(rooms), "rooms": rooms}}


def _exec_post_secondhand(user_id: int, task_id: int, args: dict) -> dict:
    title = str(args.get("title") or "").strip()
    if not title:
        return {"ok": False, "error": "发布标题不能为空"}
    try:
        price = float(args.get("price") or 0)
    except (TypeError, ValueError):
        price = 0.0
    description = str(args.get("description") or "")
    category = str(args.get("category") or "")
    item_id = cpp_bridge.secondhand_dao().publish(
        user_id, title, description, category, price
    )
    return {"ok": True, "result": {"item_id": item_id, "title": title, "price": price}}


_EXECUTORS: dict[str, Callable[[int, int, dict], dict]] = {
    "add_reminder": _exec_add_reminder,
    "reserve_seat": _exec_reserve_seat,
    "query_free_room": _exec_query_free_room,
    "post_secondhand": _exec_post_secondhand,
}
