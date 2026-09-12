#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""封测开机自检（Preflight Check）。

每轮封测开始前运行一次，一键确认环境是否满足
《docs/验收测试/00-第一阶段验收测试方案.md》§2 的准入条件。

检查项
------
E1 后端健康          GET /api/v1/health
E2 组件状态          GET /api/v1/health/detail（db / cpp_ext / ollama / 知识库 / celery 模式）
E3 Ollama 模型清单    必需 qwen2.5:3b、xjt-3b、bge-m3
E4 登录可用          mock 登录取 token
E5 种子数据齐备       8 个列表接口是否有数据
E6 签名闭环（P1）     业务接口返回的图片 / 头像 URL 是否带签名
E7 前端 BASE_URL      是否仍写死 127.0.0.1（真机封测阻塞项）
E8 关键接口冒烟       12 个模块各取 1 个接口
E9 已知缺陷复核       XFF 限流绕过等（只报告，不计入失败）

用法
----
    python tools/preflight_check.py
    python tools/preflight_check.py --base http://192.168.1.10:8000/api/v1
    python tools/preflight_check.py --write      # 额外做会写数据的检查（构造头像用例）

退出码：0 = 可开测（无阻塞项）；1 = 存在阻塞项

注意：必须用带 httpx 的解释器，例如 E:/miniconda3/python.exe。
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

import httpx

OK, FAIL, WARN, SKIP = "✅", "❌", "⚠️", "⏭"

ROWS: list[tuple[str, str, str, str]] = []
BLOCKING = 0

FRONTEND_REQUEST_JS = pathlib.Path(
    r"d:\xiaojietongproject\xjt-frontend\miniprogram\services\request.js"
)


def rec(code: str, name: str, status: str, note: str = "", blocking: bool = False) -> None:
    global BLOCKING
    if blocking and status == FAIL:
        BLOCKING += 1
    ROWS.append((code, name, status, note))
    line = f"  {status} {code} {name}"
    if note:
        line += f"  — {note}"
    print(line)


def get(c: httpx.Client, path: str, **kw):
    """安全 GET：返回 (json|None, 说明)。"""
    try:
        r = c.get(path, **kw)
        try:
            body = r.json()
        except Exception:
            return None, f"HTTP {r.status_code} 非 JSON"
        return body, f"HTTP {r.status_code} code={body.get('code')}"
    except Exception as exc:  # noqa: BLE001
        return None, f"请求异常 {exc}"


def is_signed(url: str) -> bool:
    """判断是否带签名（?e=...&s=...）。"""
    return bool(url) and "e=" in url and "s=" in url


