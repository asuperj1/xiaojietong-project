#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""前端体验走查（UX Probe）—— 按 A 分册的用户旅程实测接口层体验。

在**无法操作微信开发者工具**的条件下，尽可能把「前端使用体验」中
**可客观测量**的部分自动化：

  1. 旅程可用性：每个用户旅程步骤是否都能拿到 `code=0`
  2. 响应延迟：> 500ms 标 ⚠️，> 1500ms 标 ❌（用户可感知的卡顿）
  3. 数据质量：列表条数、关键字段是否缺失（导致前端渲染空白/破图）
  4. 空态覆盖：列表为空时前端是否有兜底（此项需人工看，这里只报告数据状态）

用法：
    E:/miniconda3/python.exe -X utf8 tools/ux_probe.py
"""
from __future__ import annotations

import sys
import time

import httpx

SLOW, VSLOW = 500, 1500
OK, WARN, FAIL = "✅", "⚠️", "❌"

ROWS: list[tuple[str, str, str, int, str]] = []  # (旅程, 接口, 状态, 耗时, 备注)


def rec(journey: str, api: str, status: str, ms: int, note: str = "") -> None:
    ROWS.append((journey, api, status, ms, note))
    print(f"  {status} {api:<42} {ms:>5}ms  {note}")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    c = httpx.Client(base_url="http://127.0.0.1:8000/api/v1", timeout=90, trust_env=False)
    print("=" * 92)
    print("校捷通 · 前端体验走查（接口层）  " + time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 92)

    # ---------- 旅程 0：登录 ----------
    print("\n【旅程 ① 启动与登录】")
    t0 = time.perf_counter()
    login = c.post("/auth/wechat-login", json={"code": "seed-user-alpha"})
    ms = int((time.perf_counter() - t0) * 1000)
    b = login.json()
    token = ((b.get("data") or {}).get("token")) or ""
    user = (b.get("data") or {}).get("user") or {}
    rec("① 登录", "POST /auth/wechat-login", OK if b.get("code") == 0 else FAIL, ms,
        f"code={b.get('code')} 昵称={user.get('nickname')}")
    # 前端首屏需要的用户字段
    need_user = ["id", "nickname", "avatar", "role", "grade", "major", "campus"]
    miss = [k for k in need_user if k not in user]
    rec("① 登录", "  └ user 字段完整性", OK if not miss else FAIL, 0,
        "全部齐备" if not miss else f"缺 {miss}（前端会渲染空白）")
    h = {"Authorization": f"Bearer {token}"}

    # ---------- 旅程 ② 首页 ----------
    print("\n【旅程 ② 首页聚合】")
    for api in ("/user/me", "/library/free-rooms", "/topics/hot"):
        t0 = time.perf_counter()
        r = c.get(api, headers=h)
        ms = int((time.perf_counter() - t0) * 1000)
        try:
            bb = r.json()
        except Exception:
            rec("② 首页", api, FAIL, ms, f"非 JSON（HTTP {r.status_code}）")
            continue
        d = bb.get("data") or {}
        st = OK if bb.get("code") == 0 else FAIL
        if isinstance(d, dict) and "items" in d:
            n = len(d["items"])
            if bb.get("code") == 0 and n == 0:
                st = WARN
        else:
            n = "-"  # 非列表接口（如 /user/me 返回对象）
        rec("② 首页", api, st, ms, f"code={bb.get('code')} 条数={n}")

    # ---------- 旅程 ③ AI 助手 ----------
    print("\n【旅程 ③ AI 助手（含首字延迟）】")
    t0 = time.perf_counter()
    r = c.get("/chat/conversations", headers=h)
    ms = int((time.perf_counter() - t0) * 1000)
    rec("③ AI", "GET /chat/conversations", OK if r.json().get("code") == 0 else FAIL, ms,
        f"会话数={len((r.json().get('data') or {}).get('items', []))}")

    # SSE 首字延迟：这是用户最直观的体验指标
    t0 = time.perf_counter()
    first_ms = None
    try:
        with c.stream("POST", "/chat/send", headers=h,
                      json={"message": "图书馆几点关门", "conversation_id": None}) as resp:
            for line in resp.iter_lines():
                if line and (line.startswith("data:") or "chunk" in str(line)):
                    first_ms = int((time.perf_counter() - t0) * 1000)
                    break
        rec("③ AI", "POST /chat/send 首字延迟",
            OK if (first_ms or 99999) < 3000 else WARN, first_ms or -1,
            "用户等待感知良好" if (first_ms or 99999) < 3000 else "等待偏久（受模型与硬件影响）")
    except Exception as exc:  # noqa: BLE001
        rec("③ AI", "POST /chat/send 首字延迟", FAIL, -1, f"异常 {type(exc).__name__}: {exc}")

    # ---------- 旅程 ④ 服务/图书馆 ----------
    print("\n【旅程 ④ 图书馆】")
    for api in ("/library/free-rooms", "/library/reservations/me"):
        t0 = time.perf_counter()
        r = c.get(api, headers=h)
        ms = int((time.perf_counter() - t0) * 1000)
        bb = r.json()
        n = len((bb.get("data") or {}).get("items", []))
        rec("④ 图书馆", api, OK if bb.get("code") == 0 else FAIL, ms, f"条数={n}")

    # ---------- 旅程 ⑤ 二手（重点：图片字段） ----------
    print("\n【旅程 ⑤ 二手交易（图片/字段是体验重灾区）】")
    t0 = time.perf_counter()
    r = c.get("/secondhand/items", headers=h)
    ms = int((time.perf_counter() - t0) * 1000)
    d = r.json().get("data") or {}
    items = d.get("items", [])
    rec("⑤ 二手", "GET /secondhand/items", OK if r.json().get("code") == 0 else FAIL, ms,
        f"条数={len(items)} total={d.get('total')}")
    if items:
        it = items[0]
        fields = ["id", "title", "price", "images", "condition_level", "category"]
        miss = [k for k in fields if k not in it]
        rec("⑤ 二手", "  └ 列表字段完整性", OK if not miss else FAIL, 0,
            "齐备" if not miss else f"缺 {miss}")
        imgs = it.get("images")
        rec("⑤ 二手", "  └ images 类型", OK if isinstance(imgs, list) else FAIL, 0,
            f"list（{len(imgs)} 张）" if isinstance(imgs, list) else f"异常类型 {type(imgs).__name__}（前端渲染会出错）")
        # 详情页字段
        t0 = time.perf_counter()
        r2 = c.get(f"/secondhand/items/{it.get('id')}", headers=h)
        ms2 = int((time.perf_counter() - t0) * 1000)
        rec("⑤ 二手", f"GET /secondhand/items/{{id}}",
            OK if r2.json().get("code") == 0 else FAIL, ms2,
            f"code={r2.json().get('code')}")

    # ---------- 旅程 ⑥ 论坛 ----------
    print("\n【旅程 ⑥ 论坛（列表→详情→评论）】")
    t0 = time.perf_counter()
    r = c.get("/topics", headers=h)
    ms = int((time.perf_counter() - t0) * 1000)
    d = r.json().get("data") or {}
    topics = d.get("items", [])
    rec("⑥ 论坛", "GET /topics", OK if r.json().get("code") == 0 else FAIL, ms,
        f"条数={len(topics)} total={d.get('total')}")
    if topics:
        tid = topics[0].get("id")
        t0 = time.perf_counter()
        r2 = c.get(f"/topics/{tid}", headers=h)
        ms2 = int((time.perf_counter() - t0) * 1000)
        d2 = r2.json().get("data") or {}
        rec("⑥ 论坛", f"GET /topics/{{id}}", OK if r2.json().get("code") == 0 else FAIL, ms2,
            f"含 comments={'comments' in d2}")

    # ---------- 旅程 ⑦ 我的（收藏等） ----------
    print("\n【旅程 ⑦ 我的】")
    for api in ("/user/tags", "/favorites", "/agent/tasks", "/agent/reminders"):
        t0 = time.perf_counter()
        r = c.get(api, headers=h)
        ms = int((time.perf_counter() - t0) * 1000)
        bb = r.json()
        dd = bb.get("data") or {}
        n = len(dd.get("items", dd.get("tags", []))) if isinstance(dd, dict) else 0
        rec("⑦ 我的", api, OK if bb.get("code") == 0 else FAIL, ms, f"条数={n}")

    # ---------- 汇总 ----------
    print("\n" + "=" * 92)
    n_ok = sum(1 for r in ROWS if r[2] == OK)
    n_warn = sum(1 for r in ROWS if r[2] == WARN)
    n_fail = sum(1 for r in ROWS if r[2] == FAIL)
    print(f"通过 {OK} {n_ok} ｜ 警告 {WARN} {n_warn} ｜ 失败 {FAIL} {n_fail}")

    timed = [r for r in ROWS if r[3] > 0]
    if timed:
        timed.sort(key=lambda x: -x[3])
        print("\n最慢 5 个接口（体验最关键）：")
        for j, api, st, ms, note in timed[:5]:
            flag = FAIL if ms > VSLOW else (WARN if ms > SLOW else "  ")
            print(f"  {flag} {ms:>5}ms  {api}")

    bad = [r for r in ROWS if r[2] == FAIL]
    if bad:
        print("\n失败项（会导致前端体验问题）：")
        for j, api, st, ms, note in bad:
            print(f"  {FAIL} [{j}] {api} — {note}")
        return 1
    print("\n接口层体验走查完成 —— 下一步需人工用微信开发者工具核对渲染/交互（见报告「需人工验证」清单）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
