#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""基座 vs 微调 · 自动量化评测（C12）。

【为什么要做】
    `eval.py` 只是「打印 10 条抽样回答让人肉眼看」，无法支撑答辩 ——
    说不出"微调到底提升了多少"。本脚本给出**可量化、可复现**的对比指标。

【评分方法（客观，不依赖人工）】
    从标准答案中抽取「关键信息点」（时间 / 数字+单位），检查模型回答是否命中：
      · 关键信息命中率 = 命中点数 / 全部点数          （越高越好，召回）
      · 完整命中率     = 所有点都命中的题数 / 总题数   （越高越好，精确）
      · 拒答率         = 回答含"未收录/不清楚/建议咨询"等（越低越好）
      · 平均回答字数   = 辅助观察（过短=敷衍，过长=跑题）

【用法】
    # 需 Ollama 已启动且模型可用（基座 qwen2.5:3b、微调 xjt-3b）
    python ai/finetune/eval_compare.py --limit 30
    python ai/finetune/eval_compare.py --models qwen2.5:3b,xjt-3b --limit 50 --out ai/finetune/out/eval_compare.json

选项：
    --models     逗号分隔的模型名（默认 qwen2.5:3b,xjt-3b）
    --set        评测集（默认 ai/finetune/data/seed_qa.jsonl）
    --limit      抽样题数（默认 30，按 topic 分层抽样）
    --temperature 采样温度（默认 0.1，保证可复现）
    --out        JSON 结果输出路径（可选）
    --base-url   Ollama 地址（默认 http://127.0.0.1:11434）

作者：成员3（C++ 数据层 / 模型微调 / 数据库）· C12
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# 拒答 / 兜底话术（出现即视为「没能回答」）
REFUSAL_PAT = re.compile(
    r"未收录|不清楚|不知道|无法回答|建议咨询|请联系|不提供该|没有相关|暂无相关|抱歉")

# 关键信息点抽取：时间 与 数字+单位
TIME_PAT = re.compile(r"\d{1,2}:\d{2}")
UNIT_PAT = re.compile(r"\d+(?:\.\d+)?\s*(?:册|天|元|次|个|门|人|分钟|小时|%|周|年|月|折)")


def key_points(answer: str) -> list[str]:
    """从标准答案抽取关键信息点（归一化去空格）。"""
    pts = TIME_PAT.findall(answer) + UNIT_PAT.findall(answer)
    seen, out = set(), []
    for p in pts:
        norm = p.replace(" ", "")
        if norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


def stratified_sample(items: list[dict], limit: int) -> list[dict]:
    """按 topic 分层抽样，保证覆盖面。"""
    by_topic: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        by_topic[it.get("topic", "综合")].append(it)

    out: list[dict] = []
    idx = 0
    while len(out) < limit:
        progressed = False
        for lst in by_topic.values():
            if idx < len(lst) and len(out) < limit:
                out.append(lst[idx])
                progressed = True
        if not progressed:
            break
        idx += 1
    return out


def ollama_chat(model: str, question: str, base_url: str,
                temperature: float, timeout: int = 180) -> tuple[str, float, str]:
    """调 Ollama /api/chat，返回 (回答, 耗时秒, 错误信息)。"""
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": question}],
        "stream": False,
        "options": {"temperature": temperature, "num_ctx": 4096},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/chat", data=payload,
        headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return "", time.perf_counter() - t0, str(exc)
    return data.get("message", {}).get("content", ""), time.perf_counter() - t0, ""


def unload_model(model: str, base_url: str) -> None:
    """请求 keep_alive=0，让 Ollama 立即卸载该模型以释放显存。

    为何必须：16GB 显存下多个模型共存会触发严重性能雪崩 ——
    实测 xjt-3b(6.4GB) 与 qwen2.5:3b(2.2GB) 同时在卡上时，
    单次推理 1.45s 恶化到 20.96s（14 倍），卸载后才恢复正常。
    """
    payload = json.dumps({"model": model, "prompt": "", "keep_alive": 0}).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/generate", data=payload,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30):
            pass
    except Exception:  # noqa: BLE001
        pass  # 卸载失败不阻断评测


def score_one(answer: str, points: list[str]) -> dict:
    """对单题打分。"""
    norm = answer.replace(" ", "")
    hit = [p for p in points if p in norm]
    return {
        "hits": len(hit),
        "total": len(points),
        "hit_points": hit,
        "all_hit": len(hit) == len(points) and bool(points),
        "refusal": bool(REFUSAL_PAT.search(answer)),
        "chars": len(answer),
    }


