#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C41 拒答阈值校准：拿 `rag_bench` 落盘的**逐题分数**扫阈值，量化「拒答」与「误拒」的取舍。

为什么要单独一个脚本：
- C20 的 `should_refuse` 只有**一个**分数阈值，而它的注释里已经记着
  「不存在任何相似度阈值能同时做到『不拒答 Q10』与『拒答 N01』」——
  但那条结论是基于 **2 条负样本**得出的。2 个点上调参就是过拟合，
  所以 C42 先把样本做够（51 题），这里再把整条曲线算出来。
- 阈值不能靠手拍：本脚本输出**完整曲线**（每个 τ 的拒答率/误拒率），
  以及按给定政策（误拒上限）选出的 τ，让取舍可复核。

输入：`rag_bench --dataset ai/eval/rag_hard_cases.json --out <前缀>` 产出的 JSON
      （需要每条带 `scores` 与 `expect_refuse`）。

用法：
    python ai/eval/calibrate_gate.py --report ai/eval/out/hard.json
    python ai/eval/calibrate_gate.py --report ... --max-over-refusal 0.05 --out ai/eval/out/gate

退出码：0 = 存在满足验收线的阈值；1 = 达不到（如实报告，不粉饰）。

作者：成员3 · C41
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

#: C41 验收线：负样本误命中率 100% → < 10%
TARGET_FALSE_HIT = 0.10


def load_rows(report: Path) -> list[dict]:
    data = json.loads(report.read_text(encoding="utf-8"))
    rows = data.get("rows") or []
    if not rows:
        raise SystemExit(f"❌ 报告里没有逐题数据：{report}")
    if not any("expect_refuse" in r for r in rows):
        raise SystemExit(
            f"❌ 报告缺少 `expect_refuse` 字段：{report}\n"
            "   请用 C42 的样本集重跑：ai/eval/rag_bench.py --dataset ai/eval/rag_hard_cases.json")
    return rows


def top_score(row: dict) -> float | None:
    """本轮检索的最高分；没有命中返回 None。"""
    scores = row.get("scores") or []
    return float(scores[0]) if scores else None


def decide(row: dict, tau: float) -> bool:
    """τ 下的拒答判定：**无命中**或**最高分低于 τ** ⇒ 拒答。"""
    top = top_score(row)
    if top is None:
        return True
    return top < tau


def evaluate(rows: list[dict], tau: float) -> dict:
    """在给定 τ 下算拒答指标。"""
    should = [r for r in rows if r.get("expect_refuse")]
    should_not = [r for r in rows if not r.get("expect_refuse")]
    refused = [r for r in rows if decide(r, tau)]

    # 验收线口径：应拒答却没拒答（= 拿无依据的来源去回答）的比例
    missed = [r for r in should if not decide(r, tau)]
    over = [r for r in should_not if decide(r, tau)]
    return {
        "tau": round(tau, 4),
        "false_hit_rate": round(len(missed) / len(should), 4) if should else None,
        "refusal_hit_rate": round((len(should) - len(missed)) / len(should), 4) if should else None,
        "over_refusal_rate": round(len(over) / len(should_not), 4) if should_not else None,
        "refused_n": len(refused),
        "missed_ids": [r["id"] for r in missed],
        "over_refusal_ids": [r["id"] for r in over],
    }


def curve(rows: list[dict], steps: int = 60) -> list[dict]:
    """在观测到的分数区间上均匀扫 τ（只看实际出现过的分，避免空扫）。"""
    tops = [top_score(r) for r in rows if top_score(r) is not None]
    if not tops:
        return [evaluate(rows, 1.0)]
    lo, hi = min(tops), max(tops)
    # 端点各外扩一点：τ 低于最低分 = 只有无命中的才拒；高于最高分 = 全拒
    span = (hi - lo) or 0.01
    grid = [lo - 0.01 + (span + 0.02) * i / (steps - 1) for i in range(steps)]
    return [evaluate(rows, t) for t in grid]


def pick(curve_points: list[dict], max_over_refusal: float) -> dict | None:
    """在「误拒率 ≤ 上限」里选误命中率最低的 τ。"""
    ok = [p for p in curve_points
          if p["over_refusal_rate"] is not None and p["over_refusal_rate"] <= max_over_refusal
          and p["false_hit_rate"] is not None]
    if not ok:
        return None
    return min(ok, key=lambda p: (p["false_hit_rate"], p["over_refusal_rate"]))


