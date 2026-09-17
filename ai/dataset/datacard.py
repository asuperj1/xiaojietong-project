"""数据集**数据卡**（`C32`）：规模 / 分布 / 划分，以及与其他模块的衔接。

数据卡要回答的四个问题
----------------------
1. **多大规模** —— 总条数、各标注状态条数、文本长度分布
2. **什么分布** —— 来源类型、分类、重要度、实体类型的分布
3. **怎么划分** —— train / dev / test 各多少、比例是否合理
4. **质量如何** —— 引用 `quality.py` 的双人一致性结论（Kappa / 实体 F1）

为什么划分要"确定性"
--------------------
如果每次跑都得到不同的 train/test 切分，那么 `C34` 的基线对比就**不可复现**：
"F1 提升 15 个百分点"可能是换了测试集的产物。所以这里用固定 `seed` +
**按 id 排序后**切分，保证同输入同输出。
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Iterable, Sequence

from .quality import summarize_samples
from .schema import Sample

__all__ = [
    "split_samples",
    "build_datacard",
    "render_markdown",
    "DEFAULT_RATIOS",
]

# 默认按 8:1:1 划分（小数据集也够用；数据量上来后可在 CLI 覆盖）
DEFAULT_RATIOS: dict[str, float] = {"train": 0.8, "dev": 0.1, "test": 0.1}


def split_samples(
    samples: Sequence[Sample],
    *,
    ratios: dict[str, float] | None = None,
    seed: int = 42,
) -> dict[str, list[Sample]]:
    """确定性划分数据集。

    - 先按 `id` 排序，再用固定 `seed` 打乱 → **同输入必然同输出**
    - 至少给每个集合留 1 条（只要样本数够），避免出现空的 test 集
    - 比例之和不必等于 1，内部会归一化
    """
    ratios = dict(ratios or DEFAULT_RATIOS)
    if not ratios:
        raise ValueError("ratios 不能为空")
    total_ratio = sum(ratios.values())
    if total_ratio <= 0:
        raise ValueError("ratios 之和必须为正数")

    ordered = sorted(samples, key=lambda s: s.id)
    rng = random.Random(seed)
    rng.shuffle(ordered)

    n = len(ordered)
    names = list(ratios)
    result: dict[str, list[Sample]] = {k: [] for k in names}

    if n == 0:
        return result

    # 先按比例算配额，再确保每个集合非空（前提是样本数 ≥ 集合数）
    if n >= len(names):
        quotas = {k: max(1, int(round(n * ratios[k] / total_ratio))) for k in names}
        # 配额可能因取整超出总数，从最大的集合里减
        while sum(quotas.values()) > n:
            biggest = max(quotas, key=lambda k: quotas[k])
            if quotas[biggest] <= 1:
                break
            quotas[biggest] -= 1
        # 也可能少于总数，把余数补给 train
        idx = 0
        while sum(quotas.values()) < n:
            quotas[names[idx % len(names)]] += 1
            idx += 1
    else:
        # 样本太少，逐个分配
        quotas = {k: 0 for k in names}
        for i in range(n):
            quotas[names[i % len(names)]] += 1

    cursor = 0
    for k in names:
        result[k] = ordered[cursor:cursor + quotas[k]]
        cursor += quotas[k]
    return result


def build_datacard(
    samples: Sequence[Sample],
    *,
    name: str = "xiaojietong-extraction",
    version: str = "v0",
    split: dict[str, list[Sample]] | None = None,
    agreement: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """生成数据卡（`dict`，可直接序列化成 JSON 归档）。

    `agreement` 传 `quality.quality_summary(...)` 的返回值即可把
    "标注质量"一并写进数据卡 —— 数据卡不带质量结论是不完整的。
    """
    card: dict[str, Any] = {
        "name": name,
        "version": version,
        "summary": summarize_samples(samples),
    }

    if split is not None:
        total = sum(len(v) for v in split.values()) or 1
        card["splits"] = {
            k: {"count": len(v), "ratio": round(len(v) / total, 4)}
            for k, v in split.items()
        }

    if agreement is not None:
        card["agreement"] = {
            "paired": agreement.get("paired"),
            "threshold": agreement.get("threshold"),
            "min_kappa": agreement.get("min_kappa"),
            "passed": agreement.get("passed"),
            "fields": {
                field: {"kappa": st.get("kappa"), "agree": st.get("agree"),
                        "disagree": st.get("disagree")}
                for field, st in (agreement.get("fields") or {}).items()
            },
            "entities": agreement.get("entities"),
        }

    if extra:
        card["extra"] = extra
    return card


def render_markdown(card: dict[str, Any]) -> str:
    """把数据卡渲染成 Markdown（可直接进论文附录 / 软著材料）。"""
    s = card.get("summary", {})
    lines = [
        f"# 数据卡 · {card.get('name')} `{card.get('version')}`",
        "",
        "## 1. 规模",
        "",
        f"- 总条数：**{s.get('total', 0)}**",
        f"- 按标注状态：`{s.get('by_status', {})}`",
        f"- 文本长度：min {s.get('text_len', {}).get('min')} / "
        f"p50 {s.get('text_len', {}).get('p50')} / "
        f"p90 {s.get('text_len', {}).get('p90')} / "
        f"max {s.get('text_len', {}).get('max')}",
        "",
        "## 2. 分布",
        "",
        f"- 来源类型：`{s.get('by_source_type', {})}`",
        f"- 分类标签：`{s.get('by_category', {})}`",
        f"- 重要度：`{s.get('by_importance', {})}`",
        f"- 含截止时间的样本：{s.get('with_deadline', 0)}",
        f"- 实体类型计数：`{s.get('entity_type_counts', {})}`",
        "",
    ]

    if "splits" in card:
        lines += ["## 3. 划分", "", "| 集合 | 条数 | 占比 |", "|---|---|---|"]
        for k, v in card["splits"].items():
            lines.append(f"| {k} | {v['count']} | {v['ratio']:.2%} |")
        lines.append("")

    if "agreement" in card:
        a = card["agreement"]
        lines += [
            "## 4. 标注质量（双人一致性）",
            "",
            f"- 共同标注：**{a.get('paired')}** 条",
            f"- 最差字段 Kappa：**{a.get('min_kappa')}**（阈值 {a.get('threshold')}）",
            f"- 结论：{'✅ 通过' if a.get('passed') else '❌ 未通过'}",
            "",
            "| 字段 | Kappa | 一致 | 不一致 |",
            "|---|---|---|---|",
        ]
        for field, st in (a.get("fields") or {}).items():
            kappa = "—" if st.get("kappa") is None else f"{st['kappa']:.4f}"
            lines.append(f"| `{field}` | {kappa} | {st.get('agree')} | {st.get('disagree')} |")
        ent = a.get("entities") or {}
        if ent:
            lines += [
                "",
                f"- 实体级：P **{ent.get('precision')}** · R **{ent.get('recall')}** · "
                f"F1 **{ent.get('f1')}**",
            ]
        lines.append("")

    return "\n".join(lines) + "\n"


def dump(card: dict[str, Any], path: str | Path) -> None:
    """写出数据卡 JSON。"""
    Path(path).write_text(
        json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def dump_splits(split: dict[str, list[Sample]], out_dir: str | Path) -> dict[str, int]:
    """把划分结果写成 `<name>.jsonl`，返回各集条数。"""
    from .schema import write_jsonl

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for name, items in split.items():
        counts[name] = write_jsonl(out / f"{name}.jsonl", items)
    return counts