def main() -> int:
    # Windows 终端常为 GBK 代码页，强制 UTF-8 输出，避免中文/符号编码崩溃
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser(description="基座 vs 微调 自动量化评测（C12）")
    ap.add_argument("--models", default="qwen2.5:3b,xjt-3b")
    ap.add_argument("--set", default="ai/finetune/data/seed_qa.jsonl", dest="setpath")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--temperature", type=float, default=0.1)
    ap.add_argument("--out", default="")
    ap.add_argument("--base-url", default="http://127.0.0.1:11434")
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]

    setpath = Path(args.setpath)
    if not setpath.is_absolute():
        setpath = REPO / setpath
    if not setpath.is_file():
        print(f"[错误] 评测集不存在：{setpath}", file=sys.stderr)
        return 1

    items = [json.loads(line) for line in
             setpath.read_text(encoding="utf-8").splitlines() if line.strip()]
    sample = stratified_sample(items, args.limit)
    print(f"# 评测集：{setpath.name}（共 {len(items)} 条，分层抽样 {len(sample)} 条）")
    print(f"# 对比模型：{', '.join(models)}｜temperature={args.temperature}\n")

    results: dict[str, dict] = {}
    details: dict[str, list[dict]] = {}

    for model in models:
        # 换模型前先卸载其它模型：16GB 显存装不下两个 3B 模型共存，
        # 共存时新模型推理会雪崩（实测 1.45s -> 20.96s），严重污染耗时指标。
        for other in models:
            if other != model:
                unload_model(other, args.base_url)
        print(f"===== 评测 {model}（已卸载其它模型释放显存）=====")
        agg = {"hit": 0, "total": 0, "all_hit": 0, "refusal": 0, "chars": 0,
               "seconds": 0.0, "failed": 0, "n": 0}
        rows: list[dict] = []

        for i, item in enumerate(sample, 1):
            q = item["question"]
            points = key_points(item.get("answer", ""))
            answer, cost, err = ollama_chat(model, q, args.base_url, args.temperature)
            if err:
                agg["failed"] += 1
                print(f"  [{i}/{len(sample)}] 失败：{err}")
                continue

            sc = score_one(answer, points)
            agg["hit"] += sc["hits"]
            agg["total"] += sc["total"]
            agg["all_hit"] += 1 if sc["all_hit"] else 0
            agg["refusal"] += 1 if sc["refusal"] else 0
            agg["chars"] += sc["chars"]
            agg["seconds"] += cost
            agg["n"] += 1

            rows.append({"q": q, "answer": answer, "cost": round(cost, 2), **sc})
            mark = "OK " if sc["all_hit"] else ("-- " if sc["total"] else "?? ")
            print(f"  [{i}/{len(sample)}] {mark}命中 {sc['hits']}/{sc['total']} "
                  f"({cost:.1f}s) {q}")

        n = max(agg["n"], 1)
        results[model] = {
            "题数": agg["n"],
            "关键信息命中率": round(agg["hit"] / max(agg["total"], 1) * 100, 1),
            "完整命中率": round(agg["all_hit"] / n * 100, 1),
            "拒答率": round(agg["refusal"] / n * 100, 1),
            "平均字数": round(agg["chars"] / n, 1),
            "平均耗时(s)": round(agg["seconds"] / n, 2),
            "失败数": agg["failed"],
        }
        details[model] = rows
        print()

    # ---------- 对比汇总 ----------
    print("## 评测结果对比\n")
    keys = list(next(iter(results.values())).keys())
    header = "| 指标 | " + " | ".join(results.keys()) + " | 变化 |"
    print(header)
    print("|---" * (len(results) + 2) + "|")

    first, *rest = results.keys()
    for k in keys:
        vals = [str(results[m][k]) for m in results]
        delta = "-"
        if rest:
            try:
                a, b = float(results[first][k]), float(results[rest[0]][k])
                diff = b - a
                better = (diff > 0) if k not in ("拒答率", "平均耗时(s)") else (diff < 0)
                if diff == 0:
                    delta = f"{diff:+.1f}"
                else:
                    delta = f"{diff:+.1f}" + (" ↑" if better else " ↓")
            except (ValueError, TypeError):
                delta = "-"
        print(f"| {k} | " + " | ".join(vals) + f" | {delta} |")

    print("\n> 说明：命中率/完整命中率越高越好；拒答率越低越好；"
          "指标由标准答案中的「时间 + 数字单位」自动抽取，无需人工判分。")

    if args.out:
        outpath = Path(args.out)
        if not outpath.is_absolute():
            outpath = REPO / outpath
        outpath.parent.mkdir(parents=True, exist_ok=True)
        outpath.write_text(json.dumps(
            {"summary": results, "details": details},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n明细已写入：{outpath}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
