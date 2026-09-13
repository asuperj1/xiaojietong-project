#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""访问闸门（简易接口鉴权）验证工具。

用途：部署到公网 IP 后，确认「外网陌生人访问测试接口」确实被挡住，
并且**没有误伤**健康检查与带令牌的正常调用。

对应实现：``backend/app/core/access_gate.py``
配置项：  ``XJT_ACCESS_TOKEN`` / ``XJT_ACCESS_GATE_EXEMPT``

运行
----
    # 本机自测（默认 http://127.0.0.1:8080）
    python -X utf8 tools/verify_access_gate.py --token <你的令牌>

    # 针对服务器验证（在服务器本机执行，或从团队机器执行）
    python -X utf8 tools/verify_access_gate.py --base http://122.51.248.93:8080 --token <令牌>

    # 关闭态自测（确认闸门为空时不拦截，开发者本机零回归）
    python -X utf8 tools/verify_access_gate.py --token ""

退出码：0 = 全部符合预期；1 = 有不符合项。
"""

from __future__ import annotations

import argparse
import json
import sys

try:
    import httpx
except ImportError:  # pragma: no cover
    print("缺少 httpx。请使用装了 httpx 的解释器，例如 E:/miniconda3/python.exe")
    raise SystemExit(2)

_results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    _results.append((ok, name, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  —— {detail}" if detail else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description="访问闸门验证")
    ap.add_argument("--base", default="http://127.0.0.1:8080", help="后端基地址（不含 /api/v1）")
    ap.add_argument("--token", default=None, help="访问令牌；传空串验证关闭态")
    ap.add_argument("--exempt", default="/api/v1/health", help="闸门豁免路径（逗号分隔）")
    args = ap.parse_args()

    base = args.base.rstrip("/")
    token = args.token
    exempt = [p.strip() for p in args.exempt.split(",") if p.strip()]
    # trust_env=False：系统代理会把本地请求打成 502（见审计 SEC-23）
    cli = httpx.Client(base_url=base, timeout=15.0, trust_env=False, follow_redirects=False)

    print("=" * 74)
    print(f"访问闸门验证 · {base} · 令牌{'已设置' if token else '为空（关闭态）'}")
    print("=" * 74)

    if token:
        # ---------------- 启用态 ----------------
        print("\n① 豁免路径：无令牌也必须放行（供探活/监控）")
        for p in exempt:
            r = cli.get(p)
            check(r.status_code == 200, f"{p} 无令牌可访问", f"HTTP {r.status_code}")

        print("\n② 敏感探针：即使无令牌也必须拦住（信息泄露防护）")
        for p in ["/api/v1/health/detail", "/api/v1/health/selfcheck"]:
            r = cli.get(p)
            check(
                r.status_code == 403,
                f"{p} 匿名被拒（会回显 DB/连接池状态）",
                f"HTTP {r.status_code}",
            )

        print("\n③ 业务接口：匿名必须被拦截（外网陌生人场景）")
        for p in ["/api/v1/secondhand/items", "/api/v1/user/me", "/api/v1/topics"]:
            r = cli.get(p)
            ok = r.status_code == 403
            body = {}
            try:
                body = r.json()
            except Exception:
                pass
            check(ok, f"{p} 匿名被拒", f"HTTP {r.status_code} code={body.get('code')}")

        print("\n④ 响应契约：403 必须是 {code,message,data}")
        r = cli.get("/api/v1/secondhand/items")
        try:
            body = r.json()
        except Exception:
            body = {}
        check(
            set(body.keys()) >= {"code", "message", "data"} and body.get("code") == 2003,
            "403 契约符合统一格式（code=2003）",
            json.dumps(body, ensure_ascii=False)[:90],
        )

        print("\n⑤ 错误令牌：同样必须被拦截")
        r = cli.get("/api/v1/secondhand/items", headers={"X-Access-Token": "definitely-wrong"})
        check(r.status_code == 403, "错误令牌被拒", f"HTTP {r.status_code}")

        print("\n⑥ 正确令牌：闸门放行（下游业务鉴权再返回 2001）")
        h = {"X-Access-Token": token}
        r = cli.get("/api/v1/secondhand/items", headers=h)
        try:
            code = r.json().get("code")
        except Exception:
            code = None
        check(
            r.status_code != 403 and code == 2001,
            "正确令牌通过闸门（下游返回 2001 未登录）",
            f"HTTP {r.status_code} code={code}",
        )

        print("\n⑦ 查询串方式（浏览器临时调试用）")
        r = cli.get(f"/api/v1/secondhand/items?access_token={token}")
        try:
            code = r.json().get("code")
        except Exception:
            code = None
        check(r.status_code != 403 and code == 2001, "?access_token= 生效", f"code={code}")

        print("\n⑧ 静态上传目录：匿名必须被拦截")
        r = cli.get("/static/uploads/")
        check(r.status_code == 403, "/static/uploads/ 匿名被拒", f"HTTP {r.status_code}")

        print("\n⑨ 令牌强度自检（防弱口令）")
        weak = token.lower() in {"test", "123456", "token", "admin", "password"} or len(token) < 16
        check(not weak, "令牌长度 ≥16 且非常见弱口令", f"长度={len(token)}")
    else:
        # ---------------- 关闭态 ----------------
        print("\n① 关闭态：不设置令牌时不应拦截（本机开发零回归）")
        r = cli.get("/api/v1/secondhand/items")
        try:
            code = r.json().get("code")
        except Exception:
            code = None
        check(
            r.status_code != 403,
            "业务接口未被闸门拦截",
            f"HTTP {r.status_code} code={code}（应为 2001 未登录）",
        )
        r = cli.get("/api/v1/health")
        check(r.status_code == 200, "/api/v1/health 正常", f"HTTP {r.status_code}")

    passed = sum(1 for ok, _, _ in _results if ok)
    total = len(_results)
    print("\n" + "=" * 74)
    print(f"结果：{passed}/{total} 通过")
    print("=" * 74)
    if passed != total:
        for ok, name, detail in _results:
            if not ok:
                print(f"  ❌ {name}" + (f"  —— {detail}" if detail else ""))
        if token:
            print("\n排查建议：")
            print("  1) 确认服务启动日志出现「访问闸门已启用」；")
            print("  2) 确认 XJT_ACCESS_TOKEN 已通过 systemd EnvironmentFile 注入；")
            print("  3) 改配置后需 systemctl restart xiaojietong-api。")
        return 1
    print("\n✅ 访问闸门行为符合预期。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
