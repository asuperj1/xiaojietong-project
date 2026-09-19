#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C41 拒答实验：把「答案侧」的信号实测一遍，并与「检索侧」阈值对比。

为什么需要它：`calibrate_gate.py` 已经实测出**检索分数救不了**（34 条应拒答样本上，
最优阈值仍有 88.2% 误命中）。剩下能让「无依据」被拦住的只有答案侧 —— 本脚本量两件事：

1. **答案支撑度**（`citation_check.text_support_ratio`，答案被检索结果支撑的比例）
   —— 思路是「编造的答案落不到来源文本上」。但它只能抓**编造**，
   抓不住「拿不相关的来源硬答」（那种答案句句有出处）。
2. **证据充分性判定**（让模型先回答一个元问题：「依据这些资料能否回答？」）
   —— 这是唯一真正针对「有无依据」的信号，代价是每次问答多一次小调用。

用法（需要 Ollama + 数据库）：
    $env:XJT_DB_PASSWORD='***'; $env:XJT_DB_PORT='3307'
    E:/miniconda3/python.exe -X utf8 ai/eval/refusal_bench.py \
        --dataset ai/eval/rag_hard_cases.json --model qwen2.5:3b \
        --out ai/eval/out/refusal_bench.json

退出码：0 = 有机制达到验收线（误命中 < 10%）；1 = 没有（如实报告）。

作者：成员3 · C41
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
DEFAULT_DATASET = EVAL_DIR / "rag_hard_cases.json"
DEFAULT_OUT = EVAL_DIR / "out" / "refusal_bench.json"
TARGET_FALSE_HIT = 0.10

#: 证据充分性判定的提示词。要点：**只让模型回答一个词**，避免它顺带把答案编出来。
JUDGE_PROMPT = (
    "下面给出若干条资料和一个问题。请判断：**仅凭这些资料**能否回答该问题？\n"
    "只输出一个词：`能` 或 `不能`。若资料里没有问题的答案（哪怕主题相关），输出 `不能`。\n\n"
    "资料：\n{knowledge}\n\n问题：{question}"
)


def bootstrap_backend() -> None:
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    env_file = BACKEND_DIR / ".env"
    if env_file.exists():
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    os.environ.setdefault("XJT_DB_HOST", "127.0.0.1")
    os.environ.setdefault("XJT_DB_PORT", "3307")
    os.environ.setdefault("XJT_DB_NAME", "xiaojietong")


def init_pool() -> None:
    from app.core.config import settings
    from app.db import cpp_bridge

    if not cpp_bridge.available():
        raise SystemExit("❌ jt_db C++ 扩展不可用（见 db/cpp_driver/README.md）")
    if not cpp_bridge.pool_ready():
        cpp_bridge.init_db(host=settings.db_host, port=settings.db_port, user=settings.db_user,
                           password=settings.db_password, dbname=settings.db_name,
                           min_conn=settings.db_min_conn, max_conn=settings.db_max_conn)


