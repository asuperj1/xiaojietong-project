#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C33 抽取微调数据集构建器：合成语料 → 训练用 chat JSONL（+ 元数据）。

【为什么要单独一个构建器，而不是直接让 `train.py` 读生成器输出】
    1. **system prompt 必须取权威出处**：C34 的 `extract_bench.SYSTEM_PROMPT`
       （`finetuned` 与 `zero-shot` 要求逐字节相同，C34 自带 `prompt_mismatch` 自检）。
       这里**按路径加载**那个常量，并把它的 **sha1 写进元数据** ——
       训练报告因此能证明"训练时用的 prompt"与"评测时用的 prompt"是同一个字符串
       （`extract_bench` 的报告里也有 `system_prompt_sha1`，两边一比就知道）。
    2. **切分要有纪律**：train/dev 必须**按类型分层**切，否则 dev 里可能整类缺失，
       早停选的 checkpoint 就不可信。
    3. **留出集要硬隔离**：C34 评测集 24 条 + C34/C35 的 16 条示例池，一条都不许进训练。
       这里在写盘前**再查一遍**（生成器里已有测试，这里是第二道闸），
       发现重合直接退出码 2，不写任何文件。

【产出】
    ai/finetune/data/extract_train.jsonl   训练集（每行 {"id","messages":[...]}）
    ai/finetune/data/extract_dev.jsonl     开发集（选 checkpoint / 早停）
    ai/finetune/data/extract_dataset.json  元数据：seed、条数、分层统计、留出集隔离结果、
                                           system prompt 的 sha1、以及生成的命令

【用法】
    python ai/finetune/build_extract_dataset.py                 # 默认 800 条，8:2 分层切分
    python ai/finetune/build_extract_dataset.py --count 1200 --dev-ratio 0.15

    # 训练（下一步；参数记录在训练报告里）
    python ai/finetune/train.py --base_model <本地基座路径> \\
        --data ai/finetune/data/extract_train.jsonl \\
        --output ai/finetune/out/xjt-extract-3b --epochs 3

退出码：0 = 成功；2 = 环境/数据错误（含留出集重合）。

作者：成员3（C++ 数据层 / 模型微调 / 数据库）· C33
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent              # ai/finetune
REPO_ROOT = HERE.parent.parent
DATASET_DIR = REPO_ROOT / "ai" / "dataset"
EVAL_DIR = REPO_ROOT / "ai" / "eval"
DEFAULT_OUT_DIR = HERE / "data"


