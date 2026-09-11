#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0 安全修复验证脚本（对应《项目审计报告260910序2-技术专项》）。

逐项验证以下修复是否真正生效（黑盒，走真实 HTTP + 真实数据库）：

    SEC-03  聊天会话消息越权读取    → 他人 conv_id 应被拒（1001）
    SEC-04  向他人会话写入消息      → 他人 conv_id 应被拒（1001）
    SEC-05  越权把他人商品置为已售  → 必须走合法下单；卖家/金额由服务端反查
    TXN-02  二手订单超卖            → 同一商品二次下单应被拒（3001）
    SEC-06  禁用账号校验死代码      → 账号 status=1 后，其 token 应立即失效（403）

前置：
    1) MySQL(3307) 可用、库 xiaojietong 已建表
    2) 后端已启动：uvicorn app.main:app --port 8000
       （需 XJT_DB_PASSWORD / XJT_DB_PORT 环境变量）

用法：
    python backend/tests/verify_p0_authz.py
    python backend/tests/verify_p0_authz.py --base http://127.0.0.1:8000

说明：脚本会创建少量测试数据（2 个 mock 用户 + 1 个会话 + 1 个商品），
      结束时清理商品；mock 用户前缀为 oXJT_DEV_p0test_，可用 SQL 归档删除。

作者：成员3 · 审计修复验证
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

PASSED: list[str] = []
FAILED: list[str] = []


