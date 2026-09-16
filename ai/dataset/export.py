"""导出：训练 JSONL / 人工标注 CSV / 统计报告（`C31`）。

三种产物各有用途
----------------
| 函数 | 产物 | 谁用 |
|---|---|---|
| `export_training_jsonl` | Qwen 对话格式 JSONL（`messages` 数组） | `C33` 微调（与 `ai/finetune/` 的格式对齐） |
| `export_labeling_csv` | 紧凑 CSV（实体压成 `类型:词\\|类型:词`） | 人工标注 / 复核（Excel、WPS 直接改） |
| `stats_markdown` | 统计报告 | 论文/软著素材、`C32` 的输入体检 |

为什么训练格式用 `messages` 而不是 `prompt/completion`
--------------------------------------------------
`ai/finetune/build_dataset.py` 产出的是 Qwen2.5 对话格式，`train.py` 直接吃它。
这里沿用同一格式，`C33` 才能**零改动**复用既有微调流水线。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable

from .schema import STATUSES, Sample

__all__ = [
    "SYSTEM_PROMPT",
    "build_messages",
    "export_training_jsonl",
    "export_labeling_csv",
    "stats_markdown",
]

SYSTEM_PROMPT = (
    "你是校园公告信息抽取助手。请从公告中抽取时间、地点、机构、事项与截止日期，"
    "只输出 JSON，不要解释。没有的字段省略，不要编造。"
)

# 导出为训练目标时保留的字段（顺序即 JSON 里的键序，便于人工比对）
TARGET_KEYS = ("deadline", "importance", "category", "entities")


def _clean_entities(ents: Any) -> list[dict[str, Any]]:
    """清理实体数组：丢弃残缺项，并且**只在有归一化值时才落 `norm` 键**。

    为何必须再清一层：顶层的空值过滤（`_non_empty`）挡不住**嵌套**的 None。
    `prelabel` 产出的实体形如 `{"type": "place", "text": "图书馆", "norm": None}`，
    而 `entities` 是非空 list ⇒ 整体被保留 ⇒ 里面的 `norm: null` 一路进了训练目标，
    等于**教模型输出 `"norm": null`** —— 恰恰是 `_non_empty` 想避免的事。
    """
    out: list[dict[str, Any]] = []
    for e in ents if isinstance(ents, list) else []:
        if not isinstance(e, dict) or not e.get("type") or not e.get("text"):
            continue
        item: dict[str, Any] = {"type": e["type"], "text": e["text"]}
        if e.get("norm") not in (None, ""):
            item["norm"] = e["norm"]
        out.append(item)
    return out


def _non_empty(labels: dict[str, Any]) -> dict[str, Any]:
    """只保留有效值：None / 空串 / 空列表都不进训练目标，避免模型学会输出空字段。

    `entities` 需要**再往里清一层**（见 `_clean_entities`）：
    非空 list 会被整体保留，其中的空字段不会被上面的条件挡住。
    """
    out: dict[str, Any] = {}
    for k in TARGET_KEYS:
        if k not in labels:
            continue
        v = labels[k]
        if v in (None, "", [], {}):
            continue
        if k == "entities":
            cleaned = _clean_entities(v)
            if not cleaned:
                continue
            out[k] = cleaned
            continue
        out[k] = v
    return out


def build_messages(sample: Sample) -> dict[str, Any]:
    """一条样本 → Qwen 对话格式（与 `ai/finetune/train.py` 的输入一致）。"""
    target = _non_empty(sample.labels or {})
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"【公告】\n{sample.text}"},
            {
                "role": "assistant",
                "content": json.dumps(target, ensure_ascii=False, sort_keys=False),
            },
        ],
        "meta": {"id": sample.id, "status": sample.status, "source": sample.source},
    }


def export_training_jsonl(samples: Iterable[Sample], out_path: str | Path) -> int:
    """导出训练用 JSONL；**只导出至少有一项标注的样本**（空目标会教坏模型）。"""
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with target.open("w", encoding="utf-8", newline="\n") as fh:
        for s in samples:
            if not _non_empty(s.labels or {}):
                continue
            fh.write(json.dumps(build_messages(s), ensure_ascii=False))
            fh.write("\n")
            n += 1
    return n


def _entities_cell(sample: Sample) -> str:
    """实体压成一格：`time:9月30日|place:图书馆` —— 便于在表格里人工增删。"""
    ents = (sample.labels or {}).get("entities") or []
    parts = []
    for e in ents:
        if isinstance(e, dict) and e.get("type") and e.get("text"):
            parts.append(f"{e['type']}:{e['text']}")
    return "|".join(parts)


def export_labeling_csv(samples: Iterable[Sample], out_path: str | Path) -> int:
    """导出人工标注用 CSV（UTF-8-SIG，Excel 直接打开不乱码）。"""
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    # utf-8-sig：Excel 默认按本地编码读 CSV，不带 BOM 的中文会乱码
    with target.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["id", "status", "source_type", "deadline", "importance", "category",
             "entities(类型:文本|...)", "text"]
        )
        for s in samples:
            labels = s.labels or {}
            writer.writerow([
                s.id,
                s.status,
                (s.source or {}).get("type", ""),
                labels.get("deadline") or "",
                labels.get("importance") if labels.get("importance") is not None else "",
                labels.get("category") or "",
                _entities_cell(s),
                s.text.replace("\n", " ")[:500],
            ])
            n += 1
    return n


def stats_markdown(samples: Iterable[Sample]) -> str:
    """生成统计报告（Markdown），用于"数据集即成果"的交付说明与 `C32` 的输入体检。"""
    rows = list(samples)
    total = len(rows)
    by_status = {k: 0 for k in STATUSES}
    by_source: dict[str, int] = {}
    ent_counts: dict[str, int] = {}
    with_labels = 0
    for s in rows:
        by_status[s.status] = by_status.get(s.status, 0) + 1
        st = (s.source or {}).get("type", "?")
        by_source[st] = by_source.get(st, 0) + 1
        if s.labels:
            with_labels += 1
        for e in (s.labels or {}).get("entities") or []:
            if isinstance(e, dict) and e.get("type"):
                ent_counts[e["type"]] = ent_counts.get(e["type"], 0) + 1

    human_done = by_status.get("human", 0) + by_status.get("reviewed", 0)
    lines = [
        "# 数据集统计报告（C31 工具链生成）",
        "",
        f"- 样本总数：**{total}**",
        f"- 已有人工标注（human + reviewed）：**{human_done}**"
        f"（{human_done / total:.1%}）" if total else "- 样本总数：**0**",
        f"- 带 labels 的样本：**{with_labels}**",
        "",
        "## 标注状态分布",
        "",
        "| 状态 | 条数 | 说明 |",
        "|---|---|---|",
        f"| raw | {by_status.get('raw', 0)} | 仅采集，未标注 |",
        f"| prelabeled | {by_status.get('prelabeled', 0)} | 机器初稿，待人工修订 |",
        f"| human | {by_status.get('human', 0)} | 人工已标注 |",
        f"| reviewed | {by_status.get('reviewed', 0)} | 复核通过（**C32 一致性只统计这里**）|",
        "",
        "## 来源分布",
        "",
        "| 来源 | 条数 |",
        "|---|---|",
    ]
    for k, v in sorted(by_source.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {k} | {v} |")

    lines += ["", "## 实体候选分布（预标注产物）", "", "| 类型 | 条数 |", "|---|---|"]
    if ent_counts:
        for k, v in sorted(ent_counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"| {k} | {v} |")
    else:
        lines.append("| （无） | 0 |")

    lines += [
        "",
        "## 待办",
        "",
        f"- [ ] 人工修订 {by_status.get('prelabeled', 0)} 条预标注样本"
        f"（导出的 CSV 可直接编辑）",
        "- [ ] `C32`：双人标注一致性 Kappa ≥ 0.8（在 reviewed 子集上统计）",
        "",
    ]
    return "\n".join(lines)
