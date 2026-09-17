#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C35 低资源提示工程优化：prompt 模板 × 标注量的对比实验。

【为什么要做】
    C34 的 README 把话说明白了：zero-shot 只有 micro-F1 0.3043，**主要原因是 prompt 缺信息**
    （年份猜 2022 / importance 系统性偏低 / category 出现组合值），而不是模型抽取能力差；
    并且 C34 的 few-shot（+16.5pt）**不能全部算作"示例的功劳"** —— 那 4 条示例隐含了
    "用基准日年份""importance 判定标准"两条 zero-shot prompt 里没写的标准。

    ⇒ 本脚本做两件 C34 没做的事：
      ① **把标准写进 prompt**（而不是靠示例隐含），做一次干净的对照：同样 0 条示例，
         写好标准 vs 不写，差多少；
      ② 量化**标注预算**：把 prompt 与示例条数 k 做成二维网格，回答
         "优化 prompt 能不能用更少的示例达到同样效果"。

【指标】与 C34 **完全同一套**（直接 import 它的 `score_case` / `aggregate` / `parse_output`），
    不另起口径 —— 主指标 micro-F1，另报 macro-F1、严格匹配率、解析失败率。
    额外记录 **prompt_eval_count**（Ollama 实际吃进去的 prompt token 数）：
    k 变大时这个数必须跟着涨，否则说明示例被 `num_ctx` **静默截断**了
    （那样结论会变成"示例没用"，其实是根本没喂进去）。

【统计】n=24 的评测集上，几个百分点的差异可能只是抽样噪声，所以：
    · 对**同一条 case** 的两个配置做**配对自助法**（重采样 case，2000 次），
      给出 Δmicro-F1 的 95% 区间；区间跨 0 就说"不显著"，不硬讲成"提升"。

【用法】
    # 离线自检（不需要 Ollama，只验流程/评分/报告能跑通）
    python ai/eval/prompt_opt.py --backend scripted \\
        --scripted-answers ai/eval/fixtures/answers_upper_bound.json \\
        --variants V0-naive,V1-date --ks 0 --curve-ks 0,1 --out ai/eval/out/_smoke.json

    # 真实实验（需 Ollama；默认：4 档阶梯 @k=0 + V0/V3 的 k 曲线）
    python ai/eval/prompt_opt.py --model qwen2.5:3b --num-ctx 8192 \\
        --out ai/eval/out/prompt_opt.json

    # 生成可提交的精简基线（去掉逐条明细）
    python ai/eval/prompt_opt.py --model qwen2.5:3b --num-ctx 8192 --slim \\
        --out ai/eval/baselines/c35_prompt_opt_20260917.json

退出码：0 = 通过（优化后相对朴素 prompt 的提升达阈值）；1 = 未达标；2 = 环境/数据错误。

作者：成员3（C++ 数据层 / 模型微调 / 数据库）· C35
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import random
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parent.parent

DEFAULT_DATASET = EVAL_DIR / "extract_cases.json"
DEFAULT_BASE_POOL = EVAL_DIR / "fixtures" / "few_shot_examples.json"          # C34 的 4 条
DEFAULT_EXT_POOL = EVAL_DIR / "fixtures" / "prompt_opt_examples_ext.json"     # C35 新增的 12 条


