#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""前后端连接性与接口契约审查。

做三件事：
  1. 提取前端（小程序）所有 API 调用点（路径 + 方法 + 是否 SSE）
  2. 提取后端全部路由
  3. 逐项比对，输出：前端调用了但后端没有的（会 404）、后端有但前端没用的（功能缺口）

用法：
    python tools/audit_frontend_backend.py
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

FRONTEND = pathlib.Path(r"d:\xiaojietongproject\xjt-frontend\miniprogram")
BACKEND = pathlib.Path(r"d:\xiaojietongproject\xiaojietong-project\backend\app\routers")

# 前端调用：request('...') / sseRequest('...') ；同时捕获 method
CALL_RE = re.compile(
    r"""(?P<fn>request|sseRequest)\(\s*(?P<q>['"`])(?P<path>[^'"`]+)(?P=q)"""
    r"""(?P<rest>.{0,200}?)""" r"""(?:method:\s*(?P<m>['"`])(?P<method>[A-Z]+)(?P=m))?""",
    re.S,
)

# 后端路由：@router.get("/xxx") 等
ROUTE_RE = re.compile(
    r"""@router\.(?P<method>get|post|put|delete)\(\s*['"](?P<path>[^'"]*)['"]"""
)

PREFIX_RE = re.compile(r"""prefix\s*=\s*['"](?P<prefix>[^'"]+)['"]""")


def collect_frontend() -> list[dict]:
    """返回 [{file, path, method, sse}]。"""
    out: list[dict] = []
    for f in sorted(FRONTEND.rglob("*.js")):
        txt = f.read_text(encoding="utf-8", errors="replace")
        for m in CALL_RE.finditer(txt):
            # 拼接式路径（'a/' + id + '/b'）只取首个字符串字面量，标记为动态
            raw = m.group("path")
            rest = m.group("rest") or ""
            dynamic = bool(re.search(r"['\"`]\s*\+", txt[m.end("q") + len(raw): m.end("q") + len(raw) + 8]))
            method = (m.group("method") or "GET").upper()
            out.append(
                {
                    "file": str(f.relative_to(FRONTEND)).replace("\\", "/"),
                    "path": raw,
                    "method": method,
                    "sse": m.group("fn") == "sseRequest",
                    "dynamic": dynamic,
                }
            )
    return out


def collect_backend() -> list[dict]:
    """返回 [{method, full}]，full 形如 /api/v1/xxx。"""
    out: list[dict] = []
    for f in sorted(BACKEND.glob("*.py")):
        txt = f.read_text(encoding="utf-8", errors="replace")
        pm = PREFIX_RE.search(txt)
        prefix = pm.group("prefix") if pm else ""
        for m in ROUTE_RE.finditer(txt):
            out.append(
                {
                    "method": m.group("method").upper(),
                    "full": "/api/v1" + prefix + m.group("path"),
                    "file": f.name,
                }
            )
    return out


def normalize(p: str) -> str:
    """去掉路径参数取值，归一成可比较形式。"""
    p = p.split("?")[0]
    p = re.sub(r"\$\{[^}]+\}", "{x}", p)
    p = re.sub(r"\{[^}]+\}", "{x}", p)
    return p.rstrip("/") or "/"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    fe = collect_frontend()
    be = collect_backend()
    be_norm = {normalize(b["full"]): b for b in be}

    print("=" * 78)
    print(f"前端 API 调用点：{len(fe)} 处   ｜   后端路由：{len(be)} 条")
    print("=" * 78)

    print("\n【一】前端调用点全清单")
    print(f"{'文件':<40} {'方法':<6} {'路径'}")
    print("-" * 78)
    for c in fe:
        tag = " [SSE]" if c["sse"] else ""
        dyn = " (动态拼接)" if c["dynamic"] else ""
        print(f"{c['file']:<40} {c['method']:<6} {c['path']}{tag}{dyn}")

    print("\n【二】契约比对（前端调用 → 后端是否存在）")
    print("-" * 78)
    missing: list[dict] = []
    # 手工映射：前端写片段路径的，按已知前缀补全
    PREFIX_HINTS = {
        "auth/": "/api/v1/", "chat/": "/api/v1/", "agent/": "/api/v1/",
        "library/": "/api/v1/", "secondhand/": "/api/v1/", "job/": "/api/v1/",
        "jobs": "/api/v1/jobs", "favorites": "/api/v1/favorites",
        "user/": "/api/v1/", "topics": "/api/v1/topics", "map/": "/api/v1/",
        "life/": "/api/v1/",
    }
    for c in fe:
        raw = c["path"]
        if raw.startswith("/pages/"):
            continue  # 页面跳转，不是 API
        if raw.startswith("/"):
            cand = "/api/v1" + raw
        else:
            cand = "/api/v1/" + raw
        n = normalize(cand)
        hit = be_norm.get(n)
        if not hit and c["dynamic"]:
            # 动态拼接：用前缀粗匹配
            base = normalize("/api/v1/" + raw).rstrip("/")
            hit = next((b for k, b in be_norm.items() if k.startswith(base)), None)
        status = "✅" if hit else "❓"
        if not hit:
            missing.append(c)
        print(f"  {status} {c['method']:<5} {raw:<38} -> {cand}")

    print("\n【三】后端有但前端未接入的接口（功能缺口）")
    print("-" * 78)
    fe_norms = set()
    for c in fe:
        raw = c["path"]
        if raw.startswith("/pages/"):
            continue
        cand = ("/api/v1" + raw) if raw.startswith("/") else ("/api/v1/" + raw)
        fe_norms.add(normalize(cand))
        if c["dynamic"]:
            base = normalize(cand).rstrip("/")
            fe_norms.add(base)
    unused = []
    for b in be:
        n = normalize(b["full"])
        if not any(n == f or n.startswith(f.rstrip("/") + "/") or f.startswith(n.rstrip("/") + "/") for f in fe_norms):
            unused.append(b)
    for b in unused:
        print(f"  · {b['method']:<6} {b['full']}   [{b['file']}]")
    if not unused:
        print("  （全部后端接口均被前端使用）")

    print("\n" + "=" * 78)
    print(f"汇总：疑似未匹配 {len(missing)} 处 ｜ 后端未接入 {len(unused)} 条")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
