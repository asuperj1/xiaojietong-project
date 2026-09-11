#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1 加固验证：接口限流（SEC-10）与 CORS 收紧（SEC-13）。

验证内容：
    1) POST /auth/wechat-login 达到阈值后返回 429 + 业务码 1010 + Retry-After
    2) 429 不会误伤阈值内的正常请求（前 N 次必须成功）
    3) CORS 响应头不再出现 `access-control-allow-credentials: true`
       （SEC-13 的核心风险点：通配来源 + 携带凭据 = 对全网站点开放）

用法：
    python backend/tests/verify_p1_ratelimit.py
    python backend/tests/verify_p1_ratelimit.py --base http://127.0.0.1:8000 --limit 10

⚠️ 本脚本会**耗尽当前 IP 的登录配额**（默认 10 次/分钟）。跑完 60 秒内再执行
   其它需要登录的用例，可能收到 429。需要连续压测时请临时放宽：

       $env:XJT_RATE_LIMIT_LOGIN_PER_MINUTE='1000'   # 改完需重启后端

作者：成员3 · 审计修复
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"    {detail}" if detail else ""))


def post_login(base: str, code: str) -> tuple[int, dict, dict]:
    """发起一次 mock 登录，返回 (状态码, 响应体, 响应头)。"""
    req = urllib.request.Request(
        f"{base}/api/v1/auth/wechat-login",
        data=json.dumps({"code": code}).encode("utf-8"),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8")), dict(resp.headers)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw), dict(e.headers)
        except json.JSONDecodeError:
            return e.code, {"raw": raw}, dict(e.headers)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser(description="限流 / CORS 验证")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--limit", type=int, default=10,
                    help="XJT_RATE_LIMIT_LOGIN_PER_MINUTE 的期望值")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    print(f"# P1 加固验证｜目标 {base}｜登录阈值 {args.limit} 次/分钟\n")

    # ---------- 1) 限流 ----------
    print(f"[SEC-10] 连续请求登录接口 {args.limit + 3} 次（阈值 {args.limit}）")
    ok_count = 0
    first_429_at = None
    retry_after = ""
    for i in range(1, args.limit + 4):
        status, body, headers = post_login(base, f"rl_probe_{i}")
        if status == 200 and body.get("code") == 0:
            ok_count += 1
        elif status == 429:
            if first_429_at is None:
                first_429_at = i
                retry_after = headers.get("retry-after", "")
    print(f"       成功 {ok_count} 次，首次 429 出现在第 {first_429_at} 次")

    check("阈值内请求全部成功", ok_count == args.limit,
          f"期望 {args.limit} 次，实际 {ok_count} 次")
    check("超阈值请求被拦截（429）", first_429_at == args.limit + 1,
          f"首次 429 在第 {first_429_at} 次（期望第 {args.limit + 1} 次）")

    status, body, headers = post_login(base, "rl_probe_last")
    check("超限响应体为统一业务码 1010",
          status == 429 and body.get("code") == 1010,
          f"HTTP {status} code={body.get('code')} msg={body.get('message')}")
    check("超限响应带 Retry-After 头", bool(retry_after or headers.get("retry-after")),
          f"Retry-After={retry_after or headers.get('retry-after')}")
    print()

    # ---------- 2) CORS ----------
    print("[SEC-13] 检查 CORS 响应头（伪装第三方站点发起请求）")
    req = urllib.request.Request(f"{base}/api/v1/health")
    req.add_header("Origin", "https://evil.example")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            h = {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as e:
        h = {k.lower(): v for k, v in e.headers.items()}

    allow_origin = h.get("access-control-allow-origin", "(无)")
    allow_cred = h.get("access-control-allow-credentials", "(无)")
    check("响应头不含 allow-credentials: true（杜绝跨站携带凭据）",
          allow_cred.lower() != "true",
          f"access-control-allow-credentials={allow_cred}")
    print(f"       access-control-allow-origin={allow_origin}（开发态允许 * 属预期）")
    print()

    total = len(PASSED) + len(FAILED)
    print(f"===== 结果：{len(PASSED)}/{total} 通过 =====")
    for name in FAILED:
        print(f"  FAILED: {name}")
    if first_429_at:
        print("\n提示：本 IP 的登录配额已耗尽，约 60 秒后自动恢复。")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
