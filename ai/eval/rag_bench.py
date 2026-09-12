"""RAG 检索质量评估框架（C14）。

作用：用一份**标注问答集**量化当前 RAG 的检索质量，作为后续
C15（切片策略可插拔）/ C16（检索重排）的**效果基准** ——
没有度量就无法验收"Top-3 命中率提升 ≥ 5 个百分点"。

指标：
- **hit@1 / hit@3 / hit@5**：命中率（命中 = 检索结果 `title` 命中该问题的
  `expected_titles` 任意一项；排序取**首个命中位置**）
- **MRR@5**：平均倒数排名（未命中记 0）
- **空结果率**：检索返回 0 条的比例
- **负样本误命中率**：`expected_titles` 为空的问题中，仍有返回结果的比例
  （衡量"不该答却乱答/编造引用"的风险）
- **延迟**：平均/中位检索耗时（毫秒）
- **检索模式**：向量命中（结果含 `score`）vs 关键词降级（无 `score`）

用法（PowerShell，注意中文需 `-X utf8`）：
    # 0) 准备：设置数据库密码（与起后端时一致）
    $env:XJT_DB_PASSWORD="***"; $env:XJT_DB_PORT="3307"

    # 1) 基线跑（默认 top-3）
    E:/miniconda3/python.exe -X utf8 ai/eval/rag_bench.py

    # 2) 落盘报告（JSON + Markdown）
    ... ai/eval/rag_bench.py --out ai/eval/out/rag_baseline.json

    # 3) 与基线对比（C15/C16 改完后用）
    ... ai/eval/rag_bench.py --compare ai/eval/out/rag_baseline.json

退出码：`hit@3` 低于 `--threshold`（默认 0.80，对应方案验收标准）时返回 1，可用于 CI 门禁。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
DEFAULT_DATASET = EVAL_DIR / "rag_questions.json"


# ------------------------------------------------------------------ 环境 ----

def bootstrap_backend() -> None:
    """把 `backend/` 加入 sys.path 并补齐默认环境变量。

    ⚠️ 必须在 `import app.*` **之前**调用：`app.core.config.settings` 是模块级单例，
    环境变量晚设不生效（与 backend/tests/conftest.py 同一套做法）。
    """
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))

    env_file = BACKEND_DIR / ".env"
    if env_file.exists():  # 有 .env 就先加载（不依赖 python-dotenv）
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())

    os.environ.setdefault("XJT_DB_HOST", "127.0.0.1")
    os.environ.setdefault("XJT_DB_PORT", "3307")  # 团队开发机；本机绿色版为 3306
    os.environ.setdefault("XJT_DB_NAME", "xiaojietong")


def load_dataset(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    questions = data.get("questions") or []
    if not questions:
        raise SystemExit(f"❌ 标注集为空：{path}")
    return data


def init_pool() -> None:
    """初始化 C++ 连接池。

    `rag.retrieve()` 的降级路径会走 `cpp_bridge.query()`，而它要求连接池已初始化 ——
    平起后端时由 `main.py` 的 lifespan 完成，本脚本是进程内直调，必须自己补这一步。
    """
    from app.core.config import settings  # noqa: PLC0415
    from app.db import cpp_bridge  # noqa: PLC0415

    if not cpp_bridge.available():
        raise SystemExit(
            "❌ jt_db C++ 扩展不可用。请先按 db/cpp_driver/README.md 构建，"
            "产物放到 backend/app/db/native/"
        )
    if not settings.db_password:
        raise SystemExit(
            "❌ 未设置数据库密码。请先执行：\n"
            "     $env:XJT_DB_PASSWORD='<你的MySQL密码>'\n"
            "     $env:XJT_DB_PORT='3307'   # 本机绿色版为 3306"
        )
    if cpp_bridge.pool_ready():
        return
    cpp_bridge.init_db(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        dbname=settings.db_name,
        min_conn=settings.db_min_conn,
        max_conn=settings.db_max_conn,
    )


# ------------------------------------------------------------------ 指标 ----

def first_rank(titles: list[str], expected: list[str]) -> int | None:
    """返回首个命中位置（1-based）；未命中返回 None。"""
    wanted = {t.strip() for t in expected if t and t.strip()}
    for idx, title in enumerate(titles, start=1):
        if title.strip() in wanted:
            return idx
    return None


def summarize(rows: list[dict], k: int) -> dict:
    """汇总指标。rows 为逐题结果。"""
    positives = [r for r in rows if r["expected_titles"]]
    negatives = [r for r in rows if not r["expected_titles"]]

    ranks = [r["rank"] for r in positives]
    hit_at = {
        f"hit@{n}": (sum(1 for x in ranks if x and x <= n) / len(positives)) if positives else 0.0
        for n in (1, 3, 5)
    }
    mrr = (sum(1.0 / x for x in ranks if x) / len(positives)) if positives else 0.0
    empty_rate = sum(1 for r in rows if r["n_hits"] == 0) / len(rows) if rows else 0.0
    false_hit_rate = (sum(1 for r in negatives if r["n_hits"] > 0) / len(negatives)) if negatives else 0.0

    latencies = [r["latency_ms"] for r in rows]
    vector_used = sum(1 for r in rows if r["vector_used"])

    return {
        "n_total": len(rows),
        "n_positive": len(positives),
        "n_negative": len(negatives),
        "k": k,
        **hit_at,
        "mrr": round(mrr, 4),
        "empty_rate": round(empty_rate, 4),
        "false_hit_rate": round(false_hit_rate, 4),
        "latency_avg_ms": round(statistics.mean(latencies), 1) if latencies else 0.0,
        "latency_p50_ms": round(statistics.median(latencies), 1) if latencies else 0.0,
        "vector_hit_rows": vector_used,
        "retrieval_mode": "vector+fallback" if vector_used else "keyword(降级)",
    }


def by_category(rows: list[dict]) -> list[dict]:
    """按分类聚合（便于定位是哪类知识检索差）。"""
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        if not r["expected_titles"]:
            continue
        buckets.setdefault(r["category"], []).append(r)
    out = []
    for cat, items in sorted(buckets.items()):
        ranks = [i["rank"] for i in items]
        out.append({
            "category": cat,
            "n": len(items),
            "hit@3": round(sum(1 for x in ranks if x and x <= 3) / len(items), 3),
        })
    return out


# ------------------------------------------------------------------ 执行 ----

async def run_benchmark(dataset: dict, top_k: int, clear_cache: bool) -> list[dict]:
    """逐题调用 RAG 检索。进程内直连（不依赖后端 HTTP 服务）。"""
    try:
        from app.services import rag  # noqa: PLC0415 - 必须在 bootstrap 之后导入
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            f"❌ 无法导入 app.services.rag：{exc}\n"
            "   请确认 backend/ 依赖已安装，且 jt_db 扩展已编译到 backend/app/db/native/"
        ) from exc

    if clear_cache:
        rag._RETRIEVE_CACHE.clear()  # 否则热点问题会命中 60s 缓存，延迟失真

    rows: list[dict] = []
    for item in dataset["questions"]:
        t0 = time.perf_counter()
        try:
            hits = await rag.retrieve(item["question"], top_k)
        except Exception as exc:  # noqa: BLE001 - 单题失败不中断整轮
            hits = []
            err = f"{type(exc).__name__}: {exc}"
        else:
            err = ""
        latency = (time.perf_counter() - t0) * 1000

        titles = [(h.get("title") or "").strip() for h in hits]
        rows.append({
            "id": item["id"],
            "category": item["category"],
            "question": item["question"],
            "expected_titles": item.get("expected_titles") or [],
            "got_titles": titles,
            "n_hits": len(titles),
            "rank": first_rank(titles, item.get("expected_titles") or []),
            "vector_used": any("score" in h for h in hits),
            "latency_ms": round(latency, 1),
            "error": err,
        })
    return rows


# ------------------------------------------------------------------ 报告 ----

def render_console(summary: dict, cats: list[dict], rows: list[dict]) -> None:
    print("=" * 78)
    print("RAG 检索质量评估（C14）")
    print("=" * 78)
    print(f"  题目数：{summary['n_total']}（可命中 {summary['n_positive']} / 负样本 {summary['n_negative']}）"
          f"    top_k = {summary['k']}")
    print(f"  检索模式：{summary['retrieval_mode']}（含 score 的题数 {summary['vector_hit_rows']}）")
    print("-" * 78)
    print(f"  hit@1 = {summary['hit@1']:.1%}    hit@3 = {summary['hit@3']:.1%}    "
          f"hit@5 = {summary['hit@5']:.1%}    MRR@5 = {summary['mrr']:.3f}")
    print(f"  空结果率 = {summary['empty_rate']:.1%}    负样本误命中率 = {summary['false_hit_rate']:.1%}")
    print(f"  延迟：avg {summary['latency_avg_ms']}ms / p50 {summary['latency_p50_ms']}ms")
    print("-" * 78)
    print("  逐条明细（✅ 命中 / ❌ 未命中 / ⚪ 负样本）：")
    for r in rows:
        if not r["expected_titles"]:
            mark = "❌ 误命中" if r["n_hits"] else "✅ 正确留空"
        else:
            rank = r["rank"]
            mark = f"✅ rank={rank}" if rank and rank <= summary["k"] else ("⚠️ 超 top-k" if rank else "❌ 未命中")
        got = " | ".join(r["got_titles"][:3]) or "（空）"
        print(f"    {r['id']:<4} {mark:<12} {r['question'][:26]:<28} → {got[:44]}")
        if r["error"]:
            print(f"          ⚠️ {r['error'][:90]}")
    if cats:
        print("-" * 78)
        print("  分类命中率（hit@3）：" + "  ".join(f"{c['category']}={c['hit@3']:.0%}" for c in cats))
    print("=" * 78)


def render_markdown(meta: dict, summary: dict, cats: list[dict], rows: list[dict]) -> str:
    lines = [
        "# RAG 检索质量评估报告（C14）",
        "",
        f"> 运行时间：{time.strftime('%Y-%m-%d %H:%M:%S')}　｜　标注集：`{meta.get('source', '')}`",
        f"> 题目数：**{summary['n_total']}**（可命中 {summary['n_positive']} / 负样本 {summary['n_negative']}）"
        f"　｜　top_k = **{summary['k']}**　｜　检索模式：**{summary['retrieval_mode']}**",
        "",
        "## 一、总体指标",
        "",
        "| 指标 | 数值 | 目标 |",
        "|---|---|---|",
        f"| **hit@1** | {summary['hit@1']:.1%} | — |",
        f"| **hit@3** | **{summary['hit@3']:.1%}** | ≥ 80%（方案验收） |",
        f"| hit@5 | {summary['hit@5']:.1%} | — |",
        f"| MRR@5 | {summary['mrr']:.3f} | — |",
        f"| 空结果率 | {summary['empty_rate']:.1%} | 越低越好 |",
        f"| **负样本误命中率** | {summary['false_hit_rate']:.1%} | 越低越好（防编造引用） |",
        f"| 平均延迟 | {summary['latency_avg_ms']} ms | — |",
        "",
        "## 二、分类命中率（hit@3）",
        "",
        "| 分类 | 题数 | hit@3 |",
        "|---|---|---|",
    ]
    for c in cats:
        lines.append(f"| {c['category']} | {c['n']} | {c['hit@3']:.0%} |")
    lines += [
        "",
        "## 三、逐条明细",
        "",
        "| 编号 | 问题 | 期望文档 | 实际 Top-3 | 排名 | 延迟(ms) |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        exp = "、".join(r["expected_titles"]) or "（负样本）"
        got = "、".join(r["got_titles"][:3]) or "（空）"
        rank = "-" if not r["expected_titles"] else (r["rank"] if r["rank"] else "未命中")
        lines.append(f"| {r['id']} | {r['question']} | {exp} | {got} | {rank} | {r['latency_ms']} |")
    lines += [
        "",
        "## 四、结论与后续",
        "",
        f"- 当前 **hit@3 = {summary['hit@3']:.1%}**"
        + ("，**已达标**（≥80%）" if summary["hit@3"] >= 0.8 else "，**未达标**（目标 ≥80%）"),
        "- 若检索模式为 `keyword(降级)`：说明 Ollama/向量库不可用，该结果**不代表向量检索真实水平**，"
        "需启动 Ollama 并加载 `bge-m3` 后重跑。",
        "- 本报告是 **C15（切片可插拔）/ C16（重排）** 的效果基线；改完后用 "
        "`--compare ai/eval/out/rag_baseline.json` 验证提升幅度。",
    ]
    return "\n".join(lines) + "\n"


def compare_with_baseline(baseline_path: Path, summary: dict) -> None:
    """与历史基线对比（C15/C16 验收用）。"""
    try:
        base = json.loads(baseline_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 无法读取基线 {baseline_path}：{exc}")
        return
    b = base.get("summary") or {}
    print("-" * 78)
    print(f"  与基线对比（{baseline_path.name}）")
    for key, label in (("hit@1", "hit@1"), ("hit@3", "hit@3"), ("hit@5", "hit@5"), ("mrr", "MRR@5")):
        if key in b:
            delta = summary[key] - b[key]
            arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "=")
            print(f"    {label:<8} {b[key]:.3f} → {summary[key]:.3f}   {arrow} {delta:+.3f}")
    print("=" * 78)


# ------------------------------------------------------------------ 入口 ----

def main() -> int:
    parser = argparse.ArgumentParser(description="RAG 检索质量评估（C14）")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET), help="标注问答集 JSON 路径")
    parser.add_argument("--top-k", type=int, default=3, help="检索条数（默认 3）")
    parser.add_argument("--out", default="", help="报告输出前缀（生成 .json 与 .md）")
    parser.add_argument("--compare", default="", help="与历史基线 JSON 对比")
    parser.add_argument("--threshold", type=float, default=0.80, help="hit@3 门禁阈值（默认 0.80）")
    parser.add_argument("--no-clear-cache", action="store_true", help="不清空 RAG 结果缓存")
    args = parser.parse_args()

    bootstrap_backend()
    init_pool()
    dataset = load_dataset(Path(args.dataset))

    print(f"标注集：{args.dataset}（{len(dataset['questions'])} 题）")
    rows = asyncio.run(run_benchmark(dataset, args.top_k, not args.no_clear_cache))
    summary = summarize(rows, args.top_k)
    cats = by_category(rows)

    render_console(summary, cats, rows)
    if args.compare:
        compare_with_baseline(Path(args.compare), summary)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "meta": {"dataset": args.dataset, "top_k": args.top_k, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")},
            "summary": summary,
            "by_category": cats,
            "rows": rows,
        }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        md = out.with_suffix(".md")
        md.write_text(render_markdown(dataset, summary, cats, rows), encoding="utf-8")
        print(f"  报告已落盘：{out} ｜ {md}")

    if summary["hit@3"] < args.threshold:
        print(f"❌ hit@3 = {summary['hit@3']:.1%} 低于阈值 {args.threshold:.0%}")
        return 1
    print(f"✅ hit@3 = {summary['hit@3']:.1%} 达到阈值 {args.threshold:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