def render_markdown(rows: list[dict], points: list[dict], chosen: dict | None,
                    policy: float) -> str:
    should = [r for r in rows if r.get("expect_refuse")]
    should_not = [r for r in rows if not r.get("expect_refuse")]
    lines = [
        "# C41 拒答阈值校准（检索分数口径）",
        "",
        f"- 样本：{len(rows)} 题（应拒答 **{len(should)}** / 不应拒答 **{len(should_not)}**）",
        f"- 判据：无命中或最高分 < τ ⇒ 拒答；政策：误拒率 ≤ **{policy:.0%}**",
        f"- 验收线：应拒答却未拒答（false hit）< **{TARGET_FALSE_HIT:.0%}**",
        "",
        "## 实测曲线（节选）",
        "",
        "| τ | 拒答率(应拒答) | 误命中率 | 误拒率(不应拒答) | 拒答总数 |",
        "|---|---|---|---|---|",
    ]
    for p in points[:: max(1, len(points) // 12)]:
        lines.append(
            f"| {p['tau']:.3f} | {p['refusal_hit_rate']:.1%} | {p['false_hit_rate']:.1%} "
            f"| {p['over_refusal_rate']:.1%} | {p['refused_n']} |")
    lines += ["", "## 按政策选出的阈值", ""]
    if chosen is None:
        lines.append(f"- ❌ 在误拒率 ≤ {policy:.0%} 的约束下**找不到任何 τ** —— "
                     "说明「检索分数」这一个信号分不开两类样本。")
    else:
        lines.append(f"- τ = **{chosen['tau']:.3f}** ⇒ 误命中率 "
                     f"**{chosen['false_hit_rate']:.1%}**，误拒率 {chosen['over_refusal_rate']:.1%}")
        lines.append(f"- 仍未拒答的题：{', '.join(chosen['missed_ids']) or '无'}")
        lines.append(f"- 被误拒的题：{', '.join(chosen['over_refusal_ids']) or '无'}")
        verdict = ("✅ 达到验收线" if chosen["false_hit_rate"] < TARGET_FALSE_HIT
                   else "❌ 未达验收线")
        lines.append(f"- {verdict}")
    lines += [
        "",
        "## 怎么读这张表",
        "",
        "- 曲线**单调**是正常的：τ 越高拒得越多，误命中降、误拒升。",
        "- 要看的是**能不能在误拒可接受的区间里把误命中压到 10% 以下**；",
        "  若整条曲线都做不到，说明单一检索分数不足以判「有无依据」，",
        "  应当换信号（答案侧支撑度 / 知识库覆盖）而不是继续拧阈值。",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="C41 拒答阈值校准")
    ap.add_argument("--report", required=True, help="rag_bench 落盘的 JSON")
    ap.add_argument("--max-over-refusal", type=float, default=0.10,
                    help="可接受的误拒率上限（默认 0.10）")
    ap.add_argument("--out", default="", help="输出前缀（写 .json 与 .md）")
    ap.add_argument("--steps", type=int, default=60)
    args = ap.parse_args(argv)

    rows = load_rows(Path(args.report))
    points = curve(rows, args.steps)
    chosen = pick(points, args.max_over_refusal)

    should = [r for r in rows if r.get("expect_refuse")]
    print("=" * 74)
    print("C41 拒答阈值校准")
    print("=" * 74)
    print(f"  样本 {len(rows)} 题（应拒答 {len(should)} / 不应拒答 {len(rows) - len(should)}）")
    baseline = evaluate(rows, 0.0)   # τ=0 ⇒ 只有「无命中」才拒答 = C20 现状
    print(f"  现状（τ 取最小值，等价于 C20 的『只靠无命中触发』）："
          f"误命中率 {baseline['false_hit_rate']:.1%}  误拒率 {baseline['over_refusal_rate']:.1%}")
    if chosen is None:
        print(f"  ❌ 误拒率 ≤ {args.max_over_refusal:.0%} 的约束下无解")
        ok = False
    else:
        print(f"  选定 τ = {chosen['tau']:.3f}：误命中率 {chosen['false_hit_rate']:.1%}，"
              f"误拒率 {chosen['over_refusal_rate']:.1%}")
        for p in points:
            print(f"    τ={p['tau']:.3f}  误命中 {p['false_hit_rate']:.1%}  "
                  f"误拒 {p['over_refusal_rate']:.1%}")
            break
        ok = chosen["false_hit_rate"] < TARGET_FALSE_HIT
        print(f"  {'✅ 达到' if ok else '❌ 未达到'}验收线（< {TARGET_FALSE_HIT:.0%}）"
              f"；未拒答题：{', '.join(chosen['missed_ids']) or '无'}")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "policy": {"max_over_refusal": args.max_over_refusal,
                       "target_false_hit": TARGET_FALSE_HIT},
            "baseline_tau0": baseline,
            "chosen": chosen,
            "curve": [{k: v for k, v in p.items() if not k.endswith("_ids")} for p in points],
        }
        out.with_suffix(".json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        out.with_suffix(".md").write_text(
            render_markdown(rows, points, chosen, args.max_over_refusal), encoding="utf-8")
        print(f"  报告已落盘：{out.with_suffix('.json')} ｜ {out.with_suffix('.md')}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
