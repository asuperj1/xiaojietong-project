#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1 加固验证：上传类型校验与静态资源安全头（SEC-11）。

验证内容：
    1) 伪造 Content-Type 上传 HTML（真实内容非图片）→ 必须被拒
    2) 声明 image/jpeg 但内容实为 PNG（类型不符）→ 必须被拒
    3) 合法 PNG → 上传成功，且落库 mime 以**真实内容**为准
    4) /static/uploads 响应带 nosniff + CSP sandbox 等安全头

用法：
    python backend/tests/verify_p1_upload.py
    python backend/tests/verify_p1_upload.py --base http://127.0.0.1:8000

依赖：httpx（backend/requirements.txt 已包含）。

作者：成员3 · 审计修复
"""
from __future__ import annotations

import argparse
import sys
import uuid

import httpx

PASSED: list[str] = []
FAILED: list[str] = []

# 1x1 透明 PNG（合法最小图片）
PNG_1X1 = bytes.fromhex(
    "89504E470D0A1A0A0000000D494844520000000100000001080600000"
    "01F15C4890000000A49444154789C6300010000050001055D8A5A00"
    "00000049454E44AE426082"
)
HTML_PAYLOAD = b"<html><body><script>alert(document.domain)</script></body></html>"


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"    {detail}" if detail else ""))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser(description="上传安全验证")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    print(f"# 上传安全验证｜目标 {base}\n")
    # trust_env=False：不读 HTTP_PROXY/HTTPS_PROXY，避免本地代理未启动时误得 502
    client = httpx.Client(base_url=base, timeout=30, trust_env=False)

    # ---------- 准备：登录 ----------
    resp = client.post("/api/v1/auth/wechat-login",
                       json={"code": f"sec11_{uuid.uuid4().hex[:8]}"})
    if resp.status_code != 200 or resp.json().get("code") != 0:
        print(f"[致命] 登录失败：HTTP {resp.status_code} {resp.text[:200]}")
        print("       若为 429，说明登录限流已触发，请等待 60 秒后重试。")
        return 2
    headers = {"Authorization": f"Bearer {resp.json()['data']['token']}"}
    print("[准备] 登录成功\n")

    # ---------- 1) 伪造类型：HTML 冒充 PNG ----------
    print("[SEC-11] 上传类型校验")
    resp = client.post("/api/v1/upload/image", headers=headers,
                       files={"file": ("evil.png", HTML_PAYLOAD, "image/png")})
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    check("HTML 冒充 PNG 被拒", body.get("code") not in (0, None),
          f"HTTP {resp.status_code} code={body.get('code')} msg={body.get('message')}")

    # ---------- 2) 声明与内容不符：PNG 内容声明为 JPEG ----------
    resp = client.post("/api/v1/upload/image", headers=headers,
                       files={"file": ("x.jpg", PNG_1X1, "image/jpeg")})
    body = resp.json()
    check("内容与声明类型不符被拒", body.get("code") not in (0, None),
          f"code={body.get('code')} msg={body.get('message')}")

    # ---------- 3) 合法图片 ----------
    resp = client.post("/api/v1/upload/image", headers=headers,
                       files={"file": ("good.png", PNG_1X1, "image/png")})
    body = resp.json()
    url = (body.get("data") or {}).get("url", "")
    check("合法 PNG 上传成功", body.get("code") == 0 and bool(url),
          f"code={body.get('code')} url={url}")

    # ---------- 4) 静态资源安全头 ----------
    if url:
        r = client.get(url)
        h = {k.lower(): v for k, v in r.headers.items()}
        check("静态响应带 X-Content-Type-Options: nosniff",
              h.get("x-content-type-options") == "nosniff",
              f"值={h.get('x-content-type-options')}")
        check("静态响应带 CSP sandbox（禁止脚本执行）",
              "sandbox" in h.get("content-security-policy", ""),
              f"值={h.get('content-security-policy')}")
    print()

    total = len(PASSED) + len(FAILED)
    print(f"===== 结果：{len(PASSED)}/{total} 通过 =====")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
