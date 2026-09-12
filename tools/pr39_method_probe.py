"""PR #39 深度审查：写接口「路由 + 方法」存在性探测（无副作用）。

目的：验证前端调用的非 GET 接口在 `dev` 后端**路径与方法都正确**。
判定：HTTP 404 = 路径不存在（严重）｜405 = 方法不匹配（严重）｜其余 = 路由与方法均存在。

设计原则：**只用「必然失败但无副作用」的入参**（不存在的 id / 假 token），
不会写入业务数据（UPDATE 命中 0 行、INSERT 被业务校验拦截）。
刻意跳过会产生真实数据的接口（`POST /topics`、`/life/orders`、`/secondhand/items`、`/agent/tasks`）。

运行：
    E:/miniconda3/python.exe -X utf8 tools/pr39_method_probe.py
"""

from __future__ import annotations

import sys

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
CLIENT = httpx.Client(trust_env=False, timeout=20.0)

# (标签, 方法, 路径, body, params)
CASES: list[tuple[str, str, str, dict | None, dict | None]] = [
    ("帖·点赞", "POST", "/topics/999999/like", None, None),
    ("帖·评论", "POST", "/topics/999999/comments", {"content": "probe"}, None),
    ("帖·审核状态", "GET", "/topics/999999/audit-status", None, None),
    ("收藏·切换", "POST", "/favorites", {"target_type": "topic", "target_id": 999999}, None),
    ("收藏·列表", "GET", "/favorites", None, {"target_type": "topic"}),
    ("图书馆·座位", "GET", "/library/rooms/1/seats", None, {"date": "2026-09-15"}),
    ("图书馆·取消预约", "POST", "/library/reservations/999999/cancel", None, None),
    ("图书馆·我的预约", "GET", "/library/reservations/me", None, None),
    ("通知·标记已读", "POST", "/life/notices/999999/read", None, None),
    ("订单·详情", "GET", "/life/orders/999999", None, None),
    ("Agent·取消任务", "POST", "/agent/tasks/999999/cancel", None, None),
    ("Agent·提醒完成", "PUT", "/agent/reminders/999999/done", None, None),
    ("Agent·任务列表", "GET", "/agent/tasks", None, {"page": 1, "size": 1}),
    ("二手·改状态", "PUT", "/secondhand/items/999999/status", {"status": 2}, None),
    ("二手·详情（已知缺失）", "GET", "/secondhand/items/999999", None, None),
    ("认证·登出", "POST", "/auth/logout", None, None),
    ("认证·刷新（假 token）", "POST", "/auth/refresh", {"refresh_token": "invalid.token.x"}, None),
    ("二手·AI 描述", "POST", "/secondhand/items/ai-describe", {"user_note": "教材"}, None),
    ("兼职·申请列表", "GET", "/jobs/applications/me", None, None),
    ("聊天·历史消息", "GET", "/chat/conversations/999999/messages", None, None),
]


def main() -> int:
    print(f"目标：{BASE}")
    print("=" * 96)
    bad = 0
    for label, method, path, body, params in CASES:
        try:
            r = CLIENT.request(method, BASE + path, json=body, params=params)
            status = r.status_code
            try:
                code = r.json().get("code", "-")
            except Exception:  # noqa: BLE001
                code = "非JSON"
        except Exception as exc:  # noqa: BLE001
            print(f"  {label:<18} {method:<5} {path:<42} 异常 {exc}")
            bad += 1
            continue

        if status == 404:
            verdict = "❌ 路径不存在"
            bad += 1
        elif status == 405:
            verdict = "❌ 方法不匹配"
            bad += 1
        elif status in (200, 400, 401, 403, 429):
            verdict = "✅ 路由+方法正确"
        else:
            verdict = "❓ 需人工"
        print(f"  {label:<18} {method:<5} {path:<42} HTTP {status:<4} code={str(code):<6} {verdict}")

    print("=" * 96)
    print(f"严重问题（404/405/异常）：{bad} 个")
    print("注：本脚本只用不存在的 id / 假 token，不写入业务数据。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
