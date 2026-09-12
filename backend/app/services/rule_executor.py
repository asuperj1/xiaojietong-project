"""规则执行器（B7）：模型不可用时的兜底解析（正则 → 工具调用，**真实执行**）。

与 ``services/agent_executor.py``（模型 Function Call 主路径）构成三级链路：
1. 模型可用 → Function Calling（主路径）；
2. 模型不可用 / 未返回工具调用 → **本模块规则解析**（真写库，非占位）；
3. 两者都未命中 → ``agent_task.status=3`` 失败 + ``error_msg``（**禁止假成功**）。

支持的自然语言模式（演示级，可持续扩充）：
- 提醒：「10 分钟后提醒我交作业」「提醒我明天下午 4 点交作业」「今晚 8 点提醒我开会」
- 预约：「帮我预约 1 号座位明天上午 9 点到 11 点」
- 空教室：「查一下空教室」「有没有空教室」
- 发布：「出一本高数教材，25 元」

返回格式与模型 tool_calls 对齐：[{"name": ..., "arguments": {...}}]，可直接交给
``agent_executor.execute_calls`` 执行。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

_PERIOD_HINT = ("上午", "下午", "晚上", "中午", "傍晚", "凌晨")


def parse(instruction: str) -> list[dict]:
    """规则解析指令为工具调用；无法识别返回空列表。"""
    text = (instruction or "").strip()
    if not text:
        return []

    if "提醒" in text:
        call = _parse_reminder(text)
        if call:
            return [call]

    if "座位" in text and ("预约" in text or "约" in text):
        call = _parse_reserve(text)
        if call:
            return [call]

    if "教室" in text and ("查" in text or "空" in text):
        return [{"name": "query_free_room", "arguments": {}}]

    if any(k in text for k in ("出", "卖", "发布", "转让")) and "元" in text:
        call = _parse_publish(text)
        if call:
            return [call]

    return []


# ------------------------------------------------------------ 时间解析 ----

def _parse_time(text: str) -> Optional[str]:
    """从文本解析绝对时间（YYYY-MM-DD HH:MM）；解析失败返回 None。

    支持：X 分钟后 / X 小时后 / （今天|明天|后天|今晚）（上午|下午|晚上）X 点(Y 分)。
    未指定日期的时刻若已过，自动顺延到明天。
    """
    now = datetime.now()

    m = re.search(r"(\d+)\s*分钟\s*后", text)
    if m:
        return (now + timedelta(minutes=int(m.group(1)))).strftime("%Y-%m-%d %H:%M")

    m = re.search(r"(\d+)\s*(?:个)?\s*小时\s*后", text)
    if m:
        return (now + timedelta(hours=int(m.group(1)))).strftime("%Y-%m-%d %H:%M")

    day = 0
    if "后天" in text:
        day = 2
    elif "明天" in text or "明晚" in text:
        day = 1
    elif "今天" in text or "今晚" in text:
        day = 0

    m = re.search(r"(上午|下午|晚上|中午|傍晚|凌晨)?\s*(\d{1,2})\s*[:点]\s*(\d{0,2})", text)
    if not m:
        return None
    period = m.group(1) or ""
    hour = int(m.group(2))
    minute = int(m.group(3) or 0)
    if period in ("下午", "晚上", "傍晚") and hour < 12:
        hour += 12
    if period == "凌晨" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None

    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=day)
    if day == 0 and target <= now:
        target += timedelta(days=1)  # 今天该时刻已过 → 顺延明天
    return target.strftime("%Y-%m-%d %H:%M")


def _strip_time_phrases(text: str) -> str:
    """剔除文本中的时间描述，保留正文（用于提醒内容提取）。"""
    t = text
    t = re.sub(r"(今天|明天|后天|大后天|今晚|明晚)\s*", "", t)
    t = re.sub(r"(" + "|".join(_PERIOD_HINT) + r")\s*", "", t)
    t = re.sub(r"\d{1,2}\s*[:点]\s*\d{0,2}\s*分?", "", t)
    t = re.sub(r"\d+\s*(?:个)?\s*(?:分钟|小时)\s*后", "", t)
    return t.strip(" ，,。的 ")


# ------------------------------------------------------------ 各工具解析 ----

def _parse_reminder(text: str) -> Optional[dict]:
    remind_at = _parse_time(text)
    if not remind_at:
        return None
    m = re.search(r"提醒我?\s*(.+)$", text)
    content = _strip_time_phrases(m.group(1)) if m else ""
    if not content:
        content = "待办事项"
    return {
        "name": "add_reminder",
        "arguments": {"content": content[:100], "remind_at": remind_at},
    }


def _parse_reserve(text: str) -> Optional[dict]:
    m_seat = re.search(r"(\d+)\s*号?\s*座位", text) or re.search(r"座位\s*(\d+)\s*号?", text)
    if not m_seat:
        return None
    seat_id = int(m_seat.group(1))

    when = _parse_time(text)
    if not when:
        return None
    date_only = when.split(" ")[0]

    m_span = re.search(
        r"(上午|下午|晚上|中午)?\s*(\d{1,2})\s*[:点]?\s*\d{0,2}\s*分?\s*(?:到|至|-|~|—)\s*"
        r"(上午|下午|晚上|中午)?\s*(\d{1,2})\s*[:点]?\s*\d{0,2}\s*分?",
        text,
    )
    if not m_span:
        return None

    def to_hhmm(period: Optional[str], hour: str) -> str:
        h = int(hour)
        if period in ("下午", "晚上") and h < 12:
            h += 12
        return f"{h:02d}:00"

    begin = to_hhmm(m_span.group(1), m_span.group(2))
    end = to_hhmm(m_span.group(3), m_span.group(4))
    return {
        "name": "reserve_seat",
        "arguments": {
            "seat_id": seat_id,
            "date": date_only,
            "begin_time": begin,
            "end_time": end,
        },
    }


def _parse_publish(text: str) -> Optional[dict]:
    m = re.search(
        r"(?:出|卖|发布|转让)\s*(?:一)?\s*(?:本|台|个|件|张|把|部|套)?\s*([^，,。]+?)\s*[，,]\s*(\d+(?:\.\d+)?)\s*元",
        text,
    )
    if not m:
        m = re.search(
            r"(?:出|卖|发布|转让)\s*(?:一)?\s*(?:本|台|个|件|张|把|部|套)?\s*([^，,。]+?)\s*(\d+(?:\.\d+)?)\s*元",
            text,
        )
    if not m:
        return None
    title = m.group(1).strip(" ，,。")
    if not title:
        return None
    return {
        "name": "post_secondhand",
        "arguments": {"title": title[:100], "price": float(m.group(2))},
    }