def chat(model: str, system: str, user: str, base_url: str, timeout: int = 180) -> tuple[str, str]:
    """一次问答。返回 (文本, 错误)。失败不抛异常，便于整轮跑完再统计。"""
    payload = json.dumps({
        "model": model, "stream": False,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "options": {"temperature": 0.0, "seed": 42, "num_ctx": 8192},
    }).encode("utf-8")
    req = urllib.request.Request(f"{base_url.rstrip('/')}/api/chat", data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return "", str(exc)
    return (data.get("message") or {}).get("content", ""), ""


async def run_one(item: dict, model: str, base_url: str, top_k: int,
                  citation_check) -> dict:
    from app.services import rag

    t0 = time.perf_counter()
    prompt, sources = await rag.build_system_prompt(item["question"])   # 生产路径
    answer, err = await asyncio.to_thread(
        chat, model, prompt, item["question"], base_url)

    sources_text = "\n".join(f"[{s.get('category')}] {s.get('title')}: {s.get('content')}"
                             for s in sources)
    knowledge = "\n".join(f"[{s.get('category')}] {s.get('title')}: {s.get('content')}"
                          for s in sources) or "（未检索到任何资料）"
    judge_raw, judge_err = await asyncio.to_thread(
        chat, model, "你是严谨的资料核对员。", JUDGE_PROMPT.format(
            knowledge=knowledge, question=item["question"]), base_url)

    report = citation_check.check_citations(
        answer, sources, question=item["question"], check_sentences=True)

    top = max((float(s.get("score") or 0.0) for s in sources), default=0.0)
    judge_says_no = ("不能" in judge_raw) and ("能" not in judge_raw.replace("不能", ""))
    return {
        "id": item["id"],
        "category": item["category"],
        "question": item["question"],
        "expect_refuse": bool(item.get("expect_refuse")),
        "n_sources": len(sources),
        "top_score": round(top, 4),
        "answer": answer[:200],
        "support_ratio": round(float(report.coverage or 0.0), 4),
        "unsupported_sentences": len(report.unsupported_sentences or []),
        "judge_raw": judge_raw.strip()[:20],
        "judge_refuse": bool(judge_says_no),
        "error": err or judge_err,
        "latency_s": round(time.perf_counter() - t0, 2),
    }


def sweep(rows: list[dict], key: str, *, lower_refuses: bool) -> list[dict]:
    """按某个分数信号扫阈值：`lower_refuses=True` 表示低于阈值就拒答。"""
    values = sorted({r[key] for r in rows})
    if not values:
        return []
    grid = [min(values)] + values + [max(values) + 1e-6]
    out = []
    for tau in grid:
        j = eval_at(rows, key, tau, lower_refuses)
        if out and out[-1]["false_hit_rate"] == j["false_hit_rate"] \
                and out[-1]["over_refusal_rate"] == j["over_refusal_rate"]:
            continue          # 同一取舍只留一行，报告可读
        out.append(j)
    return out


def eval_at(rows: list[dict], key: str, tau: float, lower_refuses: bool) -> dict:
    def refuse(r: dict) -> bool:
        return (r[key] < tau) if lower_refuses else (r[key] >= tau)

    should = [r for r in rows if r["expect_refuse"]]
    should_not = [r for r in rows if not r["expect_refuse"]]
    missed = [r["id"] for r in should if not refuse(r)]
    over = [r["id"] for r in should_not if refuse(r)]
    return {
        "threshold": round(tau, 4),
        "false_hit_rate": round(len(missed) / len(should), 4) if should else None,
        "over_refusal_rate": round(len(over) / len(should_not), 4) if should_not else None,
        "missed_ids": missed,
        "over_refusal_ids": over,
    }


def best(points: list[dict], max_over: float) -> dict | None:
    ok = [p for p in points
          if p["over_refusal_rate"] is not None and p["over_refusal_rate"] <= max_over]
    return min(ok, key=lambda p: p["false_hit_rate"]) if ok else None


def verdict_block(rows: list[dict], max_over: float) -> dict:
    should = [r for r in rows if r["expect_refuse"]]
    should_not = [r for r in rows if not r["expect_refuse"]]
    base_missed = [r["id"] for r in should if False]   # 现状：完全不拒答
    out = {
        "n_total": len(rows),
        "n_expect_refuse": len(should),
        "n_expect_answer": len(should_not),
        "baseline": {"false_hit_rate": 1.0 if should else None, "over_refusal_rate": 0.0,
                     "note": "C20 现状：只靠「检索无结果」触发，51 题里一条都没拒"},
        "signals": {},
    }
    for name, key, lower in (("retrieval_top_score", "top_score", True),
                             ("answer_support_ratio", "support_ratio", True)):
        pts = sweep(rows, key, lower_refuses=lower)
        ch = best(pts, max_over)
        out["signals"][name] = {"curve": pts, "chosen": ch,
                                "meets_target": bool(ch and ch["false_hit_rate"] < TARGET_FALSE_HIT)}
    # 判定类信号：不走阈值，直接用 yes/no
    missed = [r["id"] for r in should if not r["judge_refuse"]]
    over = [r["id"] for r in should_not if r["judge_refuse"]]
    out["signals"]["llm_sufficiency_judge"] = {
        "chosen": {"threshold": None,
                   "false_hit_rate": round(len(missed) / len(should), 4) if should else None,
                   "over_refusal_rate": round(len(over) / len(should_not), 4) if should_not else None,
                   "missed_ids": missed, "over_refusal_ids": over},
        "meets_target": (len(missed) / len(should) < TARGET_FALSE_HIT) if should else False,
    }
    return out


def render_md(v: dict, max_over: float) -> str:
    lines = [
        "# C41 拒答机制实测（答案侧 vs 检索侧）",
        "",
        f"- 样本 **{v['n_total']}** 题：应拒答 **{v['n_expect_refuse']}** / 应作答 **{v['n_expect_answer']}**",
        f"- 验收线：应拒答却未拒答（误命中）< **{TARGET_FALSE_HIT:.0%}**；本报告政策：误拒率 ≤ {max_over:.0%}",
        "",
        "## 结论",
        "",
        f"| 信号 | 误命中率 | 误拒率 | 达到验收线 |",
        "|---|---|---|---|",
    ]
    label = {"retrieval_top_score": "检索最高分（阈值）",
             "answer_support_ratio": "答案支撑度（阈值）",
             "llm_sufficiency_judge": "证据充分性判定（LLM 判能/不能）"}
    for key, sig in v["signals"].items():
        ch = sig.get("chosen")
        if not ch:
            lines.append(f"| {label[key]} | — | — | ❌ 无可行阈值 |")
            continue
        lines.append(f"| {label[key]} | {ch['false_hit_rate']:.1%} | "
                     f"{ch['over_refusal_rate']:.1%} | {'✅' if sig['meets_target'] else '❌'} |")
    lines += ["", "## 现状（不拒答）", "",
              f"- 误命中率 {v['baseline']['false_hit_rate']:.1%} —— 这正是审计里的「负样本误命中 100%」", ""]
    for key, sig in v["signals"].items():
        ch = sig.get("chosen")
        if not ch:
            continue
        lines += [f"### {label[key]}", "",
                  f"- 仍未拒答：{', '.join(ch['missed_ids']) or '无'}",
                  f"- 被误拒：{', '.join(ch['over_refusal_ids']) or '无'}", ""]
    lines += [
        "## 怎么读",
        "",
        "- **检索侧**：`ai/eval/calibrate_gate.py` 的结论一致 —— 分数分不开两类样本；",
        "  根因是 bge-m3 对「同域但无答案」的问题也给 0.5+ 的相似度（如问『教务处的电话』会命中教务系统文档）。",
        "- **答案支撑度**：只能抓「编造」（答案句子在来源里找不到 4-gram），",
        "  抓不住「拿不相关来源硬答」—— 后者句句有出处。",
        "- **充分性判定**：直接问模型「仅凭这些资料能否回答」，这是唯一针对「有无依据」的信号，",
        "  代价是每次问答多一次小调用（本报告记录了它的取舍）。",
    ]
    return "\n".join(lines) + "\n"


async def main_async(args) -> int:
    from app.services import citation_check, rag  # noqa: F401  (必须在 bootstrap 之后)

    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    questions = dataset["questions"]
    if args.limit:
        questions = questions[: args.limit]
    print(f"数据集：{args.dataset}（{len(questions)} 题）｜模型 {args.model}")

    rows = []
    for i, item in enumerate(questions, 1):
        row = await run_one(item, args.model, args.base_url, args.top_k, citation_check)
        rows.append(row)
        flag = "✅" if (row["judge_refuse"] == row["expect_refuse"]) else "⚠️"
        print(f"  [{i}/{len(questions)}] {row['id']:<4} {flag} "
              f"应拒答={str(row['expect_refuse']):<5} 判定拒答={str(row['judge_refuse']):<5} "
              f"top={row['top_score']:.3f} 支撑={row['support_ratio']:.2f}"
              f"{'  ERR:' + row['error'][:40] if row['error'] else ''}")

    v = verdict_block(rows, args.max_over_refusal)
    ok = any(s.get("meets_target") for s in v["signals"].values())

    print("=" * 78)
    for key, sig in v["signals"].items():
        ch = sig.get("chosen")
        if ch:
            fh = "—" if ch["false_hit_rate"] is None else f"{ch['false_hit_rate']:.1%}"
            ov = "—" if ch["over_refusal_rate"] is None else f"{ch['over_refusal_rate']:.1%}"
            print(f"  {key:<24} 误命中 {fh}  误拒 {ov}"
                  f"  {'✅' if sig['meets_target'] else '❌'}")
        else:
            print(f"  {key:<24} 无可行阈值")
    print(f"  ⇒ {'✅ 有机制达到验收线' if ok else '❌ 没有机制达到验收线（如实报告）'}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"verdict": v, "rows": rows}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    out.with_suffix(".md").write_text(render_md(v, args.max_over_refusal), encoding="utf-8")
    print(f"  报告：{out} ｜ {out.with_suffix('.md')}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="C41 拒答机制实测")
    ap.add_argument("--dataset", default=str(DEFAULT_DATASET))
    ap.add_argument("--model", default="qwen2.5:3b")
    ap.add_argument("--base-url", default="http://127.0.0.1:11434")
    ap.add_argument("--top-k", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-over-refusal", type=float, default=0.10)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)
    bootstrap_backend()
    init_pool()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