def _load_sibling(name: str):
    """按路径加载同目录模块（理由同 tests/test_extract_bench.py：不依赖 ai/ 的包结构）。"""
    spec = importlib.util.spec_from_file_location(f"{name}_by_path", EVAL_DIR / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


eb = _load_sibling("extract_bench")
pv = _load_sibling("prompt_variants")


# ============================================================== 示例池 ====

def build_pool(k: int, *, base_pool: Path = DEFAULT_BASE_POOL,
               ext_pool: Path = DEFAULT_EXT_POOL) -> list[dict]:
    """取 k 条 few-shot 示例。

    k ≤ 4 时**完全等于 C34 的池**（`[:k]`），因此 `V0-naive` + `k=4` 就是 C34 那次
    few-shot 实验本身 —— 这是本脚本能不能复现 C34 数字的校验点（报告里的"复现校验"）。
    k > 4 时接上 C35 的扩充池。两池分文件存放，避免同一份示例出现两个副本各自漂移。
    """
    if k <= 0:
        return []
    base = json.loads(Path(base_pool).read_text(encoding="utf-8")).get("examples") or []
    if k <= len(base):
        return base[:k]
    ext = json.loads(Path(ext_pool).read_text(encoding="utf-8")).get("examples") or []
    out = list(base)
    out.extend(ext[: k - len(base)])
    return out[:k]


def pool_size_cap() -> int:
    """两个池子加起来能提供多少条示例。"""
    n1 = len(json.loads(DEFAULT_BASE_POOL.read_text(encoding="utf-8")).get("examples") or [])
    n2 = len(json.loads(DEFAULT_EXT_POOL.read_text(encoding="utf-8")).get("examples") or [])
    return n1 + n2


# ============================================================ 统计工具 ====

def _micro_counts(rows: list[dict]) -> tuple[int, int, int]:
    tp = fp = fn = 0
    for r in rows:
        for c in r["score"]["fields"].values():
            tp += c["tp"]
            fp += c["fp"]
            fn += c["fn"]
    return tp, fp, fn


def micro_f1_of(rows: list[dict]) -> float:
    """给定若干条 case 的 micro-F1。分母为 0 时按 0.0 处理（自助法里需要有定义的值）。"""
    tp, fp, fn = _micro_counts(rows)
    if (tp + fp) == 0 or (tp + fn) == 0:
        return 0.0
    p, r = tp / (tp + fp), tp / (tp + fn)
    return 0.0 if (p + r) == 0 else 2 * p * r / (p + r)


def paired_bootstrap_delta(rows_a: list[dict], rows_b: list[dict], *,
                           iters: int = 2000, seed: int = 42) -> dict:
    """配对自助法：Δmicro-F1 = B − A 的 95% 区间。

    配对的含义是"同一条 case 两边都跑过"，所以重采样的是 **case**（同一条 case 的两侧一起进/一起出），
    而不是对两组的分数各自独立重采样 —— 后者会把 case 难度差异算进噪声，区间虚宽。
    """
    by_id_a = {r["id"]: r for r in rows_a}
    by_id_b = {r["id"]: r for r in rows_b}
    ids = [i for i in by_id_a if i in by_id_b]
    if not ids:
        raise ValueError("两组没有共同的 case id，无法配对比较")
    if len(ids) != len(rows_a) or len(ids) != len(rows_b):
        # 明确失败而不是偷偷取交集：条数不一致通常意味着评测集/抽样变了，
        # 这时算出来的 Δ 不可比，必须让人看见。
        raise ValueError(f"两组 case 数不一致：A={len(rows_a)} B={len(rows_b)} 交集={len(ids)}")

    rng = random.Random(seed)
    deltas: list[float] = []
    n = len(ids)
    for _ in range(iters):
        sample = [ids[rng.randrange(n)] for _ in range(n)]
        a = micro_f1_of([by_id_a[i] for i in sample])
        b = micro_f1_of([by_id_b[i] for i in sample])
        deltas.append(b - a)
    deltas.sort()
    lo = deltas[max(0, int(0.025 * iters) - 1)]
    hi = deltas[min(iters - 1, int(0.975 * iters))]
    point = micro_f1_of(rows_b) - micro_f1_of(rows_a)
    return {
        "delta_pt": round(point * 100, 2),
        "ci_low_pt": round(lo * 100, 2),
        "ci_high_pt": round(hi * 100, 2),
        "iters": iters,
        "n_pairs": n,
        "significant": (lo > 0) or (hi < 0),
    }


def label_budget(records: list[dict], *, naive: str, optimized: str) -> dict:
    """标注预算：优化 prompt 用 0 条示例 ≈ 朴素 prompt 用多少条？

    朴素 prompt 的 F1(k) 随 k 的曲线，找**最小的 k** 使得 F1_naive(k) 追平
    优化 prompt 在 k=0 的成绩。追不上就如实说追不上（不外推、不编）。
    """
    def series(variant: str) -> dict[int, float]:
        out = {}
        for r in records:
            if r["variant"] == variant and r["metrics"]["micro"]["f1"] is not None:
                out[r["k_effective"]] = r["metrics"]["micro"]["f1"]
        return out

    naive_curve, opt_curve = series(naive), series(optimized)
    if 0 not in opt_curve:
        return {"available": False, "why": f"{optimized} 缺少 k=0 的结果"}
    target = opt_curve[0]
    matched = sorted(k for k, f1 in naive_curve.items() if f1 >= target)
    below = sorted(k for k, f1 in naive_curve.items() if f1 < target)
    # 夹逼区间：目标成绩落在哪两个 k 之间 —— 比只报一个 k 更诚实，
    # 因为 n=24 的曲线本就非单调（本次 k=2 → 0.5635、k=3 → 0.4693）
    return {
        "available": True,
        "optimized_k0_f1": round(target, 4),
        "naive_curve": {str(k): round(v, 4) for k, v in sorted(naive_curve.items())},
        "optimized_curve": {str(k): round(v, 4) for k, v in sorted(opt_curve.items())},
        "matched_k": matched[0] if matched else None,
        "bracket_below_k": max(below) if below else None,
        "bracket_above_k": min(matched) if matched else None,
        "max_k_tested": max(naive_curve) if naive_curve else None,
        "reached": bool(matched),
    }


# ============================================================== 跑配置 ====

def _config_key(variant: str, k: int) -> str:
    return f"{variant}@k={k}"


def build_grid(variants: list[str], ks: list[int],
               curve_variants: list[str], curve_ks: list[int]) -> list[tuple[str, int]]:
    """阶梯网格 + 曲线网格，去重且保持顺序（阶梯在前）。"""
    grid: list[tuple[str, int]] = [(v, k) for v in variants for k in ks]
    for v in curve_variants:
        for k in curve_ks:
            if (v, k) not in grid:
                grid.append((v, k))
    return grid


def run_config(variant: str, k: int, *, args, dataset: dict, cases: list[dict]) -> dict:
    """跑一个 (prompt 变体 × 示例条数) 配置，返回结果记录。"""
    examples = build_pool(k, base_pool=Path(args.base_pool), ext_pool=Path(args.ext_pool))
    if k > 0 and len(examples) < k:
        print(f"  ⚠️ 示例池只有 {len(examples)} 条，不足 k={k}（已达到池子上限）")

    system_prompt = pv.get_system_prompt(variant)
    if args.backend == "ollama":
        backend = eb.OllamaBackend(args.model, args.base_url, args.temperature,
                                   num_ctx=args.num_ctx, system_prompt=system_prompt)
        problem = backend.preflight()
        if problem:
            raise SystemExit(f"❌ {problem}")
    else:
        backend = eb.ScriptedBackend(Path(args.scripted_answers))

    sub_args = SimpleNamespace(
        mode="few-shot" if examples else "zero-shot",
        dataset=Path(args.dataset),
        temperature=args.temperature,
        few_shot_k=len(examples),
        few_shot_from=Path(args.base_pool) if len(examples) <= 4 else Path(args.ext_pool),
    )
    t0 = time.perf_counter()
    report = eb.run_mode(sub_args, dataset, cases, backend, examples)
    elapsed = time.perf_counter() - t0
    if args.backend == "ollama":
        backend.unload()      # 不卸载：下一个模型/配置会和它抢显存，推理慢十倍以上

    report.update({
        "variant": variant,
        "prompt_added": pv.added_summary(variant),
        "k_requested": k,
        "k_effective": len(examples),
        "example_ids": [e["id"] for e in examples],
        "config": _config_key(variant, k),
        "wall_time_s": round(elapsed, 1),
    })
    return report


# ============================================================== 报告 ====

def render_markdown(result: dict) -> str:
    meta = result["meta"]
    lines = [
        "# C35 低资源提示工程优化 · prompt 模板 × 标注量",
        "",
        f"- 模型：`{meta['model']}` · 后端：`{meta['backend']}` · `num_ctx={meta['num_ctx']}`"
        f" · temperature={meta['temperature']} · seed={meta['seed']}",
        f"- 评测集：`{meta['dataset']}`（{meta['dataset_total']} 条，本次全部使用）",
        f"- 示例池：C34 `{meta['pool']['base']}`（{meta['pool']['base_size']} 条）"
        f" + C35 `{meta['pool']['ext']}`（{meta['pool']['ext_size']} 条）",
        "",
        "## 一、prompt 阶梯（每档只补一条 C34 已定位的缺口）",
        "",
        "| 档位 | 在 V0 之上补了什么 | prompt 字符数 | micro-F1 @k=0 | Δ vs V0 |",
        "|---|---|---|---|---|",
    ]
    ladder = result.get("ladder", [])
    base_f1 = None
    for row in ladder:
        if row["variant"] == "V0-naive":
            base_f1 = row["metrics"]["micro"]["f1"]
    for row in ladder:
        f1 = row["metrics"]["micro"]["f1"]
        delta = "-" if base_f1 is None or f1 is None else f"{round((f1 - base_f1) * 100, 2):+}"
        lines.append(f"| `{row['variant']}` | {row['prompt_added']} | {row['prompt_chars']} | {f1} | {delta} |")

    lines += ["", "## 二、prompt × 示例条数", "",
              "| 配置 | k | micro-F1 | macro-F1 | 严格匹配 | 解析失败 | prompt token（均值） | 平均耗时 s |",
              "|---|---|---|---|---|---|---|---|"]
    for r in result["configs"]:
        m = r["metrics"]
        lines.append(
            f"| `{r['variant']}` | {r['k_effective']} | **{m['micro']['f1']}** | {m['macro_f1']} "
            f"| {m['strict_accuracy']} | {r['parse_errors']} | {r.get('prompt_tokens_avg')} "
            f"| {r['avg_latency_s']} |"
        )

    if result.get("comparisons"):
        lines += ["", "## 三、配对自助法（Δ 的 95% 区间，2000 次重采样同一条 case）", "",
                  "| 对比 | Δ（百分点） | 95% 区间 | 是否显著 |", "|---|---|---|---|"]
        for c in result["comparisons"]:
            sig = "✅ 显著" if c["significant"] else "— 不显著（区间跨 0）"
            lines.append(f"| `{c['b']}` − `{c['a']}` | {c['delta_pt']:+} "
                         f"| [{c['ci_low_pt']:+}, {c['ci_high_pt']:+}] | {sig} |")

    lb = result.get("label_budget", {})
    if lb.get("available"):
        lines += ["", "## 四、标注预算", "",
                  f"- 优化 prompt 在 **k=0**（零示例）的成绩：micro-F1 **{lb['optimized_k0_f1']}**",
                  f"- 朴素 prompt 曲线：{lb['naive_curve']}",
                  f"- 优化 prompt 曲线：{lb['optimized_curve']}"]
        if lb["reached"]:
            lines.append(f"- ⇒ 朴素 prompt 需要 **k={lb['matched_k']}** 条示例才追平"
                         f"（优化 prompt 用 0 条）⇒ 省下 **{lb['matched_k']} 条标注**")
            if lb.get("bracket_above_k") is not None:
                lines.append(f"- 曲线非单调（n=24 的噪声），更准确的说法：优化 prompt 的 k=0 成绩"
                             f"落在朴素 prompt 的 **k={lb['bracket_below_k']} 与 k={lb['bracket_above_k']} 之间**"
                             f" —— 量级上相当于省下几条示例的标注")
        else:
            lines.append(f"- ⇒ 在本次测到的最大 k={lb['max_k_tested']} 内，朴素 prompt "
                         f"**仍未追平**优化 prompt 的 k=0 成绩（如实报告，不外推）")

    if result.get("gate"):
        g = result["gate"]
        lines += ["", "## 五、门禁", "",
                  f"- {g['expr']}：Δ = {g['delta_pt']:+} 个百分点（阈值 {g['min_gain_pt']}）→ "
                  f"{'✅ 通过' if g['passed'] else '❌ 未通过'}"]

    if result.get("reproduce"):
        rp = result["reproduce"]
        lines += ["", "## 六、复现校验（本脚本 vs C34 已发布数字）", "",
                  f"- C34 README 记录 few-shot micro-F1 = {rp['c34_published_f1']}"
                  f"（`qwen2.5:3b`，其 `--few-shot-k` 默认 3）",
                  f"- 本脚本 `V0-naive` @ k={rp['k']}、num_ctx={rp['our_num_ctx']} 实测 = {rp['our_f1']}",
                  f"- ⇒ {'✅ 一致（同口径复现成功）' if rp['matched'] else '⚠️ 不一致：先查清原因再看其它结论'}"]

    if result.get("caveats"):
        lines += ["", "## 七、诚实边界", ""]
        lines += [f"- {c}" for c in result["caveats"]]
    return "\n".join(lines) + "\n"


def _slim_configs(configs: list[dict]) -> list[dict]:
    """去掉逐条明细，只留指标（提交进仓库的基线文件要能看）。"""
    out = []
    for c in configs:
        slim = {k: v for k, v in c.items() if k not in ("cases", "failures")}
        out.append(slim)
    return out


def detect_truncation(records: list[dict]) -> list[str]:
    """检出“k 变大但 prompt token 没涨”的配置。

    为什么一定要查：`num_ctx` 不够时 Ollama **静默截断**，示例被丢掉而指标不掉、
    也不报错 —— 结论会变成“给示例没用”，而真实原因是示例根本没喂进去。
    这是本次实验最容易被悄悄搞砸的地方，所以单独成一个可单测的函数。
    """
    warnings: list[str] = []
    for variant in sorted({r["variant"] for r in records}):
        series = sorted((r for r in records if r["variant"] == variant),
                        key=lambda r: r["k_effective"])
        toks = [r.get("prompt_tokens_avg") or 0 for r in series]
        if len(series) >= 2 and toks and max(toks) > 0 and toks[-1] <= toks[0]:
            warnings.append(
                f"`{variant}`：k 从 {series[0]['k_effective']} 加到 {series[-1]['k_effective']}，"
                f"prompt token 却从 {toks[0]} 变成 {toks[-1]} —— 疑似被 num_ctx 截断，"
                f"示例可能没真的喂进去"
            )
    return warnings


# ================================================================ 主流程 ====

def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 GBK：报告里有 U+2212（减号）之类字符，直接 print 会 UnicodeEncodeError。
    # 更坑的是它发生在**写文件之前**，整轮 GPU 跑出来的结果会白丢 ——
    # 所以这里两处一起修：输出流可容错 + 先落盘再打印（见文件末尾）。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - 非 Windows/已重定向
        pass

    ap = argparse.ArgumentParser(
        prog="python ai/eval/prompt_opt.py",
        description="C35 低资源提示工程优化：prompt 模板 × 标注量对比实验",
    )
    ap.add_argument("--dataset", default=str(DEFAULT_DATASET))
    ap.add_argument("--backend", choices=["ollama", "scripted"], default="ollama")
    ap.add_argument("--model", default="qwen2.5:3b")
    ap.add_argument("--base-url", default="http://127.0.0.1:11434")
    ap.add_argument("--scripted-answers", default="",
                    help="scripted 后端的答案文件（离线跑通流程用）")
    ap.add_argument("--variants", default=",".join(pv.VARIANTS),
                    help="阶梯网格的 prompt 变体（逗号分隔）")
    ap.add_argument("--ks", default="0", help="阶梯网格的示例条数（逗号分隔）")
    ap.add_argument("--curve-variants", default="V0-naive,V3-optimized",
                    help="额外做 k 曲线的变体")
    ap.add_argument("--curve-ks", default="0,1,2,3,4,8,16",
                    help="k 曲线的示例条数（超出示例池的部分会按池子上限截断）")
    ap.add_argument("--num-ctx", type=int, default=8192,
                    help="上下文窗口（默认 8192；C34 默认 2048 会被 k 条示例撑爆并静默截断）")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--limit", type=int, default=0, help="0 = 全部 24 条")
    ap.add_argument("--iters", type=int, default=2000, help="自助法重采样次数")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--min-gain-pt", type=float, default=5.0,
                    help="门禁：V3-optimized@k=0 相对 V0-naive@k=0 的最小提升（百分点）")
    ap.add_argument("--base-pool", default=str(DEFAULT_BASE_POOL))
    ap.add_argument("--ext-pool", default=str(DEFAULT_EXT_POOL))
    ap.add_argument("--reproduce-f1", type=float, default=0.4693,
                    help="C34 README 记录的 few-shot micro-F1，用于复现校验")
    ap.add_argument("--reproduce-k", type=int, default=3,
                    help="C34 那次 few-shot 的示例条数（extract_bench 的 --few-shot-k 默认 3）")
    ap.add_argument("--slim", action="store_true", help="输出不含逐条明细")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    dataset_path = Path(args.dataset)
    if not dataset_path.is_absolute():
        dataset_path = REPO_ROOT / dataset_path
    if not dataset_path.exists():
        print(f"❌ 评测集不存在：{dataset_path}")
        return 2
    args.dataset = dataset_path
    args.base_pool = Path(args.base_pool)
    args.ext_pool = Path(args.ext_pool)

    dataset = eb.load_dataset(dataset_path)
    cases = eb.stratified_sample(dataset["cases"], args.limit)
    ref = dataset.get("reference_date")
    if ref and ref != pv.REFERENCE_DATE:
        # 基准日变了，"本周五"这类相对时间的期望值就不再成立 —— 必须报错而不是照跑
        print(f"❌ 评测集 reference_date={ref} 与 prompt 变体里的 {pv.REFERENCE_DATE} 不一致")
        return 2

    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    unknown = [v for v in variants if v not in pv.VARIANTS]
    if unknown:
        print(f"❌ 未知变体 {unknown}（可选：{', '.join(pv.VARIANTS)}）")
        return 2
    ks = [int(x) for x in args.ks.split(",") if x.strip()]
    curve_variants = [v.strip() for v in args.curve_variants.split(",") if v.strip()]
    curve_ks = [int(x) for x in args.curve_ks.split(",") if x.strip()]

    if args.backend == "ollama":
        print(f"[C35] 评测集 {len(cases)} 条 · 模型 `{args.model}` · num_ctx={args.num_ctx}")
    else:
        if not args.scripted_answers:
            print("❌ scripted 后端需要 --scripted-answers")
            return 2
        print(f"[C35] 离线自检（scripted）：评测集 {len(cases)} 条")

    grid = build_grid(variants, ks, curve_variants, curve_ks)
    cap = pool_size_cap()
    print(f"[C35] 示例池上限 {cap} 条（C34 4 + C35 12）· 共 {len(grid)} 个配置")

    records: list[dict] = []
    started = time.perf_counter()
    for i, (variant, k) in enumerate(grid, 1):
        print(f"[{i}/{len(grid)}] {_config_key(variant, k)} ...", flush=True)
        try:
            rec = run_config(variant, k, args=args, dataset=dataset, cases=cases)
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001 - 单个配置失败不该丢掉已跑完的结果
            print(f"  ❌ 配置失败：{type(exc).__name__}: {exc}")
            return 2
        m = rec["metrics"]
        print(f"  micro-F1={m['micro']['f1']} macro={m['macro_f1']} "
              f"strict={m['strict_accuracy']} 解析失败={rec['parse_errors']} "
              f"prompt_tokens≈{rec.get('prompt_tokens_avg')} {rec['wall_time_s']}s")
        records.append(rec)

    # k 增大时 prompt token 必须跟着涨：不涨 = 示例被静默截断，结论不成立
    truncation_warnings = detect_truncation(records)

    ladder = [r for r in records if r["k_effective"] == 0 and r["variant"] in variants]
    ladder.sort(key=lambda r: pv.VARIANTS.index(r["variant"]))
    for row in ladder:
        row["prompt_chars"] = len(pv.get_system_prompt(row["variant"]))

    # 配对比较：同 k 下优化 vs 朴素
    by_key = {(r["variant"], r["k_effective"]): r for r in records}
    comparisons: list[dict] = []
    for k in sorted({r["k_effective"] for r in records}):
        a = by_key.get(("V0-naive", k))
        b = by_key.get(("V3-optimized", k))
        if not (a and b):
            continue
        try:
            cmp_ = paired_bootstrap_delta(a["cases"], b["cases"],
                                          iters=args.iters, seed=args.seed)
        except ValueError as exc:
            print(f"  ⚠️ k={k} 配对比较跳过：{exc}")
            continue
        comparisons.append({"a": f"V0-naive@k={k}", "b": f"V3-optimized@k={k}",
                            "k": k, **cmp_})

    gate = None
    if ("V0-naive", 0) in by_key and ("V3-optimized", 0) in by_key:
        f0 = by_key[("V0-naive", 0)]["metrics"]["micro"]["f1"]
        f3 = by_key[("V3-optimized", 0)]["metrics"]["micro"]["f1"]
        d = round((f3 - f0) * 100, 2) if (f0 is not None and f3 is not None) else None
        gate = {"expr": "V3-optimized@k=0 − V0-naive@k=0", "delta_pt": d,
                "min_gain_pt": args.min_gain_pt,
                "passed": d is not None and d >= args.min_gain_pt}

    labels = label_budget(records, naive="V0-naive", optimized="V3-optimized")

    reproduce = None
    repro_cfg = None
    for r in records:
        if r["variant"] == "V0-naive" and r["k_effective"] == args.reproduce_k:
            repro_cfg = r
    if repro_cfg and repro_cfg["metrics"]["micro"]["f1"] is not None:
        ours = repro_cfg["metrics"]["micro"]["f1"]
        reproduce = {"k": args.reproduce_k, "c34_published_f1": args.reproduce_f1,
                     "our_f1": ours, "our_num_ctx": args.num_ctx,
                     "matched": abs(ours - args.reproduce_f1) < 0.001}

    caveats = [
        "评测集只有 24 条且是**合成数据**（C34 已注明）：几个百分点的差异可能只是抽样噪声，"
        "所以给出了配对自助法的 95% 区间；区间跨 0 的结论一律按「不显著」处理。",
        "`V3-optimized` 里的 category 受控词表取的是评测集实际使用的 5 类。真实部署里类目来自"
        "库表/前端栏目（属正当先验），但它确实缩小了模型的搜索空间 —— 该档增益含这一条，不与"
        "「日期规则」「重要度细则」混算。",
        "C35 的扩充示例（12 条）与 prompt 文本都是按 C34 记录的**标注规范**编写的，"
        "**不是**在评测集上反复调参拟合出来的；prompt 与示例一经确定即冻结，"
        "评测集只用于最终度量。",
        "任务书里「100 条+优 prompt ≈ 300 条+朴素」的完整曲线需要**真实标注数据**"
        "（C31 工具链 + D12 人标注协作产出）才能测到 k=100/300；本实验在现有池子"
        f"（上限 {cap} 条）内给出可测段，并如实报告是否在测到的 k 上追平，**不外推**。",
        "`importance` 的标注本身存在边界模糊（例如同为「提交材料截止」，评测集里 "
        "E02 记 4、E17 记 5）：这属于标注一致性问题（与 C32 的 Cohen's Kappa 同源），"
        "会同时压低所有档位的上限，不是 prompt 能修的。",
    ]
    if truncation_warnings:
        caveats.insert(0, "⚠️ 检出疑似截断：" + "；".join(truncation_warnings))

    result = {
        "meta": {
            "task": "C35",
            "created": time.strftime("%Y-%m-%d"),
            "backend": args.backend,
            "model": args.model if args.backend == "ollama" else None,
            "num_ctx": args.num_ctx,
            "temperature": args.temperature,
            "seed": args.seed,
            "iters": args.iters,
            "dataset": eb._rel(dataset_path),
            "dataset_total": len(dataset["cases"]),
            "n_cases": len(cases),
            "pool": {"base": eb._rel(args.base_pool), "ext": eb._rel(args.ext_pool),
                     "base_size": len(json.loads(Path(args.base_pool).read_text(encoding="utf-8"))
                                      .get("examples") or []),
                     "ext_size": len(json.loads(Path(args.ext_pool).read_text(encoding="utf-8"))
                                     .get("examples") or []),
                     "cap": cap},
            "prompt_variants": pv.ablation_rows(),
            "grid": [f"{v}@k={k}" for v, k in grid],
            "total_wall_time_s": round(time.perf_counter() - started, 1),
        },
        "ladder": _slim_configs(ladder) if args.slim else ladder,
        "configs": _slim_configs(records) if args.slim else records,
        "comparisons": comparisons,
        "label_budget": labels,
        "gate": gate,
        "reproduce": reproduce,
        "caveats": caveats,
    }

    md = render_markdown(result)

    # 【先落盘再打印】控制台编码/重定向出问题时，不能让整轮 GPU 结果丢掉
    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = REPO_ROOT / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        out_path.with_suffix(".md").write_text(md, encoding="utf-8")
        print(f"\n[C35] JSON -> {eb._rel(out_path)}")
        print(f"[C35] Markdown -> {eb._rel(out_path.with_suffix('.md'))}")

    print(md)
    return 0 if (gate is None or gate["passed"]) else 1


if __name__ == "__main__":
    sys.exit(main())
