#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`C16` 重排离线评测：重排到底有没有把「该排第一的」提前？

为什么不用 Ollama 就能测
-----------------------
`C14` 基线（`ai/eval/baselines/rag_baseline_20260912.json`）冻结了每题**实际召回了哪几篇**
（`got_titles`），知识库正文可从 MySQL 读。所以「在已召回集合内重排」这件事
**完全可离线复算** —— 这正是重排的作用域。

⚠️ 两个必须写在报告里的事实（不说清楚就是自欺）
-----------------------------------------------
1. **`hit@3` 已经饱和**：`C14` 实测 `hit@3 = 100%`。任务卡给 `C16` 定的验收是
   「`Top-3` + ≥ 5 个百分点」，而 `Top-3` 已是满分 → **该指标零信息量，无法再提升**。
   真正有区分度的是 `hit@1`（92%）与 `MRR`（0.9533），所以本脚本按 `hit@1/MRR` 报。
2. **基线只存了 top-3**：无法离线评估「先多召 20 条再重排」的收益（那需要向量库 + Ollama）。
   本脚本测的是**在 3 条候选内重排**，是收益的**下界**。

用法
----
    cd <repo>
    $env:XJT_DB_PASSWORD='<your-local-password>'; $env:XJT_DB_PORT='3307'
    python tools/eval_rerank.py

退出码：0 = 重排相对基线有 ≥ 5 个百分点的提升（达标）；1 = 未达标（见本次实测：纯词法覆盖式重排为负收益）；2 = 环境不可用。

作者：成员3 · C16
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("XJT_DB_HOST", "127.0.0.1")
os.environ.setdefault("XJT_DB_PORT", "3307")

from app.db import cpp_bridge  # noqa: E402
from app.services.rerank import LexicalStrategy, NoopStrategy  # noqa: E402

BASELINE = _ROOT / "ai" / "eval" / "baselines" / "rag_baseline_20260912.json"


def _load_docs() -> dict[str, dict]:
    rows = cpp_bridge.query(
        "SELECT title, category, content, source_url FROM knowledge_doc WHERE status != 2",
        [],
    )
    return {
        str(r.get("title") or ""): {
            "title": str(r.get("title") or ""),
            "category": str(r.get("category") or ""),
            "content": str(r.get("content") or ""),
            "source_url": str(r.get("source_url") or ""),
        }
        for r in rows
    }


def _metrics(rows: list[dict], order_of) -> dict:
    """order_of(row) -> list[str]（按最终排序的标题列表），算 hit@1/hit@3/MRR。"""
    n = len(rows)
    hit1 = hit3 = 0
    rr_sum = 0.0
    ranks: list[int | None] = []
    for r in rows:
        expected = set(r.get("expected_titles") or [])
        order = order_of(r)
        rank = next((i + 1 for i, t in enumerate(order) if t in expected), None)
        ranks.append(rank)
        if rank == 1:
            hit1 += 1
        if rank is not None and rank <= 3:
            hit3 += 1
        if rank:
            rr_sum += 1.0 / rank
    return {
        "n": n,
        "hit@1": hit1 / n if n else 0.0,
        "hit@3": hit3 / n if n else 0.0,
        "mrr": rr_sum / n if n else 0.0,
        "ranks": ranks,
    }


def _bar(v: float, width: int = 20) -> str:
    filled = int(round(v * width))
    return "#" * filled + "." * (width - filled)


