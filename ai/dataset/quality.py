"""数据集**标注质量校验**（`C32`）：双人一致性（Cohen's Kappa）+ 实体级 P/R/F1。

为什么需要它
------------
`C31` 的流水线会产出「机器预标注 + 人工修订」的数据。但如果**两个标注者对同一批
样本的判断本身就不一致**，那么用这批数据去微调（`C33`）或做基线对比（`C34`），
得出的提升就分不清是"模型变好了"还是"标注噪声变小了"。
所以 `C32` 的验收口径是：**Cohen's Kappa ≥ 0.8**，并输出可复核的数据卡。

三个设计决定
------------
1. **对比的是"两份标注文件"，不是一条样本内的两个字段**
   `Sample.labels` 只有一份标注，做不了一致性统计。所以本模块的输入是
   **两份 JSONL**（标注者 A / B 各一份），按 `id` 配对后逐字段比对。
2. **只统计 `human` / `reviewed` 状态的样本**
   这是 `schema.py` 里就写死的约定：`raw`/`prelabeled` 是机器初稿，
   把它们算进一致性只会得到虚高的 Kappa。
3. **分类字段与非分类字段分开处理**
   - `category`/`importance` 等**离散值** → Cohen's Kappa
   - `entities` 是**集合** → 用 P/R/F1（Kappa 对集合不可用）
   - `deadline` 是**类时间字符串** → 先归一化再当离散值处理

边界处理（容易写错的地方）
--------------------------
- `pe == 1`（双方都只用了同一个标签）：Kappa 数学上无定义。
  这里约定：完全一致 → `1.0`；否则 → `0.0`，并在报告里标注 `degenerate=True`。
- 配对为空（没有共同 `id`）→ 返回 `None` 而不是 `0.0`，
  避免"没数据"被误读成"一致性极差"。
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .schema import Sample, read_jsonl

__all__ = [
    "cohen_kappa",
    "load_labels",
    "pair_by_id",
    "agreement_report",
    "entity_prf",
    "quality_summary",
    "normalize_deadline",
    "normalize_category",
    "KAPPA_THRESHOLD",
]

# `C32` 的验收阈值（任务单 §C32：Cohen's Kappa ≥ 0.8）
KAPPA_THRESHOLD = 0.8

# 一致性统计只认这两个状态（见 schema.py 的状态机约定）
_ANNOTATED_STATUSES = ("human", "reviewed")


# ---------------------------------------------------------------- 归一化


def normalize_category(value: Any) -> str:
    """分类标签归一化：去空白、统一小写、空值统一成 `""`。"""
    return str(value or "").strip().lower()


def normalize_deadline(value: Any) -> str:
    """日期时间归一化：取前 10 字符（`YYYY-MM-DD`）。

    标注者常写成 `2026-09-30` / `2026-09-30 23:59:59` / `2026-09-30T00:00:00`，
    这三种在业务上是同一件事，不该算"不一致"。
    非日期形状的值原样返回（去空白），避免把异常值悄悄吞掉。
    """
    s = str(value or "").strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return s


def _identity(value: Any) -> str:
    return str(value if value is not None else "").strip()


# ---------------------------------------------------------------- Kappa


def cohen_kappa(a: Sequence[Any], b: Sequence[Any]) -> dict[str, Any]:
    """计算 Cohen's Kappa。

    返回 `{"kappa": float|None, "po": float, "pe": float, "n": int, "degenerate": bool}`。

    - `n == 0`            → `kappa=None`（没有可统计的配对）
    - `pe == 1`（退化）   → 完全一致给 `1.0`，否则给 `0.0`，并置 `degenerate=True`
    """
    if len(a) != len(b):
        raise ValueError(f"两组标注长度不一致：{len(a)} vs {len(b)}")
    n = len(a)
    if n == 0:
        return {"kappa": None, "po": 0.0, "pe": 0.0, "n": 0, "degenerate": False}

    sa = [_identity(x) for x in a]
    sb = [_identity(x) for x in b]

    agree = sum(1 for x, y in zip(sa, sb) if x == y)
    po = agree / n

    ca, cb = Counter(sa), Counter(sb)
    pe = sum((ca[k] / n) * (cb[k] / n) for k in set(ca) | set(cb))

    if abs(1.0 - pe) < 1e-12:
        # 双方都只用一个标签 —— Kappa 无定义，按是否完全一致给退化值
        return {"kappa": 1.0 if agree == n else 0.0, "po": po, "pe": pe,
                "n": n, "degenerate": True}

    return {"kappa": (po - pe) / (1.0 - pe), "po": po, "pe": pe,
            "n": n, "degenerate": False}


# ---------------------------------------------------------------- 读入与配对


def load_labels(
    path: str | Path,
    *,
    only_annotated: bool = True,
) -> dict[str, dict[str, Any]]:
    """读一份标注 JSONL，返回 `{样本 id: labels}`。

    `only_annotated=True`（默认）时只收 `human`/`reviewed` 状态的样本 ——
    机器初稿参与一致性统计会得出虚高的 Kappa。
    """
    result: dict[str, dict[str, Any]] = {}
    for sample in read_jsonl(path):
        if only_annotated and sample.status not in _ANNOTATED_STATUSES:
            continue
        result[sample.id] = dict(sample.labels or {})
    return result


def pair_by_id(
    map_a: dict[str, dict[str, Any]],
    map_b: dict[str, dict[str, Any]],
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """按 `id` 取交集并配对（保持 `id` 排序，便于复核）。"""
    common = sorted(set(map_a) & set(map_b))
    return [(sid, map_a[sid], map_b[sid]) for sid in common]


# ---------------------------------------------------------------- 一致性报告


def agreement_report(
    pairs: Iterable[tuple[str, dict[str, Any], dict[str, Any]]],
    field: str,
    *,
    normalize: Callable[[Any], str] = _identity,
    missing: Any = "",
) -> dict[str, Any]:
    """对某一字段做一致性统计。

    `normalize` 决定如何把原始值变成可比较的离散值（如日期取前 10 位）；
    `missing` 是"该标注者没标这个字段"时的取值。
    """
    a_vals: list[str] = []
    b_vals: list[str] = []
    disagreements: list[dict[str, Any]] = []

    for sid, la, lb in pairs:
        va = normalize(la.get(field, missing))
        vb = normalize(lb.get(field, missing))
        a_vals.append(va)
        b_vals.append(vb)
        if va != vb:
            disagreements.append({"id": sid, "a": va, "b": vb})

    stats = cohen_kappa(a_vals, b_vals)
    stats.update({
        "field": field,
        "agree": stats["n"] - len(disagreements),
        "disagree": len(disagreements),
        "disagreements": disagreements[:50],   # 报告里只留前 50 条，避免刷屏
        "passed": (stats["kappa"] is not None and stats["kappa"] >= KAPPA_THRESHOLD),
    })
    return stats


def entity_prf(
    pairs: Iterable[tuple[str, dict[str, Any], dict[str, Any]]],
    *,
    field: str = "entities",
) -> dict[str, Any]:
    """实体集合的 P/R/F1（Kappa 不适用于集合，改用这个）。

    实体比较以 `(type, norm or text)` 为键 —— 只看归一化值会漏掉"同一时间的不同写法"，
    只看原文又会把 `9月30日` 与 `2026-09-30` 判成两个实体。
    """
    tp = fp = fn = 0
    for _sid, la, lb in pairs:
        set_a = _entity_keys(la.get(field))
        set_b = _entity_keys(lb.get(field))
        tp += len(set_a & set_b)
        fp += len(set_a - set_b)
        fn += len(set_b - set_a)

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": round(precision, 4), "recall": round(recall, 4),
            "f1": round(f1, 4)}


def _entity_keys(value: Any) -> set[tuple[str, str]]:
    """把 entities 归一成 `{(type, 值)}` 集合。"""
    keys: set[tuple[str, str]] = set()
    if not isinstance(value, (list, tuple)):
        return keys
    for item in value:
        if isinstance(item, dict):
            etype = str(item.get("type", "")).strip()
            val = str(item.get("norm") or item.get("text") or "").strip()
            if val:
                keys.add((etype, val))
        elif item:
            keys.add(("", str(item).strip()))
    return keys


# ---------------------------------------------------------------- 总报告


def quality_summary(
    path_a: str | Path,
    path_b: str | Path,
    *,
    fields: Sequence[str] = ("category", "importance", "deadline"),
) -> dict[str, Any]:
    """双人标注质量的**总报告**（供 CLI 与数据卡调用）。

    返回结构：
    ```
    {
      "paired": 40,            # 共同标注的样本数
      "only_in_a": 2, "only_in_b": 1,
      "fields": { "category": {...kappa...}, "importance": {...}, "deadline": {...} },
      "entities": { ...P/R/F1... },
      "min_kappa": 0.83,       # 各字段里最差的那个（验收看它）
      "passed": true           # min_kappa >= 0.8
    }
    ```
    """
    map_a = load_labels(path_a)
    map_b = load_labels(path_b)
    pairs = pair_by_id(map_a, map_b)

    report: dict[str, Any] = {
        "annotator_a": str(path_a),
        "annotator_b": str(path_b),
        "annotator_a_total": len(map_a),
        "annotator_b_total": len(map_b),
        "paired": len(pairs),
        "only_in_a": sorted(set(map_a) - set(map_b))[:20],
        "only_in_b": sorted(set(map_b) - set(map_a))[:20],
        "only_in_a_count": len(set(map_a) - set(map_b)),
        "only_in_b_count": len(set(map_b) - set(map_a)),
        "threshold": KAPPA_THRESHOLD,
    }

    normalizers: dict[str, Callable[[Any], str]] = {
        "deadline": normalize_deadline,
        "category": normalize_category,
    }
    fields_report: dict[str, Any] = {}
    for field in fields:
        fields_report[field] = agreement_report(
            pairs, field, normalize=normalizers.get(field, _identity)
        )
    report["fields"] = fields_report
    report["entities"] = entity_prf(pairs)

    kappas = [f["kappa"] for f in fields_report.values() if f["kappa"] is not None]
    report["min_kappa"] = round(min(kappas), 4) if kappas else None
    report["passed"] = bool(kappas) and min(kappas) >= KAPPA_THRESHOLD
    return report


def render_markdown(report: dict[str, Any]) -> str:
    """把总报告渲染成 Markdown（可粘进评审材料 / 论文附录）。"""
    lines = [
        "# 双人标注一致性报告（C32）",
        "",
        f"- 标注者 A：`{report['annotator_a']}`（{report['annotator_a_total']} 条）",
        f"- 标注者 B：`{report['annotator_b']}`（{report['annotator_b_total']} 条）",
        f"- **共同标注**：{report['paired']} 条"
        f"（仅 A 有 {report['only_in_a_count']} 条，仅 B 有 {report['only_in_b_count']} 条）",
        f"- 验收阈值：Cohen's Kappa ≥ **{report['threshold']}**",
        "",
        "## 逐字段一致性（Cohen's Kappa）",
        "",
        "| 字段 | Kappa | 一致 | 不一致 | 退化 | 通过 |",
        "|---|---|---|---|---|---|",
    ]
    for field, st in report["fields"].items():
        kappa = "—" if st["kappa"] is None else f"{st['kappa']:.4f}"
        lines.append(
            f"| `{field}` | {kappa} | {st['agree']} | {st['disagree']} | "
            f"{'是' if st['degenerate'] else '否'} | {'✅' if st['passed'] else '❌'} |"
        )

    ent = report["entities"]
    lines += [
        "",
        "## 实体级一致性（集合不适用 Kappa，用 P/R/F1）",
        "",
        f"- TP={ent['tp']} FP={ent['fp']} FN={ent['fn']}",
        f"- Precision **{ent['precision']}** · Recall **{ent['recall']}** · F1 **{ent['f1']}**",
        "",
        "## 结论",
        "",
    ]
    if report["min_kappa"] is None:
        lines.append("- ⚠️ 没有可统计的字段（配对为空？），无法判定。")
    elif report["passed"]:
        lines.append(
            f"- ✅ **通过**：最差字段 Kappa = **{report['min_kappa']}** ≥ {report['threshold']}"
        )
    else:
        lines.append(
            f"- ❌ **未通过**：最差字段 Kappa = **{report['min_kappa']}** < {report['threshold']}，"
            "需回溯标注指南或复训标注者"
        )

    # 附上前若干条分歧，便于直接定位
    for field, st in report["fields"].items():
        if st["disagreements"]:
            lines += ["", f"### `{field}` 分歧样例（前 {len(st['disagreements'])} 条）", ""]
            lines.append("| id | A | B |")
            lines.append("|---|---|---|")
            for d in st["disagreements"]:
                lines.append(f"| `{d['id']}` | `{d['a']}` | `{d['b']}` |")
    return "\n".join(lines) + "\n"


def summarize_samples(samples: Sequence[Sample]) -> dict[str, Any]:
    """样本层面的规模/分布统计（数据卡的原料）。"""
    by_status = Counter(s.status for s in samples)
    by_source = Counter(str((s.source or {}).get("type", "")) for s in samples)
    by_category = Counter(
        normalize_category((s.labels or {}).get("category", "")) for s in samples
    )
    importance = Counter(
        str((s.labels or {}).get("importance", "")) for s in samples
    )
    with_deadline = sum(1 for s in samples if (s.labels or {}).get("deadline"))
    entity_types = Counter()
    for s in samples:
        for key in _entity_keys((s.labels or {}).get("entities")):
            entity_types[key[0]] += 1

    lengths = [len(s.text or "") for s in samples] or [0]
    lengths_sorted = sorted(lengths)

    def pct(p: float) -> int:
        idx = min(len(lengths_sorted) - 1, int(len(lengths_sorted) * p))
        return lengths_sorted[idx]

    return {
        "total": len(samples),
        "by_status": dict(by_status),
        "by_source_type": dict(by_source),
        "by_category": dict(by_category),
        "by_importance": dict(importance),
        "with_deadline": with_deadline,
        "entity_type_counts": dict(entity_types),
        "text_len": {"min": lengths_sorted[0], "p50": pct(0.5),
                     "p90": pct(0.9), "max": lengths_sorted[-1]},
    }


def dump_json(report: dict[str, Any], path: str | Path) -> None:
    """把报告写成 JSON（便于入库/比对历史版本）。"""
    Path(path).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
