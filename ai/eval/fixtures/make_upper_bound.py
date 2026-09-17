#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成"上限答案"文件：`answers_upper_bound.json`。

【用途】
    把评测集的 `expected` 原样当作模型预测喂回去。如果评分函数和评测集是自洽的，
    micro-F1 **必须恰好等于 1.0**。任何小于 1.0 的结果都说明评分链路有 bug
    （而不是模型不行）。

    这是评测框架的**自洽性检查**，用来回答一个很实际的问题：
    "等真跑出 0.72 的 F1 时，我怎么知道是模型差还是我的评分写错了？"

【用法】
    python ai/eval/fixtures/make_upper_bound.py
    python ai/eval/extract_bench.py --backend scripted \\
        --scripted-answers ai/eval/fixtures/answers_upper_bound.json \\
        --out ai/eval/out/extract_upper_bound.json
    # 期望：micro-F1 = 1.0；若不是 1.0，就是评分函数坏了

【⚠️ 这个文件不是模型结果】
    `answers_upper_bound.json` 只是"抄了答案的假模型"。它**不能**作为
    "抽取效果"的证据引用；报告里请只引用 `backend: ollama` 的结果。
    本脚本刻意把它放在 `fixtures/` 而不是 `out/`，就是为了避免被误读成基线。

作者：成员3（C++ 数据层 / 模型微调 / 数据库）· C34
"""
from __future__ import annotations

import json
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent
REPO_ROOT = FIXTURES_DIR.parents[2]
DATASET = REPO_ROOT / "ai" / "eval" / "extract_cases.json"
OUT = FIXTURES_DIR / "answers_upper_bound.json"


def main() -> int:
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    answers = {c["id"]: json.dumps(c["expected"], ensure_ascii=False) for c in data["cases"]}
    payload = {
        "purpose": "upper-bound self-check: predictions == ground truth",
        "warning": (
            "NOT a model result. Used only to verify that the scorer and the dataset "
            "are self-consistent (micro-F1 must be exactly 1.0). Never report this as "
            "an extraction baseline."
        ),
        "generated_by": "ai/eval/fixtures/make_upper_bound.py",
        "dataset": str(DATASET.relative_to(REPO_ROOT)),
        "reference_date": data.get("reference_date"),
        "n": len(answers),
        "answers": answers,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[make_upper_bound] {len(answers)} 条 -> {OUT.relative_to(REPO_ROOT)}")
    print("[make_upper_bound] 提示：这是自检用假答案，不是模型结果")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