def main() -> int:
    if not BASELINE.exists():
        print(f"[NG] 基线不存在：{BASELINE}")
        return 2

    try:
        cpp_bridge.init_db(
            os.environ["XJT_DB_HOST"],
            int(os.environ["XJT_DB_PORT"]),
            os.environ.get("XJT_DB_USER", "root"),
            os.environ.get("XJT_DB_PASSWORD", ""),
            os.environ.get("XJT_DB_NAME", "xiaojietong"),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[NG] 数据库连接失败：{exc}")
        return 2

    docs = _load_docs()
    payload = json.loads(BASELINE.read_text(encoding="utf-8"))
    all_rows = payload.get("rows") or []
    pos_rows = [r for r in all_rows if r.get("expected_titles") and r.get("got_titles")]
    neg_rows = [r for r in all_rows if not r.get("expected_titles") and r.get("got_titles")]
    if not pos_rows:
        print("[NG] 基线里没有可用的正样本")
        return 2

    # 题面 + 召回标题 → 候选文档（丢掉库里已不存在的标题，并提示）
    missing: set[str] = set()

    def candidates(row: dict) -> list[dict]:
        out = []
        for t in row.get("got_titles") or []:
            if t in docs:
                out.append(docs[t])
            else:
                missing.add(t)
        return out

    noop = NoopStrategy()
    lex = LexicalStrategy()

    base = _metrics(pos_rows, lambda r: list(r.get("got_titles") or []))
    after = _metrics(
        pos_rows,
        lambda r: [d["title"] for d in lex.rerank(str(r.get("question") or ""), candidates(r))],
    )

    print("=" * 92)
    print("C16 重排离线评测（C14 基线召回集内重排 · 真实知识库正文）")
    print("=" * 92)
    print(f"知识库文档 {len(docs)} 篇 ｜ 基线题 {len(all_rows)}（正 {len(pos_rows)} / 负 {len(neg_rows)}）")
    print(f"基线冻结指标：hit@1={payload['summary']['hit@1']:.2f}  hit@3={payload['summary']['hit@3']:.2f}  "
          f"mrr={payload['summary']['mrr']:.4f}")
    if missing:
        print(f"[!] 基线里有 {len(missing)} 个标题在现行库中不存在，已跳过：{sorted(missing)[:5]}")

    print("\n---- 指标对比（在已召回的 top-3 内重排）----")
    print(f"{'指标':<8}{'baseline':>12}{'rerank':>12}{'Δ':>10}")
    for key in ("hit@1", "hit@3", "mrr"):
        d = after[key] - base[key]
        print(f"{key:<8}{base[key]:>12.4f}{after[key]:>12.4f}{d:>+10.4f}")

    print("\n---- 排序细节 ----")
    print(f"  baseline  hit@1 {_bar(base['hit@1'])}  MRR {_bar(base['mrr'])}")
    print(f"  rerank    hit@1 {_bar(after['hit@1'])}  MRR {_bar(after['mrr'])}")
    changed = [i for i in range(len(pos_rows)) if base["ranks"][i] != after["ranks"][i]]
    for i in changed:
        r = pos_rows[i]
        print(f"  {r['id']} {str(r['question'])[:20]:<22} rank {base['ranks'][i]} → {after['ranks'][i]}   "
              f"预期={r.get('expected_titles')}")
    print(f"  排序发生变化的题：{len(changed)} / {len(pos_rows)}")

    # ---------------- 负样本：重排分数能不能当拒答信号 ----------------
    print("\n---- 负样本可分辨性（C14 遗留问题：负样本误命中 100%）----")
    pos_top, neg_top = [], []
    for r in pos_rows:
        cands = candidates(r)
        if cands:
            pos_top.append(max(lex.score_all(str(r.get("question") or ""), cands)))
    for r in neg_rows:
        cands = candidates(r)
        if cands:
            neg_top.append(max(lex.score_all(str(r.get("question") or ""), cands)))
    if pos_top and neg_top:
        pos_top.sort()
        neg_top.sort()
        print(f"  正样本 top-1 词法分：min={pos_top[0]:.3f} 中位={pos_top[len(pos_top)//2]:.3f} max={pos_top[-1]:.3f}")
        print(f"  负样本 top-1 词法分：min={neg_top[0]:.3f} 中位={neg_top[len(neg_top)//2]:.3f} max={neg_top[-1]:.3f}")
        separable = min(pos_top) > max(neg_top)
        print(f"  是否可分（正 min > 负 max）：{'是' if separable else '**否**'}")
        if not separable:
            print("  → 与 `C20` 的实测结论一致：**词法重合度不能当拒答门禁**。")
            print("     拒答仍需相似度分数，见 CAC-27（评测基线补记 score）。")

    print("\n" + "=" * 92)
    improved = after["hit@1"] - base["hit@1"] >= 0.05 or after["mrr"] - base["mrr"] >= 0.05
    print(f"[{'PASS' if improved else 'NG'}] hit@1 {base['hit@1']:.4f} → {after['hit@1']:.4f}"
          f" ｜ MRR {base['mrr']:.4f} → {after['mrr']:.4f}")
    if not improved:
        print("结论：**纯词法「覆盖式」重排在已召回集内是负收益**（丢了向量语义信号）。")
        print("      正确做法是分数融合 `w·向量分 + (1−w)·词法分`，而不是覆盖；")
        print("      而 w 需要逐命中项的向量分（`CAC-27`），故本策略保持默认关闭。")
    print("注意：任务卡给 C16 的验收指标 `Top-3 +≥5pp` **已饱和（hit@3 基线 100%）**，")
    print("      本脚本按 hit@1/MRR 报；且基线只存了 top-3，测不到「多召 20 条再重排」的收益（是下界）。")
    print("=" * 92)
    return 0 if improved else 1


if __name__ == "__main__":
    raise SystemExit(main())