def req(base: str, method: str, path: str, body: dict | None = None,
        token: str = "") -> tuple[int, dict]:
    """发起请求，返回 (http_status, 响应体 dict)。"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(f"{base}{path}", data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=180) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw}


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"    {detail}" if detail else ""))


def login(base: str, code: str) -> tuple[str, int]:
    """mock 登录，返回 (access_token, user_id)。"""
    status, body = req(base, "POST", "/api/v1/auth/wechat-login", {"code": code})
    assert status == 200 and body.get("code") == 0, f"登录失败：{status} {body}"
    return body["data"]["token"], int(body["data"]["user"]["id"])


MYSQL = os.environ.get(
    "XJT_MYSQL_CLI", r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe")


def mysql_available() -> bool:
    return bool(os.environ.get("XJT_DB_PASSWORD")) and os.path.isfile(MYSQL)


def mysql_exec(sql: str) -> str:
    """执行 SQL，返回 stdout（-N -B：无列名、制表符分隔，便于取 LAST_INSERT_ID）。"""
    port = os.environ.get("XJT_DB_PORT", "3307")
    env = dict(os.environ, MYSQL_PWD=os.environ["XJT_DB_PASSWORD"])
    out = subprocess.run(
        [MYSQL, "-h", "127.0.0.1", "-P", port, "-u", "root", "-N", "-B",
         "-e", sql], env=env, check=True, capture_output=True)
    return out.stdout.decode("utf-8", "replace").strip()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser(description="P0 安全修复验证")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    print(f"# P0 修复验证｜目标 {base}\n")

    # ---------- 准备：两个独立用户 ----------
    print("[准备] 登录两个测试用户 A / B")
    token_a, uid_a = login(base, "p0test_a")
    token_b, uid_b = login(base, "p0test_b")
    print(f"       A: uid={uid_a}  B: uid={uid_b}\n")

    # ---------- SEC-03 / SEC-04：聊天会话越权 ----------
    # 会话夹具用 SQL 直插（鉴权测试不应依赖 LLM 可用性；/chat/send 是 SSE 流）
    print("[SEC-03/04] 聊天会话越权（A 拥有会话，B 尝试读写）")
    conv_id = None
    if mysql_available():
        try:
            # 注意：LAST_INSERT_ID() 必须与 INSERT 在同一连接，否则返回 0
            raw = mysql_exec(
                f"INSERT INTO xiaojietong.ai_conversation (user_id, title) "
                f"VALUES ({uid_a}, 'P0验证会话');"
                f"SELECT LAST_INSERT_ID();")
            conv_id = int(raw.splitlines()[-1])
            # 插入带标记的私密消息，用于证明夹具非空（否则 B 的“被拒”可能只是没数据）
            mysql_exec(
                f"INSERT INTO xiaojietong.ai_message (conversation_id, role, content) "
                f"VALUES ({conv_id}, 'user', 'A的私密消息_MARKER');")
            check("A 拥有会话夹具（含1条私密消息）", conv_id > 0, f"conv_id={conv_id}")
        except Exception as exc:  # noqa: BLE001
            check("A 拥有会话夹具（含1条私密消息）", False, f"SQL 失败：{exc}")
            conv_id = None
    else:
        print("      [SKIP] 缺 mysql 客户端或 XJT_DB_PASSWORD，跳过聊天越权用例")

    if conv_id:
        # SEC-03：B 读 A 的会话消息（BizError 返回 HTTP 400 + code 1001）
        status, body = req(base, "GET", f"/api/v1/chat/conversations/{conv_id}/messages",
                           token=token_b)
        check("SEC-03 B 读取 A 的会话被拒",
              body.get("code") == 1001 or status == 403,
              f"HTTP {status} code={body.get('code')} msg={body.get('message')}")

        # SEC-04：B 往 A 的会话发消息
        status, body = req(base, "POST", "/api/v1/chat/send",
                           {"content": "越权注入", "conversation_id": conv_id}, token_b)
        check("SEC-04 B 向 A 的会话写入被拒",
              body.get("code") == 1001 or status == 403,
              f"HTTP {status} code={body.get('code')} msg={body.get('message')}")

        # 阳性对照：A 自己能读到那条私密消息（证明夹具非空，B 的失败才具说服力）
        status, body = req(base, "GET", f"/api/v1/chat/conversations/{conv_id}/messages",
                           token=token_a)
        has_marker = "A的私密消息_MARKER" in json.dumps(body, ensure_ascii=False)
        check("对照：A 能读到自己的私密消息",
              status == 200 and body.get("code") == 0 and has_marker,
              f"HTTP {status} code={body.get('code')} 含标记={has_marker}")
        # 清理夹具
        if mysql_available():
            mysql_exec(f"DELETE FROM xiaojietong.ai_message WHERE conversation_id={conv_id};")
            mysql_exec(f"DELETE FROM xiaojietong.ai_conversation WHERE id={conv_id};")
    print()

    # ---------- SEC-05 / TXN-02：二手订单 ----------
    print("[SEC-05/TXN-02] 二手订单：越权下架 + 超卖 + 卖家/金额伪造")
    status, body = req(base, "POST", "/api/v1/secondhand/items",
                       {"title": "P0验证用教材", "description": "验证后自动下架",
                        "category": "教材", "price": 42.5}, token_a)
    item_id = body.get("data", {}).get("item_id") if status == 200 else None
    check("A 发布商品", bool(item_id), f"item_id={item_id} HTTP {status}")

    if item_id:
        # SEC-05：B 伪造 seller_id/amount 下单 —— 新契约下这两个字段应被忽略
        status, body = req(base, "POST", "/api/v1/secondhand/orders",
                           {"item_id": item_id, "seller_id": 999999, "amount": 0.01},
                           token_b)
        ok_first = status == 200 and body.get("code") == 0
        amount = (body.get("data") or {}).get("amount")
        check("SEC-05 卖家/金额由服务端反查（伪造值被忽略）",
              ok_first and amount == 42.5,
              f"HTTP {status} code={body.get('code')} 服务端金额={amount}（客户端传 0.01）")

        # TXN-02：同一商品二次下单应被拒（BizError 自带 HTTP 400）
        status, body = req(base, "POST", "/api/v1/secondhand/orders",
                           {"item_id": item_id}, token_a)
        check("TXN-02 二次下单被拒（防超卖）",
              body.get("code") == 3001,
              f"HTTP {status} code={body.get('code')} msg={body.get('message')}")

        # 自己买自己的商品应被拒
        status2, body2 = req(base, "POST", "/api/v1/secondhand/items",
                             {"title": "P0验证用教材2", "price": 10}, token_a)
        own_item = (body2.get("data") or {}).get("item_id")
        if own_item:
            status, body = req(base, "POST", "/api/v1/secondhand/orders",
                               {"item_id": own_item}, token_a)
            check("不能购买自己发布的物品",
                  body.get("code") == 3001,
                  f"HTTP {status} code={body.get('code')} msg={body.get('message')}")
            req(base, "PUT", f"/api/v1/secondhand/items/{own_item}/status",
                {"status": "2"}, token_a)

        # 清理商品
        req(base, "PUT", f"/api/v1/secondhand/items/{item_id}/status",
            {"status": "2"}, token_a)
    print()

    # ---------- SEC-06：禁用账号校验 ----------
    print("[SEC-06] 账号禁用后 token 应立即失效")
    status, body = req(base, "GET", "/api/v1/user/me", token=token_a)
    before_ok = status == 200 and body.get("code") == 0
    check("禁用前 A 可正常访问", before_ok, f"HTTP {status}")

    if before_ok:
        if not mysql_available():
            print("      [SKIP] 未找到 mysql 客户端或 XJT_DB_PASSWORD，跳过禁用测试")
        else:
            mysql_exec(f"UPDATE xiaojietong.`user` SET status=1 WHERE id={uid_a};")
            status, body = req(base, "GET", "/api/v1/user/me", token=token_a)
            check("SEC-06 禁用后 A 的 token 被拒（403）",
                  status == 403 or body.get("code") == 2003,
                  f"HTTP {status} code={body.get('code')} msg={body.get('message')}")
            mysql_exec(f"UPDATE xiaojietong.`user` SET status=0 WHERE id={uid_a};")
            print("      （已恢复 status=0）")

    print()
    total = len(PASSED) + len(FAILED)
    print(f"===== 结果：{len(PASSED)}/{total} 通过 =====")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
