#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""信息抽取基线对比实验框架（C34）。

【为什么要做】
    微调（C33）之后必须回答一个能被追问的问题：**到底提升了多少**。
    任务书的验收口径是「对比零样本 / few-shot / 微调，**F1 提升 ≥ 15 个百分点**」。
    15 个百分点只有在**同一评测集 + 同一评分函数**下纵向可比才有意义 ——
    否则数字会被换数据集、换评分口径污染。

    因此本脚本的设计原则是：**把评测集和评分函数钉死，只让 prompt 和模型变**。

【三种对照（任务书原定）】
    ① zero-shot   仅任务说明 + 输出格式约定
    ② few-shot     额外给 k 条「文本 → JSON」示例（从训练集抽，不进评测集）
    ③ finetuned    与 ① 相同的 prompt，模型换成微调版本
       —— 只有 prompt 完全相同，才能把差异归因到「微调」而不是「提示更好」

    另外可选 ④ rule(C27 规则 baseline)。规则抽取完全离线、无随机性，
    是「不用模型能做到什么程度」的参照，也是 F1 是否可信的旁证。

【指标】
    · 字段级 P/R/F1：category / importance / deadline
    · 实体级 P/R/F1：集合比对，键 = (type, norm or text)
    · **micro-F1**：所有字段的 TP/FP/FN 汇总后再算（主指标，验收看它）
    · macro-F1  ：各字段 F1 的算术平均（防止单字段刷分）
    · 严格匹配率：整条 JSON 完全正确的比例（最严口径）
    · JSON 解析失败率：模型没按要求输出 JSON 的比例（独立统计，不混进 F1）

【为什么 micro 和 macro 都要报】
    如果只报 micro，模型在样本多的字段上答好、在样本少的字段上全错，总分照样很高。
    两个口径差得远，说明模型能力**不均衡** —— 这是微调数据分布有问题，
    不是「模型很好只是个别 case 没答对」。

【用法】
    离线自测（不需要 Ollama，用预置答案跑通全流程）：
        python ai/eval/extract_bench.py --backend scripted \\
            --scripted-answers ai/eval/fixtures/answers_zero_shot.json \\
            --out ai/eval/out/extract_zero_shot.json

    真实评测（需 Ollama 已启动且模型可用）：
        python ai/eval/extract_bench.py --backend ollama --model qwen2.5:3b \\
            --modes zero-shot,few-shot --limit 40 \\
            --out ai/eval/out/extract_base.json

        # 微调模型（与上面的 prompt 完全一致，只换模型）
        python ai/eval/extract_bench.py --backend ollama --model xjt-3b \\
            --modes finetuned --limit 40 \\
            --out ai/eval/out/extract_finetuned.json

        # 与基线对比（ΔF1 < 阈值时退出码 1，可直接做 CI 门禁）
        python ai/eval/extract_bench.py --backend ollama --model xjt-3b \\
            --modes finetuned --compare ai/eval/out/extract_base.json

选项：
    --dataset      评测集（默认 ai/eval/extract_cases.json）
    --backend      scripted / ollama（默认 ollama）
    --model        模型名（ollama 后端）
    --modes        逗号分隔，默认 zero-shot
    --few-shot-k   few-shot 示例条数（默认 3）
    --few-shot-from 示例来源（默认 ai/finetune/data/seed_qa.jsonl 之外时用数据集自身 train 部分）
    --limit        抽样条数（按 category 分层抽样，保证覆盖面）
    --temperature  采样温度（默认 0.0，评测必须可复现）
    --out          JSON 结果输出路径
    --compare      基线 JSON，计算 Δ 并判定是否达标
    --threshold    达标阈值（百分点，默认 15.0）
    --base-url     Ollama 地址（默认 http://127.0.0.1:11434）

退出码：0 = 达标（或没给 --compare）；1 = 未达标；2 = 环境/数据错误。

作者：成员3（C++ 数据层 / 模型微调 / 数据库）· C34
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parent.parent
DEFAULT_DATASET = EVAL_DIR / "extract_cases.json"
DEFAULT_OUT_DIR = EVAL_DIR / "out"

# 抽取的字段与口径（与 C32 质检保持同一套，避免两处口径打架）
FIELDS = ("category", "importance", "deadline")