def _load(path: Path, name: str):
    """按路径加载（并注册进 sys.modules —— 被测模块里有 dataclass，不注册会炸）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_modules():
    synth = _load(DATASET_DIR / "synth_notice.py", "synth_notice_build")
    bench = _load(EVAL_DIR / "extract_bench.py", "extract_bench_build")
    return synth, bench


def stratified_split(samples: list, dev_ratio: float, seed: int) -> tuple[list, list]:
    """按 `tags["kind"]` 分层切 train/dev。

    分层的原因：类别/时间表达在切分后必须**两边都覆盖**，否则 dev 上量到的是另一回事，
    早停会挑错 checkpoint。同层内用带种子的洗牌保证可复现。
    """
    if not 0 < dev_ratio < 1:
        raise ValueError("dev-ratio 必须在 (0, 1) 开区间内")
    rng = random.Random(seed)
    buckets: dict[str, list] = {}
    for s in samples:
        buckets.setdefault(s.tags["kind"], []).append(s)

    train: list = []
    dev: list = []
    for kind in sorted(buckets):
        group = list(buckets[kind])
        rng.shuffle(group)
        n_dev = max(1, round(len(group) * dev_ratio)) if len(group) > 1 else 0
        dev.extend(group[:n_dev])
        train.extend(group[n_dev:])
    # 再各洗一次：避免写盘顺序把"类型"写成块状（训练时 batch 内多样性更好）
    rng.shuffle(train)
    rng.shuffle(dev)
    return train, dev


def write_jsonl(rows: list[dict], path: Path) -> tuple[int, str]:
    """写 JSONL，返回 (行数, sha256)。

    为什么要算 sha256：训练报告里要能证明"哪个模型是哪份数据训出来的 —— 一字不改"。
    没有这个，后续任何人改一行数据都不会被察觉（而数据变了，F1 就不可比了）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            line = json.dumps(r, ensure_ascii=False) + "\n"
            fh.write(line)
            digest.update(line.encode("utf-8"))
    return len(rows), digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

    ap = argparse.ArgumentParser(prog="python ai/finetune/build_extract_dataset.py",
                                 description="C33 抽取微调数据集构建（合成语料 → chat JSONL）")
    ap.add_argument("--count", type=int, default=800)
    ap.add_argument("--seed", type=int, default=20260917)
    ap.add_argument("--dev-ratio", type=float, default=0.2)
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    args = ap.parse_args(argv)

    synth, bench = load_modules()
    system_prompt = bench.SYSTEM_PROMPT
    prompt_sha1 = hashlib.sha1(system_prompt.encode("utf-8")).hexdigest()

    print(f"[build_extract_dataset] 生成 {args.count} 条合成语料（seed={args.seed}）")
    samples = synth.generate(args.count, seed=args.seed)
    problems = synth.validate_all(samples)
    if problems:
        print(f"❌ 生成语料自检未通过（{len(problems)} 项）：")
        for p in problems[:10]:
            print(f"   - {p}")
        return 2

    # ---- 留出集硬隔离（第二道闸；生成器里已有测试）----
    holdout_texts = synth.load_holdout_texts()
    hold_norm = {t.replace(" ", "") for t in holdout_texts}
    overlap = [s.id for s in samples if s.text.replace(" ", "") in hold_norm]
    if overlap:
        print(f"❌ 与留出集（C34 评测集 + C34/C35 示例池）正文重合：{overlap[:5]}…")
        return 2
    sim = synth.similarity_report(samples, holdout_texts)
    print(f"  留出集隔离：{len(holdout_texts)} 条，正文零重合；"
          f"最大 {sim['n_gram']}-gram 重合 {sim['max_similarity']}（阈值 {sim['warn_at']}）")
    if not sim["passed"]:
        print(f"❌ 与留出集的表层重合度过高（{sim['max_similarity']} ≥ {sim['warn_at']}）——"
              "先改模板族再训，否则「提升」很可能是抄来的")
        return 2

    train, dev = stratified_split(samples, args.dev_ratio, args.seed)
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = REPO_ROOT / out_dir

    train_rows = [synth.to_messages(s, system_prompt) for s in train]
    dev_rows = [synth.to_messages(s, system_prompt) for s in dev]
    n_train, sha_train = write_jsonl(train_rows, out_dir / "extract_train.jsonl")
    n_dev, sha_dev = write_jsonl(dev_rows, out_dir / "extract_dev.jsonl")

    stats = synth.summarize(samples)
    meta = {
        "task": "C33",
        "created": time.strftime("%Y-%m-%d"),
        "command": "python ai/finetune/build_extract_dataset.py "
                   f"--count {args.count} --seed {args.seed} --dev-ratio {args.dev_ratio}",
        "generator": "ai/dataset/synth_notice.py",
        "generator_seed": args.seed,
        "reference_date": synth.REFERENCE_DATE.isoformat(),
        "system_prompt_source": "ai/eval/extract_bench.py::SYSTEM_PROMPT",
        "system_prompt_sha1": prompt_sha1,
        "system_prompt_chars": len(system_prompt),
        "counts": {"total": stats["count"], "train": n_train, "dev": n_dev},
        "file_sha256": {"extract_train.jsonl": sha_train, "extract_dev.jsonl": sha_dev},
        "split": {"strategy": "按 tags.kind 分层", "dev_ratio": args.dev_ratio},
        "coverage": {
            "by_category": stats["by_category"],
            "by_importance": stats["by_importance"],
            "by_time_kind": stats["by_time_kind"],
            "by_n_entities": stats["by_n_entities"],
            "null_deadline_count": stats["null_deadline_count"],
            "matrix_cells_covered": stats["matrix_cells_covered"],
        },
        "holdout_isolation": {
            "holdout_size": len(holdout_texts),
            "holdout_sources": ["ai/eval/extract_cases.json",
                                "ai/eval/fixtures/few_shot_examples.json",
                                "ai/eval/fixtures/prompt_opt_examples_ext.json"],
            "text_overlap": 0,
            "max_ngram_similarity": sim["max_similarity"],
            "threshold": sim["warn_at"],
            "passed": sim["passed"],
        },
        "known_limitations": [
            "训练集是**合成**的；C34 评测集是人工编写的（同域不同源），泛化性未验证。",
            "相对时间全部按固定基准日 2026-09-16 推算 ⇒ 模型可能过拟合到 2026 年"
            "（表现为忽略 prompt 里的「今天」）。已混入含显式年份的绝对时间样本缓解，"
            "但不能消除。",
            "重要度 importance 的标注边界本身模糊（评测集里 E02 记 4、E17 记 5）⇒ 该字段的"
            "上限受标注一致性限制，不是模型问题（与 C32 的 Cohen's Kappa 同源）。",
            "`train.py` 目前是**整序列 loss**（未按 assistant 段做 mask）⇒ 模型也会学"
            "「生成通知正文」。抽取任务的更优做法是只对输出算 loss；本次先按原流水线跑，"
            "作为后续改进点记录在训练报告里。",
        ],
    }
    (out_dir / "extract_dataset.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"  切分：train {n_train} / dev {n_dev}（按类型分层）")
    print(f"  sha256：train {sha_train[:16]}… / dev {sha_dev[:16]}…")
    print(f"  system prompt：来自 {meta['system_prompt_source']}，"
          f"{len(system_prompt)} 字符，sha1={prompt_sha1[:12]}")
    print(f"  覆盖：类别 {stats['by_category']}")
    print(f"        重要度 {stats['by_importance']}（null deadline {stats['null_deadline_count']}）")
    print(f"        实体数 {stats['by_n_entities']}")
    print(f"  写出：{out_dir / 'extract_train.jsonl'}")
    print(f"        {out_dir / 'extract_dev.jsonl'}")
    print(f"        {out_dir / 'extract_dataset.json'}")
    print("  下一步：python ai/finetune/train.py --base_model <基座> "
          f"--data {out_dir.relative_to(REPO_ROOT).as_posix()}/extract_train.jsonl "
          "--output ai/finetune/out/xjt-extract-3b --epochs 3")
    return 0


if __name__ == "__main__":
    sys.exit(main())
