#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C42 硬样本集的**自检**：把每条题目的「为什么它属于这一类」对着真实知识库核验一遍。

背景：C14 的负样本只有 2 条、且没有任何机器校验 —— 「库内无答案」这种断言
一旦知识库更新就会悄悄失效（题目变成可答，但报告里还写着「该拒答」）。
所以本题集的每条都带 `check`，由本脚本对着 `knowledge_doc` 逐条核实。

三类校验：
- `absent`  ：`terms` 里的词**一个都不能**出现在任何知识库文档里 ⇒ 该题确实无依据
- `multi`   ：每个事实都在对应文档里找得到，且**没有任何一篇文档同时包含全部事实**
              ⇒ 该题确实需要跨文档合并（不是单篇就能答的）
- `window`  ：`anchor + days` 与 `as_of` 的先后关系必须等于声明的 `expired`
              （给了 `rule_doc`/`rule_text` 时，还要求该文档里真有这条规则文字）

用法：
    $env:XJT_DB_PASSWORD='***'; $env:XJT_DB_PORT='3307'
    E:/miniconda3/python.exe -X utf8 ai/eval/verify_hard_cases.py

退出码：0 全部通过；1 有任一校验失败（含结构性检查）。

作者：成员3 · C42
"""
from __future__ import annotations

import argparse
import json
import os  # noqa: F401  (bootstrap 里会用到)
import re
import sys
from datetime import date, timedelta
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
DEFAULT_DATASET = EVAL_DIR / "rag_hard_cases.json"

MIN_TOTAL = 50
REQUIRED_CATEGORIES = ("库内无答案", "多源合并", "时效过期")


def bootstrap_backend() -> None:
    """把 `backend/` 加进 sys.path 并补齐默认环境变量（必须在 import app.* 之前）。"""
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    env_file = BACKEND_DIR / ".env"
    if env_file.exists():
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())
    os.environ.setdefault("XJT_DB_HOST", "127.0.0.1")
    os.environ.setdefault("XJT_DB_PORT", "3307")
    os.environ.setdefault("XJT_DB_NAME", "xiaojietong")


def _squash(text: str) -> str:
    """去掉所有空白再比对。

    必要性：知识库里既可能是「24 小时内上门」也可能是「24小时内上门」，
    而数据集是人写的 —— 空格差异不该让校验假失败（实测踩过）。
    """
    return re.sub(r"\s+", "", text or "")


def load_kb() -> dict[str, str]:
    """读知识库：{标题: 内容}（未删除的文档）。"""
    from app.core.config import settings
    from app.db import cpp_bridge

    if not cpp_bridge.available():
        raise SystemExit("❌ jt_db C++ 扩展不可用；先按 db/cpp_driver/README.md 构建到 backend/app/db/native/")
    if not cpp_bridge.pool_ready():
        cpp_bridge.init_db(host=settings.db_host, port=settings.db_port, user=settings.db_user,
                           password=settings.db_password, dbname=settings.db_name,
                           min_conn=settings.db_min_conn, max_conn=settings.db_max_conn)
    rows = cpp_bridge.query(
        "SELECT title, content FROM knowledge_doc WHERE status != 2", [])
    return {(r.get("title") or "").strip(): r.get("content") or "" for r in rows}


# ------------------------------------------------------------------ 校验 ----

def check_absent(item: dict, kb: dict[str, str]) -> list[str]:
    problems = []
    for term in item["check"]["terms"]:
        for title, content in kb.items():
            if _squash(term) in _squash(content) or _squash(term) in _squash(title):
                problems.append(
                    f"{item['id']}：断言的「无答案」不成立 —— 词「{term}」出现在《{title}》里；"
                    "该题已可答，应改类别或换问法")
    return problems


def check_conflict(item: dict, kb: dict[str, str]) -> list[str]:
    """保留：将来语料里出现**真实冲突**时用它（当前 27 篇无冲突，见数据集 meta）。"""
    problems = []
    for pair in item["check"]["pairs"]:
        title, needle = pair["title"], pair["must_contain"]
        if title not in kb:
            problems.append(f"{item['id']}：冲突的一方《{title}》不在知识库里")
        elif _squash(needle) not in _squash(kb[title]):
            problems.append(
                f"{item['id']}：《{title}》里没有「{needle}」—— 冲突可能已被修掉，"
                "该题不再成立（这是好事，但要同步更新数据集）")
    return problems


def check_multi(item: dict, kb: dict[str, str]) -> list[str]:
    """多源合并：每个事实各自找得到，且**没有单篇同时包含全部事实**。

    第二半才是关键：若某一篇就包含全部事实，这题单篇就能答，
    就不属于「多源合并」——它会缩小本类的意义（自检会把这种题揪出来）。
    """
    problems = []
    facts = item["check"]["facts"]
    needles = []
    for fact in facts:
        title, needle = fact["title"], fact["must_contain"]
        needles.append(_squash(needle))
        if title not in kb:
            problems.append(f"{item['id']}：事实所在文档《{title}》不在知识库里")
        elif _squash(needle) not in _squash(kb[title]):
            problems.append(
                f"{item['id']}：《{title}》里找不到事实「{needle}」—— 该题的依据已不存在")
    if len(facts) < 2:
        problems.append(f"{item['id']}：`multi` 至少要两个事实")
        return problems
    for title, content in kb.items():
        squashed = _squash(content)
        if all(n and n in squashed for n in needles):
            problems.append(
                f"{item['id']}：《{title}》一篇就包含了全部事实 —— 这题不是多源合并，"
                "应改成单源题或重写问题")
    return problems


def check_window(item: dict, kb: dict[str, str]) -> list[str]:
    c = item["check"]
    anchor = date.fromisoformat(c["anchor"])
    as_of = date.fromisoformat(c["as_of"])
    expired = (anchor + timedelta(days=int(c["days"]))) < as_of
    problems = []
    if expired != bool(c["expired"]):
        problems.append(
            f"{item['id']}：时效判断与声明不符 —— 按 {c['anchor']}+{c['days']}天 计算为 "
            f"{'已过期' if expired else '未过期'}，数据集写的是 "
            f"{'已过期' if c['expired'] else '未过期'}")
    rule_doc = c.get("rule_doc")
    if rule_doc:
        if rule_doc not in kb:
            problems.append(f"{item['id']}：规则来源《{rule_doc}》不在知识库里")
        elif c.get("rule_text") and _squash(c["rule_text"]) not in _squash(kb[rule_doc]):
            problems.append(
                f"{item['id']}：《{rule_doc}》里找不到规则文字「{c['rule_text']}」")
    return problems


CHECKERS = {"absent": check_absent, "multi": check_multi, "window": check_window}


def structural_problems(dataset: dict) -> tuple[list[str], dict[str, int]]:
    """不依赖数据库的结构性检查（数量/类别/字段/一致性）。"""
    problems: list[str] = []
    questions = dataset.get("questions") or []
    counts: dict[str, int] = {}

    ids = [q.get("id") for q in questions]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        problems.append(f"id 重复：{sorted(dupes)}")

    for q in questions:
        cat = q.get("category", "?")
        counts[cat] = counts.get(cat, 0) + 1
        if "expect_refuse" not in q:
            problems.append(f"{q.get('id')}：缺 `expect_refuse`，无法判定该不该拒答")
        if "check" not in q:
            problems.append(f"{q.get('id')}：缺 `check`，无法机器核验")
            continue
        kind = q["check"].get("kind")
        if kind not in CHECKERS:
            problems.append(f"{q.get('id')}：未知 check.kind={kind}")
        # 语义一致：可答的题不该被要求拒答；拒答题必须在「无答案/已过期」两类里
        if q.get("expect_refuse") and cat not in ("库内无答案", "时效过期"):
            problems.append(f"{q.get('id')}：`expect_refuse=true` 但类别是「{cat}」")
        if q.get("expect_refuse") and kind == "window" and not q["check"].get("expired"):
            problems.append(f"{q.get('id')}：时效未过期却要求拒答（会把正常可答当幻觉）")

    missing = [c for c in REQUIRED_CATEGORIES if not counts.get(c)]
    if missing:
        problems.append(f"缺类别：{missing}")
    if len(questions) < MIN_TOTAL:
        problems.append(f"题量 {len(questions)} < 验收线 {MIN_TOTAL}")
    return problems, counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="C42 硬样本集自检")
    ap.add_argument("--dataset", default=str(DEFAULT_DATASET))
    ap.add_argument("--kb-dump", default="",
                    help="离线模式：用 JSON（{标题: 内容}）代替数据库，便于在无库机器上跑")
    args = ap.parse_args(argv)

    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    questions = dataset["questions"]
    print(f"数据集：{args.dataset}（{len(questions)} 题）")

    problems, counts = structural_problems(dataset)
    print("  类别分布：" + "　".join(f"{k}={v}" for k, v in sorted(counts.items())))

    if args.kb_dump:
        kb = json.loads(Path(args.kb_dump).read_text(encoding="utf-8"))
    else:
        bootstrap_backend()
        kb = load_kb()
    print(f"  知识库：{len(kb)} 篇")

    by_cat: dict[str, list[str]] = {}
    for q in questions:
        checker = CHECKERS.get((q.get("check") or {}).get("kind"))
        if checker is None:
            continue
        found = checker(q, kb)
        by_cat.setdefault(q["category"], []).extend(found)
        problems.extend(found)

    print("-" * 74)
    for cat in REQUIRED_CATEGORIES:
        bad = by_cat.get(cat, [])
        n = counts.get(cat, 0)
        print(f"  {cat:<8} {n:>3} 条　校验{'通过 ✅' if not bad else f'失败 ❌（{len(bad)} 处）'}")

    if problems:
        print("-" * 74)
        for p in problems:
            print(f"❌ {p}")
        print(f"\n共 {len(problems)} 处问题 —— 修好再拿这套题下结论")
        return 1

    refuse = sum(1 for q in questions if q.get("expect_refuse"))
    print("-" * 74)
    print(f"✅ 全部通过：{len(questions)} 题（应拒答 {refuse} / 不应拒答 {len(questions) - refuse}），"
          f"三类校验均对着真实知识库成立")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
