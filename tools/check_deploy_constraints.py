#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内部联调部署方案的「约束合规」静态检查。

用途：把产品方提出的**硬性禁令**变成可重复执行的校验，防止后续被人误改回去
（例如某次调试图方便把端口改成 80、或加了个 nginx 配置）。

检查对象：``deploy/internal-test/``（脚本 + systemd + 文档）与访问闸门代码。

运行
----
    python -X utf8 tools/check_deploy_constraints.py
    python -X utf8 tools/check_deploy_constraints.py --dir deploy/internal-test

退出码：0 = 全部合规；1 = 存在违规项（**可接进 CI**）。
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- 违禁模式
# 只匹配「会被真正执行/生效」的配置形态，避免误伤文档里的“禁止 80/443”等说明文字。
FORBIDDEN: list[tuple[str, str, str]] = [
    # (名称, 正则, 说明)
    ("监听 80", r"(?m)^\s*listen\s+80\b|--port\s+80\b|^\s*EXPOSE\s+80\b|-p\s*80:80", "禁止监听 80 端口"),
    ("监听 443", r"(?m)^\s*listen\s+443\b|--port\s+443\b|^\s*EXPOSE\s+443\b|-p\s*443:443", "禁止监听 443 端口"),
    ("Nginx 虚拟主机", r"(?m)^\s*server_name\s+\S+\.(com|cn|net|org)\b", "禁止绑定域名"),
    ("SSL 证书配置", r"(?m)^\s*ssl_certificate\s", "禁止 SSL/HTTPS 证书配置"),
    ("证书工具", r"\b(certbot|acme\.sh|acme-tiny)\b", "禁止申请证书（属对外服务准备）"),
    ("内网穿透", r"\b(frp[cs]?|ngrok|cloudflared|localtunnel|autossh)\b", "禁止隧道/穿透（属规避备案）"),
    ("SSH 反向隧道", r"ssh\s+-R\s", "禁止 SSH 反向隧道"),
    ("业务占用 22", r"--port\s+22\b", "禁止业务占用 SSH 端口"),
    ("业务占用 3306", r"--port\s+3306\b", "禁止业务占用 MySQL 端口"),
    ("业务占用 6379", r"--port\s+6379\b", "禁止业务占用 Redis 端口"),
    ("域名 A 记录", r"\bA\s*记录\b|add-record|dns.*a-record", "禁止域名解析配置"),
]

# ---------------------------------------------------------------- 必须存在
REQUIRED: list[tuple[str, pathlib.Path, str]] = [
    ("端口策略守卫", pathlib.Path("deploy/internal-test/scripts/lib/common.sh"), "assert_port_policy"),
    ("8080 默认端口", pathlib.Path("deploy/internal-test/scripts/lib/common.sh"), "APP_PORT:-8080"),
    ("健康检查路径", pathlib.Path("deploy/internal-test/scripts/lib/common.sh"), "/api/v1/health"),
    ("systemd 单元", pathlib.Path("deploy/internal-test/systemd/xiaojietong-api.service"), "--port __APP_PORT__"),
    ("一键部署脚本", pathlib.Path("deploy/internal-test/scripts/08-deploy.sh"), "01-pull-code.sh"),
    ("一键部署编译步骤", pathlib.Path("deploy/internal-test/scripts/08-deploy.sh"), "02-build-cpp.sh"),
    ("防火墙拒绝 80", pathlib.Path("deploy/internal-test/scripts/07-firewall.sh"), "deny 80/tcp"),
    ("防火墙拒绝 443", pathlib.Path("deploy/internal-test/scripts/07-firewall.sh"), "deny 443/tcp"),
    ("安全组提醒", pathlib.Path("deploy/internal-test/scripts/lib/common.sh"), "安全组"),
    ("访问令牌配置", pathlib.Path("backend/app/core/access_gate.py"), "XJT_ACCESS_TOKEN"),
    ("精确豁免匹配", pathlib.Path("backend/app/core/access_gate.py"), "默认精确匹配"),
    ("备案风险文档", pathlib.Path("deploy/internal-test/docs/04-ICP备案风险提示.md"), "备案"),
    ("安全组拒 80/443", pathlib.Path("deploy/internal-test/docs/02-部署操作手册.md"), "拒绝"),
    ("运维验收清单", pathlib.Path("deploy/internal-test/docs/05-运维排障与验收清单.md"), "4.1 合规"),
    # —— 文档侧红线正向断言（文档不参与违禁模式扫描，改由此处断言“确实写了禁止”）——
    ("README 红线清单", pathlib.Path("deploy/internal-test/README.md"), "红线"),
    ("README 禁 80/443", pathlib.Path("deploy/internal-test/README.md"), "不得监听"),
    ("风险文档禁隧道", pathlib.Path("deploy/internal-test/docs/04-ICP备案风险提示.md"), "内网穿透"),
    ("风险文档 12 条禁止清单", pathlib.Path("deploy/internal-test/docs/04-ICP备案风险提示.md"), "禁止事项清单"),
    ("操作手册安全组提醒", pathlib.Path("deploy/internal-test/docs/02-部署操作手册.md"), "安全组"),
    ("鉴权说明了精确匹配", pathlib.Path("deploy/internal-test/docs/03-访问鉴权说明.md"), "精确匹配"),
]

SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".so", ".pyd"}

# ⚠️ 违禁模式**只扫可执行 / 配置文件**，不扫文档。
# 原因：合规文档（04 风险提示 / 05 验收清单 / README 红线清单）**必然**会列举
# 「禁止使用 frp」「不得申请证书」等字样，对文档做正则扫描只会产生大量误报。
# 文档侧改由下方 REQUIRED 清单做**正向断言**（必须包含备案提示、拒绝 80/443 等）。
SCAN_SUFFIX = {".sh", ".service", ".conf", ".cfg", ".env", ".ini", ".yml", ".yaml", ".json", ".py", ".ps1"}


def iter_files(base: pathlib.Path, *, code_only: bool = False):
    for p in sorted(base.rglob("*")):
        if not p.is_file() or p.suffix.lower() in SKIP_SUFFIX:
            continue
        if code_only and p.suffix.lower() not in SCAN_SUFFIX and p.name != ".env":
            continue
        yield p


def main() -> int:
    ap = argparse.ArgumentParser(description="部署方案约束合规检查")
    ap.add_argument("--dir", default="deploy/internal-test", help="待检查目录")
    args = ap.parse_args()

    base = (ROOT / args.dir).resolve()
    if not base.is_dir():
        print(f"❌ 目录不存在：{base}")
        return 1

    violations: list[str] = []
    print("=" * 74)
    print(f"部署约束合规检查 · {base.relative_to(ROOT)}")
    print("=" * 74)

    print("\n① 违禁模式扫描（只扫可执行/配置文件，避免误伤合规文档）")
    files = list(iter_files(base))
    code_files = list(iter_files(base, code_only=True))
    print(f"     扫描范围：{len(code_files)} 个可执行/配置文件")
    for name, pattern, why in FORBIDDEN:
        rx = re.compile(pattern)
        hits = []
        for f in code_files:
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                # 行内含「拒绝/禁止」字样的是**拦截规则**（如 ufw deny 80/tcp），属正确做法
                if re.search(r"(?i)deny|reject|拒绝|禁止|不得", line):
                    continue
                if rx.search(line):
                    hits.append(f"{f.relative_to(ROOT)}:{line_no}  {line.strip()[:100]}")
        if hits:
            print(f"  [FAIL] {name} —— {why}")
            for h in hits[:5]:
                print(f"         {h}")
            violations.append(f"{name}: {len(hits)} 处")
        else:
            print(f"  [PASS] 未发现 {name}")

    print("\n② 必备要素核对")
    for label, rel, needle in REQUIRED:
        p = ROOT / rel
        ok = p.is_file() and needle in p.read_text(encoding="utf-8", errors="ignore")
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}  —— {rel}")
        if not ok:
            violations.append(f"缺少要素：{label}（{rel}）")

    print("\n③ 端口纪律抽查（脚本内所有 --port 取值）")
    port_ok = True
    for f in files:
        if f.suffix != ".sh":
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"--port\s+([0-9]{2,5})", text):
            val = m.group(1)
            if val != "8080" and not f.name == "common.sh":
                print(f"  [WARN] {f.relative_to(ROOT)} 出现 --port {val}")
                port_ok = False
    print(f"  [{'PASS' if port_ok else 'WARN'}] 脚本中的 --port 均为 8080（或使用 $APP_PORT 变量）")

    print("\n" + "=" * 74)
    if violations:
        print(f"结果：❌ 存在 {len(violations)} 项问题")
        for v in violations:
            print(f"  - {v}")
        print("\n⚠️ 请对照 deploy/internal-test/docs/04-ICP备案风险提示.md 整改。")
        return 1
    print("结果：✅ 全部合规（无 80/443、无域名、无证书、无隧道；8080 + 访问闸门齐备）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
