#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查开放中的 Pull Request 的目标分支（base）与规模。

**为什么需要它**：GitHub 的「Create a pull request」提示链接会**默认把 base 设为仓库的
默认分支（多为 `main`）**，团队规范要求一律 **→ `dev`**（见 `docs/团队Git合作协议.md`）。
本脚本一次性列出所有 open PR 的 `base <- head`，把误指向 `main` 的**高亮告警**。

用法
----
    E:/miniconda3/python.exe -X utf8 tools/check_prs.py
    E:/miniconda3/python.exe -X utf8 tools/check_prs.py --repo asuperj1/xiaojietong-project
    E:/miniconda3/python.exe -X utf8 tools/check_prs.py --expect dev      # 期望的 base（默认 dev）

说明：使用 GitHub 公共 REST API（公共仓库无需 token；如需更高频率可 `--token <PAT>`）。
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict

import httpx

WARN = "⚠️"
OK = "✅"


def fetch(repo: str, token: str | None, proxy: str | None) -> list[dict]:
    url = f"https://api.github.com/repos/{repo}/pulls?state=open&per_page=50"
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    kw: dict = {"timeout": 60, "trust_env": False}
    if proxy:
        kw["proxy"] = proxy
    try:
        r = httpx.get(url, headers=headers, **kw)
    except Exception:
        # 回退：交给环境代理
        r = httpx.get(url, headers=headers, timeout=60)
    if r.status_code != 200:
        print(f"{WARN} GitHub API 返回 HTTP {r.status_code}：{r.text[:200]}")
        return []
    return r.json()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="asuperj1/xiaojietong-project")
    ap.add_argument("--expect", default="dev", help="期望的目标分支（默认 dev）")
    ap.add_argument("--token", default=None, help="可选 GitHub PAT")
    ap.add_argument("--proxy", default="http://127.0.0.1:7897", help='代理；传 "" 表示不走代理')
    args = ap.parse_args()

    print("=" * 108)
    print(f"开放中的 Pull Request · {args.repo}")
    print("=" * 108)

    prs = fetch(args.repo, args.token, args.proxy or None)
    if not prs:
        print("（没有 open PR，或 API 调用失败）")
        return 1

    hdr = f"{'PR':<6}{'base <- head':<54}{'文件':>5}{'+/−':>11}  标题"
    print(hdr)
    print("-" * 108)

    wrong: list[int] = []
    for p in sorted(prs, key=lambda x: x["number"]):
        base = p["base"]["ref"]
        head = p["head"]["ref"]
        num = f"#{p['number']}"
        flag = "" if base == args.expect else f"  {WARN} base 应为 {args.expect}！"
        if base != args.expect:
            wrong.append(p["number"])
        print(f"{num:<6}{base + ' <- ' + head:<54}{p['changed_files']:>5}"
              f"{'+' + str(p['additions']) + '/-' + str(p['deletions']):>11}  {p['title'][:48]}{flag}")

    print()
    print("=== 按 base 分组 ===")
    g: dict[str, list[str]] = defaultdict(list)
    for p in prs:
        g[p["base"]["ref"]].append(f"#{p['number']}")
    for k in sorted(g):
        mark = OK if k == args.expect else WARN
        print(f"  {mark} base={k}: {', '.join(sorted(g[k], key=lambda s: int(s[1:])))}")

    print()
    if wrong:
        print(f"{WARN} {len(wrong)} 个 PR 的目标分支不是 `{args.expect}`："
              f"{', '.join('#' + str(n) for n in wrong)}")
        print("   处理：在 PR 页面点标题右侧的「Edit」→ 把 base 改为 `dev`（GitHub 支持改 base）。")
        print("   预防：新建 PR 时用带 base 的链接 ——")
        print(f"     https://github.com/{args.repo}/compare/{args.expect}...<你的分支>?expand=1")
    else:
        print(f"{OK} 全部 {len(prs)} 个 PR 的目标分支均为 `{args.expect}` ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
