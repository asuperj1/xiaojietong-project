"""数据集样本的**标准格式**与校验（`C31`）。

为什么把它单独抽出来
--------------------
采集、预标注、导出、人工修订是四条链路，都读写同一份 JSONL。格式一旦漂移，
下游的 `C32`（标注一致性 Kappa）和 `C33`（微调）会集体返工 —— 所以
"什么算一条合法样本"只在这里定义一次，其余模块只负责搬运。

样本格式（JSONL 每行一条）
--------------------------
```json
{
  "id": "notice-9002",
  "text": "请符合条件的同学于 9 月 30 日前提交材料，逾期不再受理。",
  "source": {"type": "notice", "ref": "9002", "url": "https://..."},
  "labels": {
    "deadline": "2026-09-30",
    "importance": 5,
    "category": "奖学金",
    "entities": [{"type": "time", "text": "9 月 30 日", "norm": "2026-09-30"}]
  },
  "annotation": {"status": "prelabeled", "by": "rule", "at": "2026-09-16T10:00:00"}
}
```

设计要点
--------
1. **`labels` 允许为空 dict**：空白样本是有效中间状态（采集后未标注）；
   校验只强制 `id` / `text` / `source`，不强制已标注 —— 否则采集步骤无法单独跑通。
2. **`annotation.status` 是一台**状态机**：`raw`（仅采集）→ `prelabeled`（机器初稿）
   → `human`（人工标注）→ `reviewed`（复核通过）。`C32` 的双人一致性只在
   `human` 及之后的状态上统计。
3. **`entities` 用 `norm` 存归一化值**：原文是「9 月 30 日」、归一化是 `2026-09-30`，
   两者都保留 —— 只存归一化会丢失"模型该学什么字面"的信息。
4. **校验返回问题列表而不是抛异常**：批量场景下要一次看完全部问题，
   而不是修一条跑一次。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

__all__ = [
    "SOURCE_TYPES",
    "STATUSES",
    "ENTITY_TYPES",
    "Sample",
    "make_sample",
    "validate_sample",
    "read_jsonl",
    "write_jsonl",
]

# 采集源类型（与 db 表/文件来源对应）
SOURCE_TYPES = ("notice", "knowledge", "file", "other")
# 标注状态机
STATUSES = ("raw", "prelabeled", "human", "reviewed")
# 实体类型（轻量信息抽取的四类；先定小集合，后续可扩展）
ENTITY_TYPES = ("time", "place", "org", "matter")

MAX_TEXT_LEN = 20000          # 单条正文上限（超长多半是采集串了文件）

# deadline 的存储格式（与 C34 的口径一致：`YYYY-MM-DD`）；`None` / `""` 表示“无截止”
_DEADLINE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _as_text(value: Any) -> str:
    """把任意值转成字符串，但 **None → ""**（而不是 `str(None) == "None"`）。

    为何不能直接用 `str()`：JSON 里的 `null` 经 `json.loads` 变成 `None`，
    而 `obj.get("text", "")` 在**键存在、值为 null** 时返回的是 `None`（不是默认值），
    `str(None)` 得到的是**看起来完全正常的字符串 `"None"`**。

    后果有两层，第二层更隐蔽：

    1. 训练样本的 user 侧变成 `【公告】\nNone` —— 模型被训练成“从 None 里抽取”；
    2. `id` 同为 null 的两条样本会**双双变成 `"None"` 而互相撞上**，
       而 `C32` 统计一致性是**按 id 配对**的 ⇒ 会算出一个漂亮但毫无意义的数字。
       （这与“id 会飘”是同一后果的两个方向：不是飘，是**撞**。）

    ⚠️ 这一类问题的共性是「None 在进入校验之前就变成了看起来正常的值」，
    所以修复必须落在**读取边界**，而不是在校验里加特例 ——
    校验拿到的已经是 `"None"`，非空、确实是 str，完全合法。
    """
    return "" if value is None else str(value)


def _clean_source(raw: Any) -> dict[str, Any]:
    """归一化 `source`：把其中为 null 的 `type`/`ref`/`url` 清成空串。

    `{"ref": null}` 会原样进入 `meta.source`。它不进训练目标，危害比 `text`/`id` 小，
    但既然是同一类问题（JSON null 被当成值），顺手清掉更省事。
    保留其余未知键（不丢信息）。
    """
    src = dict(raw) if isinstance(raw, dict) else {}
    for key in ("type", "ref", "url"):
        if key in src:
            src[key] = _as_text(src[key])
    return src


@dataclass
class Sample:
    """一条数据集样本。"""

    id: str
    text: str
    source: dict[str, Any] = field(default_factory=dict)
    labels: dict[str, Any] = field(default_factory=dict)
    annotation: dict[str, Any] = field(default_factory=lambda: {"status": "raw"})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def status(self) -> str:
        return str(self.annotation.get("status", "raw"))


def _stable_suffix(text: str) -> str:
    """由正文得到**跨进程稳定**的 id 后缀。

    为什么不用内置 `hash()`：Python 对 str 的 `hash()` 默认带随机化
    （`PYTHONHASHSEED`），**同一段文本在不同进程会得到不同的值**。
    而 `C32` 的标注一致性是**按 `id` 配对**的（`pair_by_id`）——
    id 一飘，两个人的标注就配不上对，Kappa 直接失效。

    用 sha256 前缀：跨进程稳定，且与 `collect._text_hash`（去重键）同族。
    """
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12]


def make_sample(
    text: str,
    *,
    source_type: str = "file",
    ref: str = "",
    url: str = "",
    sample_id: str | None = None,
) -> Sample:
    """构造一条未标注样本（`id` 省略时按来源生成，保证可追溯）。"""
    sid = sample_id or f"{source_type}-{ref or _stable_suffix(text)}"
    return Sample(
        id=sid,
        text=(text or "").strip(),
        source={"type": source_type, "ref": ref, "url": url},
        labels={},
        annotation={"status": "raw"},
    )


def validate_sample(sample: Sample) -> list[str]:
    """校验一条样本，返回问题列表（空列表 = 合法）。

    不抛异常是刻意的：批量校验时要能**一次看完**所有问题。
    """
    problems: list[str] = []
    if not sample.id or not isinstance(sample.id, str):
        problems.append("id 必须是非空字符串")
    elif sample.id == "None":
        # `str(None)` 的产物：说明上游把 JSON null 当成了值。
        # 两个 null id 都会变成 "None" 而互相撞上，C32 按 id 配对会算出
        # 一个漂亮但毫无意义的 Kappa —— 必须在读取边界拦下（见 _as_text）。
        problems.append("id 不能是字符串 'None'（通常来自把 JSON null 当成了值）")
    if not sample.text or not sample.text.strip():
        problems.append("text 不能为空")
    elif sample.text.strip() == "None":
        problems.append("text 不能是字符串 'None'（通常来自把 JSON null 当成了值）")
    elif len(sample.text) > MAX_TEXT_LEN:
        problems.append(f"text 过长（{len(sample.text)} > {MAX_TEXT_LEN}）")

    stype = (sample.source or {}).get("type", "")
    if stype not in SOURCE_TYPES:
        problems.append(f"source.type 非法：{stype!r}（允许 {SOURCE_TYPES}）")

    if sample.status not in STATUSES:
        problems.append(f"annotation.status 非法：{sample.status!r}（允许 {STATUSES}）")

    labels = sample.labels or {}
    if not isinstance(labels, dict):
        problems.append("labels 必须是对象")
        return problems
    if "importance" in labels:
        imp = labels["importance"]
        if not isinstance(imp, int) or not 1 <= imp <= 5:
            problems.append(f"labels.importance 必须是 1~5 的整数，当前 {imp!r}")
    if "deadline" in labels:
        # deadline 是 TARGET_KEYS 里唯一没有校验的字段，而它会进训练目标。
        # 口径与 C34 一致：`YYYY-MM-DD`；`None` / `""` 表示“无截止”。
        dl = labels["deadline"]
        if dl is not None and dl != "" and not (
            isinstance(dl, str) and _DEADLINE_RE.match(dl)
        ):
            problems.append(
                f"labels.deadline 必须是 YYYY-MM-DD（或 null / 空串表示无截止），当前 {dl!r}"
            )
    if "entities" in labels:
        ents = labels["entities"]
        if not isinstance(ents, list):
            problems.append("labels.entities 必须是数组")
        else:
            for i, ent in enumerate(ents):
                if not isinstance(ent, dict):
                    problems.append(f"labels.entities[{i}] 必须是对象")
                    continue
                if ent.get("type") not in ENTITY_TYPES:
                    problems.append(f"labels.entities[{i}].type 非法：{ent.get('type')!r}")
                if not ent.get("text"):
                    problems.append(f"labels.entities[{i}].text 不能为空")
    return problems


def read_jsonl(path: str | Path) -> list[Sample]:
    """读取 JSONL（跳过空行与 `#` 注释行）。

    ⚠️ 所有字符串字段一律过 `_as_text()`：JSON 里的 `null` 是 `None`，
    若直接 `str(None)` 会得到**看起来正常的 `"None"`**，而校验层拦不住它
    （非空、确实是 str）。这类泄漏必须在**读取边界**堵住。
    """
    samples: list[Sample] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        obj = json.loads(line)
        samples.append(
            Sample(
                id=_as_text(obj.get("id")),
                text=_as_text(obj.get("text")),
                source=_clean_source(obj.get("source")),
                labels=obj.get("labels") or {},
                annotation=obj.get("annotation") or {"status": "raw"},
            )
        )
    return samples


def write_jsonl(path: str | Path, samples: Iterable[Sample]) -> int:
    """写出 JSONL，返回条数（`ensure_ascii=False`：中文直接可读，便于人工修订）。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with target.open("w", encoding="utf-8", newline="\n") as fh:
        for s in samples:
            fh.write(json.dumps(s.to_dict(), ensure_ascii=False))
            fh.write("\n")
            n += 1
    return n
