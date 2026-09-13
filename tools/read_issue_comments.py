#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析 MCP 返回的议题 / PR 详情 JSON，按需提取评论。

**为什么需要它**：`issues_get_detail` 等 MCP 调用在议题评论多时会把结果落盘成
单行 JSON（可达 20KB+），直接读会挤爆上下文、且无法按行截取。本脚本把
「评论列表摘要」与「最新 N 条正文」分开输出，便于按需取用。

用法
----
    python tools/read_issue_comments.py <content.json>                 # 摘要 + 最新 1 条
    python tools/read_issue_comments.py <content.json> --last 2        # 最新 2 条
    python tools/read_issue_comments.py <content.json> --index 3       # 只看第 3 条
    python tools/read_issue_comments.py <content.json> --list          # 只列摘要
    python tools/read_issue_comments.py <content.json> --chars 3000    # 每条截断长度
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="MCP 落盘的 content.json 路径")
    ap.add_argument("--last", type=int, default=1, help="输出最新 N 条正文（默认 1）")
    ap.add_argument("--index", type=int, default=None, help="只输出第 N 条（1-based）")
    ap.add_argument("--list", action="store_true", help="只列摘要")
    ap.add_argument("--chars", type=int, default=2500, help="每条正文截断字符数")
    args = ap.parse_args()

    p = pathlib.Path(args.path)
    if not p.exists():
        print(f"❌ 文件不存在：{p}")
        return 1

    data = json.loads(p.read_text(encoding="utf-8"))
    d = data.get("data", data)
    comments = d.get("comments") or []

    print("=" * 76)
    print(f"议题/PR #{d.get('number')} · {d.get('title')}")
    print(f"状态：{d.get('status')}  评论数：{len(comments)}")
    print("=" * 76)

    if not comments:
        print("（暂无评论）")
        return 0

    print("\n评论摘要：")
    for i, c in enumerate(comments, 1):
        body = c.get("body") or ""
        print(f"  [{i:>2}] {c.get('author'):<14} {c.get('created_at')}  {len(body):>6} 字符  "
              f"{body.splitlines()[0][:48] if body.strip() else ''}")

    if args.list:
        return 0

    # 选定要展开的条目
    if args.index:
        if not 1 <= args.index <= len(comments):
            print(f"\n❌ --index 超出范围（1~{len(comments)}）")
            return 1
        chosen = [comments[args.index - 1]]
    else:
        chosen = comments[-max(1, args.last):]

    for c in chosen:
        body = c.get("body") or ""
        print("\n" + "=" * 76)
        print(f"作者：{c.get('author')}  @ {c.get('created_at')}")
        print("=" * 76)
        if len(body) > args.chars:
            print(body[: args.chars])
            print(f"\n…（已截断，共 {len(body)} 字符；用 --chars 调整）")
        else:
            print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