def section(title: str) -> None:
    print(f"\n{title}")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000/api/v1")
    ap.add_argument("--write", action="store_true", help="执行会写数据的检查")
    args = ap.parse_args()

    print("=" * 78)
    print("校捷通 · 封测开机自检（Preflight Check）")
    print(f"目标：{args.base}   时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 78)

    # ★ trust_env=False：绕开 Windows 系统代理，否则本地服务会 502
    c = httpx.Client(base_url=args.base, timeout=60, trust_env=False)

    # ---------------- E1 后端健康 ----------------
    section("E1 · 后端健康")
    health, note = get(c, "/health")
    # /health 是特例：直接返回 {"status":"ok",...}，不走 {code,message,data} 统一体
    if health is not None and (health.get("status") == "ok" or health.get("code") == 0):
        rec("E1", "GET /health", OK,
            f"status={health.get('status')} db={health.get('db')} cpp_ext={health.get('cpp_ext')}")
    else:
        rec("E1", "GET /health", FAIL, note + "（后端未启动？）", blocking=True)
        print("\n⛔ 后端不可达，后续检查无法进行。请先启动后端：")
        print("   cd backend; $env:XJT_DB_PASSWORD='jhq000000'; $env:XJT_DB_PORT='3307';")
        print("   E:\\miniconda3\\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000")
        return 1

    # ---------------- E2 组件状态 ----------------
    section("E2 · 组件状态（health/detail）")
    detail, note = get(c, "/health/detail")
    # /health/detail 同样不走统一体：顶层即数据（兼容 data 包裹形态）
    d = (detail or {}).get("data") or (detail or {})

    db = d.get("db") or {}
    if isinstance(db, dict):
        db_ok = bool(db.get("ping", db.get("pool_ready", db.get("status") in ("ok", True))))
    else:
        db_ok = db in ("ok", "1", 1, True)
    rec("E2.1", "MySQL", OK if db_ok else FAIL, f"db={db}", blocking=not db_ok)

    cpp = d.get("cpp_ext", d.get("cpp", None))
    rec("E2.2", "C++ 扩展 jt_db", OK if cpp in (True, "true", "1", 1) else FAIL,
        f"cpp_ext={cpp}", blocking=True)

    oll = d.get("ollama") or {}
    oll_ok = oll.get("reachable") if isinstance(oll, dict) else None
    rec("E2.3", "Ollama 可达", OK if oll_ok in (True, "true", "1", 1) else FAIL,
        f"reachable={oll_ok}（检查是否开着系统代理/VPN）", blocking=True)

    kn = d.get("knowledge") or {}
    chunk = kn.get("chunks", kn.get("chunk")) if isinstance(kn, dict) else None
    doc = kn.get("docs", kn.get("doc")) if isinstance(kn, dict) else None
    rec("E2.4", "知识库已建索引", OK if (isinstance(chunk, int) and chunk > 0) else FAIL,
        f"doc={doc} chunk={chunk}", blocking=True)

    cel = d.get("celery") or {}
    rec("E2.5", "Celery 运行模式", OK, str(cel) if cel else "未暴露（可选）")

    retr = d.get("retrieval_mode", d.get("retrieval", None))
    deg = d.get("degrade_reason")
    rec("E2.6", "检索模式", OK if (retr == "vector" or not deg) else WARN,
        f"mode={retr}" + (f"；降级原因：{deg}" if deg else ""))

    # E2.7 版本识别：含 B15 的后端才在 health/detail 暴露 celery 字段
    has_b15 = "celery" in d
    rec("E2.7", "后端版本（含 PR#42 修复）",
        OK if has_b15 else WARN,
        "已含 B15 / 评审修复" if has_b15
        else "⚠️ 当前后端不含 B15 —— PR #42 尚未合并或未部署；封测须在合并后跑")

    # ---------------- E3 模型清单 ----------------
    section("E3 · Ollama 模型清单")
    models: list[str] = []
    if isinstance(oll, dict):
        raw = oll.get("models") or oll.get("model_list") or []
        if isinstance(raw, list):
            models = [m if isinstance(m, str) else str(m.get("name", m)) for m in raw]
    need = ["qwen2.5:3b", "xjt-3b", "bge-m3"]
    if models:
        for m in need:
            hit = any(m in x for x in models)
            rec(f"E3", f"模型 {m}", OK if hit else FAIL,
                "" if hit else f"缺失；已装：{', '.join(models)}", blocking=not hit)
    else:
        rec("E3", "模型清单", SKIP, "health/detail 未暴露模型列表；请手工 ollama list 核对")

    # ---------------- E4 登录 ----------------
    section("E4 · 登录")
    token = None
    login_user: dict = {}
    try:
        r = c.post("/auth/wechat-login", json={"code": "preflight-check-code"})
        body = r.json()
        if body.get("code") == 0:
            token = (body.get("data") or {}).get("token")
            login_user = (body.get("data") or {}).get("user") or {}
            rec("E4", "mock 登录取 token", OK, f"user_id={login_user.get('id')} nick={login_user.get('nickname')}")
        else:
            rec("E4", "mock 登录取 token", FAIL, f"code={body.get('code')} {body.get('message')}", blocking=True)
    except Exception as exc:  # noqa: BLE001
        rec("E4", "mock 登录取 token", FAIL, f"异常 {exc}", blocking=True)

    h = {"Authorization": f"Bearer {token}"} if token else {}

    # ---------------- E5 种子数据 ----------------
    section("E5 · 种子数据齐备（列表接口是否为空）")
    lists = [
        ("图书馆", "/library/free-rooms"),
        ("二手", "/secondhand/items"),
        ("兼职", "/jobs"),
        ("论坛", "/topics"),
        ("地图 POI", "/map/pois"),
        ("生活商家", "/life/merchants"),
        ("通知", "/life/notices"),
    ]
    for name, path in lists:
        body, note = get(c, path, headers=h)
        if body is None:
            rec("E5", name, FAIL, note, blocking=True)
            continue
        data = body.get("data") or {}
        items = data.get("items") if isinstance(data, dict) else None
        n = len(items) if isinstance(items, list) else (1 if data else 0)
        note_txt = f"{n} 条" if n > 0 else "接口返回空（需准备种子数据：见方案 §3.3）"
        if n == 0:
            note_txt += "；若确认表内有数据，多为下架/待审状态被正确过滤，请核对 DB"
        rec("E5", name, OK if n > 0 else FAIL, note_txt, blocking=n == 0)

    # ---------------- E6 签名闭环（P1） ----------------
    section("E6 · 签名闭环（B14 P1 修复复核）")
    # 6.1 /user/me 的 avatar
    me, _ = get(c, "/user/me", headers=h)
    me_user = (me or {}).get("data") or {}
    me_avatar = me_user.get("avatar", "") if isinstance(me_user, dict) else ""
    rec("E6.1", "GET /user/me avatar", OK if (not me_avatar or is_signed(me_avatar)) else FAIL,
        f"avatar={'<空>' if not me_avatar else (me_avatar[:60] + ('…已签名' if is_signed(me_avatar) else ' ← 未签名!'))}",
        blocking=bool(me_avatar) and not is_signed(me_avatar))

    # 6.2 登录响应的 avatar（auth.py 独立 _user_view，易漏）
    la = login_user.get("avatar", "")
    rec("E6.2", "登录响应 user.avatar", OK if (not la or is_signed(la)) else FAIL,
        f"avatar={'<空>' if not la else (la[:60] + ('…已签名' if is_signed(la) else ' ← 未签名! 对应 auth.py 遗漏'))}",
        blocking=False)

    # 6.3 商品图
    items, _ = get(c, "/secondhand/items", headers=h)
    its = ((items or {}).get("data") or {}).get("items") or []
    img_stat = SKIP
    img_note = "无二手商品数据，跳过"
    if its:
        urls = []
        for it in its[:5]:
            for u in (it.get("images") or []):
                if isinstance(u, str) and u:
                    urls.append(u)
        if urls:
            bad = [u for u in urls if u.startswith("/static/uploads") and not is_signed(u)]
            img_stat = FAIL if bad else OK
            img_note = f"共 {len(urls)} 个图片 URL，未签名 {len(bad)} 个" + (f" 例：{bad[0][:60]}" if bad else "")
        else:
            img_note = "商品存在但 images 为空"
        rec("E6.3", "二手商品图 URL", img_stat, img_note, blocking=img_stat == FAIL)

    # 6.4 收藏图 / 菜品图
    for label, path in (("收藏", "/favorites"), ("菜品", "/life/merchants/1/menu")):
        body, note = get(c, path, headers=h)
        if body is None:
            rec("E6.4", label, SKIP, note)
            continue
        txt = str(body)
        if "/static/uploads" in txt:
            bad = "e=" not in txt
            rec("E6.4", f"{label}图片", FAIL if bad else OK,
                "含未签名路径!" if bad else "已签名", blocking=bad)
        else:
            rec("E6.4", label, SKIP, "无图片数据")

    # ---------------- E7 前端 BASE_URL ----------------
    section("E7 · 前端配置")
    try:
        txt = FRONTEND_REQUEST_JS.read_text(encoding="utf-8", errors="replace")
        has_local = "127.0.0.1" in txt or "localhost" in txt
        rec("E7.1", "BASE_URL 非 127.0.0.1",
            WARN if has_local else OK,
            "仍写死 127.0.0.1 → 真机封测会全白屏（见方案 §3.2）" if has_local else "已指向局域网/域名")
    except FileNotFoundError:
        rec("E7.1", "前端 request.js", SKIP, f"未找到 {FRONTEND_REQUEST_JS}")

    # ---------------- E8 冒烟 ----------------
    section("E8 · 12 模块冒烟")
    smoke = [
        ("认证", "GET", "/user/me"),
        ("AI 助手", "GET", "/chat/conversations"),
        ("Agent", "GET", "/agent/tasks"),
        ("图书馆", "GET", "/library/free-rooms"),
        ("二手", "GET", "/secondhand/items"),
        ("兼职", "GET", "/jobs"),
        ("论坛", "GET", "/topics"),
        ("地图", "GET", "/map/pois"),
        ("生活", "GET", "/life/merchants"),
        ("收藏", "GET", "/favorites"),
        ("健康详情", "GET", "/health/detail"),
        ("管理端", "GET", "/admin/metrics"),
    ]
    for name, method, path in smoke:
        try:
            r = c.request(method, path, headers=h)
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            code = body.get("code")
            if code is None and path.startswith("/health"):
                # /health* 是特例：直接返回业务数据，无 code 字段
                ok = r.status_code == 200
                code = f"HTTP{r.status_code}"
            else:
                ok = code in (0, 2003)  # 管理端对学生账号返回 2003 属正常
            rec("E8", f"{name} {path}", OK if ok else FAIL,
                f"HTTP {r.status_code} code={code}" + ("（无权限，属正常）" if code == 2003 else ""),
                blocking=(not ok) and code != 2003)
        except Exception as exc:  # noqa: BLE001
            rec("E8", f"{name} {path}", FAIL, f"异常 {exc}", blocking=True)

    # ---------------- E9 已知缺陷复核 ----------------
    section("E9 · 已知缺陷复核（只报告，不计入阻塞）")
    # XFF 绕过
    try:
        codes = []
        for i in range(12):
            r = c.post("/auth/wechat-login", json={"code": "preflight-xff"},
                       headers={"X-Forwarded-For": f"10.0.0.{i}"})
            codes.append(r.status_code)
        got429 = codes.count(429)
        rec("E9.1", "XFF 限流绕过（SEC-17）",
            OK if got429 > 0 else WARN,
            f"伪造 XFF 12 次登录，429 次数={got429}" + ("（仍可绕过，已登记）" if got429 == 0 else ""))
    except Exception as exc:  # noqa: BLE001
        rec("E9.1", "XFF 限流绕过", SKIP, str(exc))

    # ---------------- 汇总 ----------------
    print("\n" + "=" * 78)
    n_ok = sum(1 for r in ROWS if r[2] == OK)
    n_fail = sum(1 for r in ROWS if r[2] == FAIL)
    n_warn = sum(1 for r in ROWS if r[2] == WARN)
    n_skip = sum(1 for r in ROWS if r[2] == SKIP)
    print(f"汇总：{OK} {n_ok} 项通过 ｜ {FAIL} {n_fail} 项失败 ｜ {WARN} {n_warn} 项警告 ｜ {SKIP} {n_skip} 项跳过")
    print(f"阻塞项：{BLOCKING} 个")

    if n_fail:
        print("\n失败项：")
        for code, name, status, note in ROWS:
            if status == FAIL:
                print(f"  {FAIL} {code} {name} — {note}")

    if BLOCKING == 0:
        print("\n✅ 环境自检通过 —— 可以开始封测（下一步：按 A 分册执行使用向测试）")
        return 0
    print("\n❌ 存在阻塞项 —— 请先解决后重新自检（详见《第一阶段验收测试方案》§7）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