def _rel(path) -> str:
    """尽量转成仓库内相对路径。

    报告是要提交进仓库、给别人看的。写 D:\\...\\worktree\\... 这种本地绝对路径，
    别人打开报告只会看到自己的目录不存在 —— 也无法据此定位到评测集。
    """
    try:
        return Path(path).resolve().relative_to(REPO_ROOT).as_posix()
    except (ValueError, OSError):
        return str(path)


# 尽力从"模型输出的一坨文本"里抠出 JSON —— 评测必须容错，
# 否则模型只是多写了一句解释就被判全错，F1 会被非能力因素拉低。
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


# ------------------------------------------------------------------ 数据 ----

def load_dataset(path: Path) -> dict:
    """读评测集。结构与 `rag_questions.json` 保持一致（见 ai/eval/README.md）。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases") or []
    if not cases:
        raise SystemExit(f"❌ 评测集为空：{path}")
    for c in cases:
        if not c.get("id") or "text" not in c or "expected" not in c:
            raise SystemExit(f"❌ 评测集条目缺字段（需 id/text/expected）：{c!r}")
    return data


def stratified_sample(cases: list[dict], limit: int) -> list[dict]:
    """按 category 轮转抽样，保证每个类别都被覆盖（不是随机截断前 N 条）。"""
    if limit <= 0 or limit >= len(cases):
        return list(cases)
    buckets: dict[str, list[dict]] = defaultdict(list)
    for c in cases:
        buckets[c.get("category", "综合")].append(c)

    out: list[dict] = []
    idx = 0
    while len(out) < limit:
        progressed = False
        for bucket in buckets.values():
            if idx < len(bucket) and len(out) < limit:
                out.append(bucket[idx])
                progressed = True
        if not progressed:
            break
        idx += 1
    return out


def build_few_shot_examples(cases: list[dict], k: int, exclude_ids: set[str]) -> list[dict]:
    """从评测集里挑 k 条**不在本次评测范围**的样本作为 few-shot 示例。

    ⚠️ 必须排除评测样本本身，否则是把答案直接喂给模型 —— F1 会虚高，
    这种"作弊"在答辩时一问就穿帮。
    """
    pool = [c for c in cases if c["id"] not in exclude_ids]
    return pool[:k] if k > 0 else []


# ------------------------------------------------------------ 提示构建 ----

SCHEMA_HINT = """{
  "category": "通知类别（如 奖学金/活动/通知/讲座/竞赛）",
  "importance": 3,
  "deadline": "YYYY-MM-DD 或 null",
  "entities": [{"type": "time", "text": "原文片段", "norm": "YYYY-MM-DD"}]
}"""

SYSTEM_PROMPT = (
    "你是校园公告信息抽取器。只输出 JSON，不要任何解释、不要 markdown 代码块。"
    f"\n输出格式：\n{SCHEMA_HINT}"
)


def build_prompt(text: str, mode: str, examples: list[dict] | None = None) -> str:
    """按模式拼 prompt。

    `finetuned` 与 `zero-shot` **必须拼出完全相同的字符串** ——
    这是把提升归因给「微调」而非「提示」的前提。
    """
    parts: list[str] = []
    if mode == "few-shot" and examples:
        parts.append("以下是若干示例：")
        for ex in examples:
            payload = json.dumps(ex["expected"], ensure_ascii=False)
            parts.append(f"输入：{ex['text']}\n输出：{payload}")
        parts.append("")
    parts.append(f"请抽取以下公告的信息：\n{text}")
    return "\n".join(parts)


# -------------------------------------------------------------- 后处理 ----

def parse_output(raw: str) -> tuple[dict | None, str]:
    """把模型输出解析成 dict。返回 (结果, 错误原因)。

    容错顺序：整体 JSON → ```json 代码块 → 文本里第一个 {...}。
    三级都失败才算解析错误 —— 但**不把解析失败当 F1 的 0**，
    而是单独统计，因为"格式没守住"和"内容抽错"是两类问题。
    """
    if not raw or not raw.strip():
        return None, "空输出"
    for candidate in (raw, *(m.strip() for m in _FENCE_RE.findall(raw))):
        try:
            obj = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(obj, dict):
            return obj, ""

    m = _OBJ_RE.search(raw)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj, ""
        except json.JSONDecodeError:
            pass
    return None, "未解析出 JSON"


def normalize_deadline(value) -> str | None:
    """只取日期部分。

    与 C32 的 `normalize_deadline` 同一口径：`2026-09-30 23:59:59` 与
    `2026-09-30` 应视为**同一个答案**，否则 F1 会被格式差异污染 ——
    那样测的是"会不会写零点"而不是"有没有抽对时间"。
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in ("null", "none", "无"):
        return None
    m = re.match(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return s


def entity_set(value) -> set[tuple[str, str]]:
    """把 entities 归一成可比较的集合。键 = (type, norm or text)。"""
    out: set[tuple[str, str]] = set()
    if not isinstance(value, list):
        return out
    for item in value:
        if not isinstance(item, dict):
            continue
        etype = str(item.get("type", "")).strip()
        key = item.get("norm") or item.get("text") or ""
        key = str(key).strip()
        if etype and key:
            out.add((etype, key))
    return out


def normalize_field(field: str, value):
    """字段归一化 —— 只有 deadline 需要（其余精确比对）。"""
    if field == "deadline":
        return normalize_deadline(value)
    if field == "importance":
        if value is None or str(value).strip() == "":
            return None
        try:
            return int(str(value).strip())
        except ValueError:
            return str(value).strip()
    if value is None:
        return None
    return str(value).strip()


# -------------------------------------------------------------- 评分 ----

def score_case(pred: dict | None, expected: dict) -> dict:
    """对单条样本算 TP/FP/FN。

    规则：把每个字段的答案看成一次"预测-真值"配对。
      · 预测正确 → TP
      · 预测了但错 → FP（并且真值未命中 → FN）
      · 没预测（缺字段或 null）而真值非空 → FN（漏抽）
      · 都没值 → 双方一致，不产生 TP/FP/FN（不计入分母）
    """
    result: dict = {"fields": {}, "strict": False}
    if pred is None:
        # 解析失败：所有非空真值字段都算漏抽（FN），不产生 FP
        for f in FIELDS:
            if normalize_field(f, expected.get(f)) is not None:
                result["fields"][f] = {"tp": 0, "fp": 0, "fn": 1}
        if entity_set(expected.get("entities")):
            result["fields"]["entities"] = {"tp": 0, "fp": 0, "fn": 1}
        return result

    all_ok = True
    for f in FIELDS:
        want = normalize_field(f, expected.get(f))
        got = normalize_field(f, pred.get(f))
        if want is None and got is None:
            result["fields"][f] = {"tp": 0, "fp": 0, "fn": 0}
            continue
        if want is not None and got is not None and want == got:
            result["fields"][f] = {"tp": 1, "fp": 0, "fn": 0}
        elif want is None and got is not None:
            # 真值没有、模型硬编了一个 → 过度抽取
            result["fields"][f] = {"tp": 0, "fp": 1, "fn": 0}
            all_ok = False
        elif want is not None and got is None:
            result["fields"][f] = {"tp": 0, "fp": 0, "fn": 1}
            all_ok = False
        else:
            result["fields"][f] = {"tp": 0, "fp": 1, "fn": 1}
            all_ok = False

    want_e = entity_set(expected.get("entities"))
    got_e = entity_set(pred.get("entities"))
    if want_e or got_e:
        result["fields"]["entities"] = {
            "tp": len(want_e & got_e),
            "fp": len(got_e - want_e),
            "fn": len(want_e - got_e),
        }
        if want_e != got_e:
            all_ok = False

    result["strict"] = all_ok
    return result


def prf(tp: int, fp: int, fn: int) -> dict:
    """标准 P/R/F1；分母为 0 时返回 None（**不是 0** —— 无样本≠全错）。"""
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    if precision is None or recall is None or (precision + recall) == 0:
        f1 = 0.0 if (tp or fp or fn) else None
    else:
        f1 = 2 * precision * recall / (precision + recall)
    return {"precision": precision, "recall": recall, "f1": f1, "tp": tp, "fp": fp, "fn": fn}


def aggregate(rows: list[dict]) -> dict:
    """汇总所有样本 → 逐字段 + micro/macro。"""
    totals: dict[str, dict[str, int]] = {
        f: {"tp": 0, "fp": 0, "fn": 0} for f in (*FIELDS, "entities")
    }
    strict_hits = 0
    for row in rows:
        if row["score"]["strict"]:
            strict_hits += 1
        for fname, c in row["score"]["fields"].items():
            for k in ("tp", "fp", "fn"):
                totals[fname][k] += c[k]

    per_field = {f: prf(**totals[f]) for f in totals}
    summed = {k: sum(totals[f][k] for f in totals) for k in ("tp", "fp", "fn")}
    micro = prf(**summed)
    f1s = [v["f1"] for v in per_field.values() if v["f1"] is not None]
    return {
        "per_field": per_field,
        "micro": micro,
        "macro_f1": round(statistics.fmean(f1s), 4) if f1s else None,
        "strict_accuracy": round(strict_hits / len(rows), 4) if rows else None,
        "n": len(rows),
    }


def render_markdown(report: dict) -> str:
    lines = [
        f"# 信息抽取基线对比（C34）· 模式 `{report['mode']}`",
        "",
        f"- 后端：`{report['backend']}` · 模型：`{report['model'] or '-'}`",
        f"- 评测集：`{report['dataset']}`（本次 {report['metrics']['n']} 条，全部 {report['dataset_total']} 条）",
        f"- 温度：{report['temperature']}（0 = 可复现）",
        "",
        "## 逐字段指标",
        "",
        "| 字段 | P | R | F1 | TP | FP | FN |",
        "|---|---|---|---|---|---|---|",
    ]
    for fname, m in report["metrics"]["per_field"].items():
        fmt = lambda v: "-" if v is None else f"{v:.4f}"  # noqa: E731
        lines.append(
            f"| `{fname}` | {fmt(m['precision'])} | {fmt(m['recall'])} | {fmt(m['f1'])} "
            f"| {m['tp']} | {m['fp']} | {m['fn']} |"
        )
    m = report["metrics"]
    lines += [
        "",
        "## 总体",
        "",
        f"- **micro-F1：{m['micro']['f1']}**（P {m['micro']['precision']} / R {m['micro']['recall']}）"
        " ← 验收看这个",
        f"- macro-F1：{m['macro_f1']}",
        f"- 严格匹配率：{m['strict_accuracy']}（整条 JSON 全对）",
        f"- JSON 解析失败率：{report['parse_error_rate']}（{report['parse_errors']} 条）",
        f"- 平均耗时：{report['avg_latency_s']} s/条",
        "",
    ]
    if report.get("comparison"):
        c = report["comparison"]
        lines += [
            "## 与基线对比",
            "",
            f"- 基线：`{c['baseline_path']}`（micro-F1 {c['baseline_f1']}）",
            f"- 本次：{c['current_f1']}",
            f"- **Δ = {c['delta_pt']} 个百分点**（阈值 {c['threshold_pt']}）",
            f"- 结论：{'✅ 达标' if c['passed'] else '❌ 未达标'}",
            "",
        ]
    if report.get("failures"):
        lines += ["## 抽错的样例（前 10 条）", ""]
        for f in report["failures"][:10]:
            lines.append(f"- `{f['id']}`（{f['reason']}）")
            lines.append(f"  - 期望：`{json.dumps(f['expected'], ensure_ascii=False)}`")
            lines.append(f"  - 预测：`{json.dumps(f['predicted'], ensure_ascii=False)}`")
        lines.append("")
    return "\n".join(lines)


# -------------------------------------------------------------- 后端 ----

class ScriptedBackend:
    """离线后端：从预置文件读答案。

    存在的意义：让**评分逻辑本身**可以被测试。
    否则所有测试都必须依赖 Ollama 在线，CI 跑不了，评分函数也就没人敢改。
    """

    name = "scripted"

    def __init__(self, answers_path: Path):
        self.answers_path = answers_path
        data = json.loads(answers_path.read_text(encoding="utf-8"))
        self.answers: dict[str, str] = data.get("answers") or {}

    def generate(self, case: dict, prompt: str) -> tuple[str, float, str]:  # noqa: ARG002
        if case["id"] not in self.answers:
            return "", 0.0, f"预置答案缺少 {case['id']}"
        return self.answers[case["id"]], 0.0, ""


class OllamaBackend:
    """真实后端：调 Ollama /api/chat。"""

    name = "ollama"

    def __init__(self, model: str, base_url: str, temperature: float, timeout: int = 180):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.timeout = timeout

    def generate(self, case: dict, prompt: str) -> tuple[str, float, str]:  # noqa: ARG002
        payload = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            # 评测要可复现：temperature=0 + 固定 seed
            "options": {"temperature": self.temperature, "seed": 42, "num_ctx": 2048},
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/chat", data=payload,
            headers={"Content-Type": "application/json"})
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            return "", time.perf_counter() - t0, str(exc)
        return data.get("message", {}).get("content", ""), time.perf_counter() - t0, ""

    def preflight(self) -> str:
        """开跑前确认模型在不在 —— 否则每条都超时，浪费几十分钟才发现模型名写错。"""
        req = urllib.request.Request(f"{self.base_url}/api/tags")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                tags = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            return f"Ollama 不可达（{self.base_url}）：{exc}"
        names = [m.get("name", "") for m in tags.get("models", [])]
        if not any(n == self.model or n.startswith(f"{self.model}:") for n in names):
            return f"模型 `{self.model}` 不在 Ollama 中。已安装：{names}"
        return ""


# ---------------------------------------------------------------- 主流程 ----

def run_mode(args, dataset: dict, cases: list[dict], backend) -> dict:
    """跑一种模式，返回报告 dict。"""
    exclude = {c["id"] for c in cases}
    examples = build_few_shot_examples(dataset["cases"], args.few_shot_k, exclude) \
        if args.mode == "few-shot" else None

    rows: list[dict] = []
    parse_errors = 0
    latencies: list[float] = []
    failures: list[dict] = []
    prompt_mismatch = 0

    for i, case in enumerate(cases, 1):
        prompt = build_prompt(case["text"], args.mode, examples)
        raw, latency, err = backend.generate(case, prompt)
        if err:
            print(f"  [{i}/{len(cases)}] {case['id']} ⚠️  {err}")
        parsed, perr = parse_output(raw)
        if parsed is None:
            parse_errors += 1
        latencies.append(latency)

        score = score_case(parsed, case["expected"])
        rows.append({"id": case["id"], "raw": raw, "parsed": parsed, "score": score})
        if not score["strict"] and len(failures) < 50:
            failures.append({
                "id": case["id"],
                "reason": perr or "字段不一致",
                "expected": case["expected"],
                "predicted": parsed,
            })

    # 自检：zero-shot 与 finetuned 的 prompt 必须逐字节相同（否则提升不可归因）
    if args.mode == "finetuned" and cases:
        probe = cases[0]
        if build_prompt(probe["text"], "finetuned", None) != build_prompt(probe["text"], "zero-shot", None):
            prompt_mismatch = 1
            print("  ⚠️ finetuned 与 zero-shot 的 prompt 不一致 —— 提升无法归因到微调")

    metrics = aggregate(rows)
    return {
        "mode": args.mode,
        "backend": backend.name,
        "model": getattr(backend, "model", None),
        "dataset": _rel(args.dataset),
        "dataset_total": len(dataset["cases"]),
        "temperature": args.temperature,
        "few_shot_k": args.few_shot_k if args.mode == "few-shot" else 0,
        "metrics": metrics,
        "parse_errors": parse_errors,
        "parse_error_rate": round(parse_errors / len(rows), 4) if rows else None,
        "avg_latency_s": round(statistics.fmean(latencies), 3) if latencies else None,
        "prompt_mismatch": bool(prompt_mismatch),
        "failures": failures,
        "cases": rows,
    }


def compare_to_baseline(report: dict, baseline_path: Path, threshold_pt: float) -> dict:
    """与基线比 micro-F1，按百分点判定。"""
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    base_f1 = (baseline.get("metrics", {}).get("micro", {}) or {}).get("f1")
    cur_f1 = (report["metrics"]["micro"] or {}).get("f1")
    if base_f1 is None or cur_f1 is None:
        raise SystemExit(f"❌ 基线或本次的 micro-F1 为空，无法对比：{baseline_path}")

    # 明确报"百分点"而不是比例 —— 「F1 从 0.60 到 0.75」是 15 个百分点，
    # 说成「提升 25%」会让验收口径打架。
    delta_pt = round((cur_f1 - base_f1) * 100, 2)
    return {
        "baseline_path": _rel(baseline_path),
        "baseline_f1": base_f1,
        "current_f1": cur_f1,
        "delta_pt": delta_pt,
        "threshold_pt": threshold_pt,
        "passed": delta_pt >= threshold_pt,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python ai/eval/extract_bench.py",
        description="C34 信息抽取基线对比（零样本 / few-shot / 微调）",
    )
    ap.add_argument("--dataset", default=str(DEFAULT_DATASET))
    ap.add_argument("--backend", choices=["ollama", "scripted"], default="ollama")
    ap.add_argument("--model", default="qwen2.5:3b")
    ap.add_argument("--modes", default="zero-shot",
                    help="逗号分隔：zero-shot / few-shot / finetuned")
    ap.add_argument("--few-shot-k", type=int, default=3)
    ap.add_argument("--scripted-answers", default="",
                    help="scripted 后端的答案文件（answers: {case_id: 输出文本}）")
    ap.add_argument("--limit", type=int, default=0, help="0 = 全部")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--out", default="")
    ap.add_argument("--compare", default="", help="基线 JSON 路径")
    ap.add_argument("--threshold", type=float, default=15.0, help="达标阈值（百分点）")
    ap.add_argument("--base-url", default="http://127.0.0.1:11434")
    ap.add_argument("--allow-partial", action="store_true",
                    help="评测集条目缺 expected 字段时跳过而不是报错")
    args = ap.parse_args(argv)

    dataset_path = Path(args.dataset)
    if not dataset_path.is_absolute():
        dataset_path = REPO_ROOT / dataset_path
    args.dataset = dataset_path
    if not dataset_path.exists():
        print(f"❌ 评测集不存在：{dataset_path}")
        return 2

    dataset = load_dataset(dataset_path)
    if args.allow_partial:
        dataset["cases"] = [c for c in dataset["cases"] if c.get("expected")]
    cases = stratified_sample(dataset["cases"], args.limit)
    print(f"[extract_bench] 评测集 {len(dataset['cases'])} 条，本次抽 {len(cases)} 条分层抽样")

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    invalid = [m for m in modes if m not in ("zero-shot", "few-shot", "finetuned")]
    if invalid:
        print(f"❌ 未知模式：{invalid}")
        return 2
    if args.backend == "ollama" and "few-shot" not in modes and "finetuned" not in modes:
        print("[extract_bench] 提示：只跑 zero-shot 时不需要 few-shot 示例")

    reports: list[dict] = []
    for mode in modes:
        args.mode = mode
        backend = _make_backend(args)
        if backend is None:
            return 2
        if isinstance(backend, OllamaBackend):
            problem = backend.preflight()
            if problem:
                print(f"❌ {problem}")
                return 2
        print(f"[extract_bench] 模式 `{mode}` → 后端 `{backend.name}`"
              f"{f' 模型 `{backend.model}`' if hasattr(backend, 'model') else ''}")
        report = run_mode(args, dataset, cases, backend)
        m = report["metrics"]
        print(f"  micro-F1 = {m['micro']['f1']} · macro-F1 = {m['macro_f1']} "
              f"· 严格匹配 = {m['strict_accuracy']} · 解析失败 = {report['parse_errors']}")
        reports.append(report)

    # 对比（只对最后一个模式；多模式时按需自行比对）
    exit_code = 0
    if args.compare:
        target = reports[-1]
        target["comparison"] = compare_to_baseline(target, Path(args.compare), args.threshold)
        c = target["comparison"]
        print(f"[extract_bench] ΔF1 = {c['delta_pt']} 个百分点（阈值 {c['threshold_pt']}）→ "
              f"{'✅ 达标' if c['passed'] else '❌ 未达标'}")
        if not c["passed"]:
            exit_code = 1

    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = REPO_ROOT / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = reports[0] if len(reports) == 1 else {"runs": reports}
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[extract_bench] JSON 报告 -> {out_path}")

        md_path = out_path.with_suffix(".md")
        md_path.write_text("\n".join(render_markdown(r) for r in reports), encoding="utf-8")
        print(f"[extract_bench] Markdown 报告 -> {md_path}")
    else:
        for r in reports:
            print(render_markdown(r))

    return exit_code


def _make_backend(args):
    if args.backend == "scripted":
        if not args.scripted_answers:
            print("❌ scripted 后端需要 --scripted-answers")
            return None
        path = Path(args.scripted_answers)
        if not path.is_absolute():
            path = REPO_ROOT / path
        if not path.exists():
            print(f"❌ 答案文件不存在：{path}")
            return None
        return ScriptedBackend(path)
    return OllamaBackend(args.model, args.base_url, args.temperature)


if __name__ == "__main__":
    sys.exit(main())
