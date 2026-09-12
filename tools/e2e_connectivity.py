#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""前后端真实连通性测试（按前端实际调用的接口逐个打请求）。

与 audit_frontend_backend.py 的区别：
  · 那个做**静态契约比对**（前端写了什么 vs 后端有什么）
  · 这个做**动态连通性验证**（真的发请求，看返回码与业务码）

用法：
    python tools/e2e_connectivity.py
"""
from __future__ import annotations

import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
RESULTS: list[tuple[str, str, int, int, str]] = []  # (模块, 接口, http, code, 备注)


def rec(module: str, api: str, http: int, code: int, note: str = "") -> None:
    RESULTS.append((module, api, http, code, note))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    c = httpx.Client(base_url=BASE, timeout=120, trust_env=False)

    print("=" * 84)
    print("前后端真实连通性测试")
    print("=" * 84)

    # ---------- 0. 健康检查 ----------
    # 注意：health 挂在 api_prefix 下，即 /api/v1/health；
    #       直接请求根路径 /health 会返回 404（部署探活时容易踩坑，见待办 FEAT-10）
    r = httpx.get("http://127.0.0.1:8000/api/v1/health", timeout=15, trust_env=False)
    rec("系统", "GET /api/v1/health", r.status_code, 0 if r.status_code == 200 else -1)
    try:
        h = r.json()
        print(f"[健康检查] HTTP {r.status_code}  db={h.get('db')}  cpp_ext={h.get('cpp_ext')}  pool={h.get('pool')}")
    except Exception:
        print(f"[健康检查] HTTP {r.status_code}（响应非 JSON，后端可能未启动）")

    # ---------- 1. 登录 ----------
    r = c.post("/auth/wechat-login", json={"code": f"audit_{int(time.time())}"})
    d = r.json()
    rec("认证", "POST /auth/wechat-login", r.status_code, d.get("code", -1))
    tok = d["data"]["token"]
    uid = d["data"]["user"]["id"]
    hh = {"Authorization": f"Bearer {tok}"}
    print(f"[登录] 用户 id={uid}")

    # ---------- 2. 逐个模块 ----------
    cases = [
        # (模块, 方法, 路径, 参数, 说明)
        ("用户", "GET", "/user/me", None, "我的信息"),
        ("聊天", "POST", "/chat/quick", {"keyword": "查空教室"}, "快捷指令"),
        ("聊天", "GET", "/chat/conversations", None, "会话列表（F4历史）"),
        ("帖子", "GET", "/topics", {"page": 1, "size": 5}, "帖子列表"),
        ("帖子", "GET", "/topics/hot", None, "热门帖"),
        ("帖子", "GET", "/topics/mine", {"page": 1, "size": 5}, "我的帖子"),
        ("帖子", "POST", "/topics", {"title": "连通性测试帖", "content": "audit", "category": "综合"}, "发帖(含审核)"),
        ("收藏", "GET", "/favorites", {"target_type": "topic"}, "我的收藏"),
        ("图书馆", "GET", "/library/free-rooms", {"campus": "", "floor": "", "period": ""}, "查空教室"),
        ("图书馆", "GET", "/library/reservations/me", None, "我的预约"),
        ("二手", "GET", "/secondhand/items", {"page": 1, "size": 5}, "二手列表"),
        ("二手", "GET", "/secondhand/items/mine", {"page": 1, "size": 5}, "我的发布"),
        ("兼职", "GET", "/jobs", {"page": 1, "size": 5}, "岗位列表"),
        ("兼职", "GET", "/jobs/applications/me", None, "我的申请"),
        ("生活", "GET", "/life/merchants", None, "商家列表"),
        ("生活", "GET", "/life/notices", None, "通知列表"),
        ("地图", "GET", "/map/pois", None, "POI"),
        ("地图", "GET", "/map/nearby", {"lat": 43.8, "lng": 125.3}, "附近"),
        ("地图", "GET", "/map/building/1", None, "建筑详情(图书馆借用)"),
        ("Agent", "GET", "/agent/tasks", None, "任务列表"),
        ("Agent", "GET", "/agent/reminders", None, "提醒列表"),
    ]

    print("\n" + "=" * 84)
    print(f"{'模块':<8}{'接口':<42}{'HTTP':<7}{'业务码':<8}{'结果'}")
    print("-" * 84)
    for module, method, path, params, note in cases:
        try:
            if method == "GET":
                r = c.get(path, headers=hh, params=params)
            else:
                r = c.request(method, path, headers=hh, json=params)
            try:
                body = r.json()
                code = body.get("code", -1)
            except Exception:
                code = -999
            ok = r.status_code < 400 and code == 0
            rec(module, f"{method} {path}", r.status_code, code, note)
            flag = "✅" if ok else "⚠️"
            print(f"{module:<8}{method + ' ' + path:<42}{r.status_code:<7}{code:<8}{flag} {note}")
        except Exception as exc:
            rec(module, f"{method} {path}", -1, -1, f"异常:{exc}")
            print(f"{module:<8}{method + ' ' + path:<42}{'ERR':<7}{'-':<8}❌ {exc}")

    # ---------- 3. SSE 流式 ----------
    print("\n" + "=" * 84)
    print("[SSE 流式] POST /chat/send")
    t0 = time.time()
    chunks, sources, done = 0, 0, False
    try:
        with c.stream("POST", "/chat/send", headers=hh, json={"content": "图书馆在哪里？"}) as resp:
            event = None
            for line in resp.iter_lines():
                if line.startswith("event: "):
                    event = line[7:]
                elif line.startswith("data: ") and event:
                    if event == "sources":
                        sources = len(json.loads(line[6:]))
                    elif event == "chunk":
                        chunks += 1
                    elif event == "done":
                        done = True
        print(f"  耗时 {time.time()-t0:.1f}s ｜ 分片 {chunks} ｜ RAG来源 {sources} ｜ done事件 {done}")
        print(f"  {'✅ 流式正常' if done and chunks > 0 else '⚠️ 流式异常'}")
        rec("聊天", "POST /chat/send (SSE)", 200, 0, f"{chunks}分片/{sources}来源")
    except Exception as exc:
        print(f"  ❌ 异常: {exc}")
        rec("聊天", "POST /chat/send (SSE)", -1, -1, str(exc))

    # ---------- 4. 汇总 ----------
    ok_n = sum(1 for _, _, h, cd, _ in RESULTS if h and h < 400 and cd == 0)
    print("\n" + "=" * 84)
    print(f"汇总：{ok_n}/{len(RESULTS)} 通过")
    print("=" * 84)
    return 0 if ok_n == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
