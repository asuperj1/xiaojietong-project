"""预标注：为采集到的样本生成**供人工修订的初稿**（`C31`）。

定位（刻意与 `C27`/`C28` 区分开）
---------------------------------
`C27`（时间抽取）/ `C28`（重要度打分）是**生产链路**的实现，职责是"把结果写进库"。
本模块属于**数据集构建链路**，目标不同：

- 产出**初稿**，最终以人工标注为准（`C32` 只在人工标注上统计 Kappa）；
- 把"哪些片段值得标"先**高亮成候选**，降低人工标注的漏标率；
- 内置的 `KeywordTagger` **不做打分、不写库**。

因此两者不重复。等 `C27`/`C28` 合并后，可以直接把它们当成更强的 labeler 注入：
`--labeler app.services.notice_extract:extract_deadline_detail`（见 `resolve_labeler`）。

为什么 labeler 是"函数"而不是"类"
--------------------------------
注入函数最容易替换、最容易在测试里造假数据；类还要约定构造参数，反而更难组合。
"""

from __future__ import annotations

import importlib
import re
from typing import Any, Callable, Iterable

from .schema import Sample

__all__ = [
    "Labeler",
    "KeywordTagger",
    "NullLabeler",
    "resolve_labeler",
    "prelabel_samples",
]

# labeler 契约：吃一条样本，返回 labels 片段（合并进 sample.labels）
Labeler = Callable[[Sample], dict[str, Any]]

# ---------------------------------------------------------------- 内置规则

# 时间候选：日期 / 周几 / 相对日 / 截止措辞（只做**候选高亮**，不做归一化）
_TIME_RE = re.compile(
    r"(?:\d{4}\s*年\s*)?\d{1,2}\s*月\s*\d{1,2}\s*[日号]"
    r"|\d{4}\s*[-/.]\s*\d{1,2}\s*[-/.]\s*\d{1,2}"
    r"|(?:本|下|这)?\s*(?:周|星期|礼拜)[一二三四五六日天]"
    r"|今天|明天|后天|大后天|\d{1,3}\s*(?:天|小时|周|星期|个月)\s*内"
)
_PLACE_WORDS = (
    "食堂", "图书馆", "宿舍", "教学楼", "行政楼", "体育馆", "校医院", "实验楼",
    "中心校区", "南岭校区", "南湖校区", "新民校区", "朝阳校区", "和平校区",
    "驿站", "快递点", "报告厅", "活动中心", "操场",
)
_ORG_WORDS = (
    "教务处", "学工办", "研究生院", "校医院", "保卫处", "图书馆", "后勤处",
    "财务处", "招生办", "团委", "各学院", "学生会", "校办",
)
_MATTER_WORDS = (
    "选课", "退课", "考试", "补考", "重修", "报名", "缴费", "学费", "奖学金",
    "助学金", "退宿", "报到", "注册", "答辩", "实习", "招聘", "体检", "疫苗",
    "校园卡", "一卡通", "宿舍", "闭馆", "停电", "停水", "维修",
)


def _find_words(text: str, words: Iterable[str], limit: int = 8) -> list[str]:
    """按词表在文本中找候选（去重、保持词表顺序，结果确定）。"""
    hits: list[str] = []
    for w in words:
        if w in text and w not in hits:
            hits.append(w)
        if len(hits) >= limit:
            break
    return hits


class KeywordTagger:
    """关键字候选标注器：抽出 time / place / org / matter 四类**候选**。

    产物写进 `labels["entities"]`，`norm` 一律留空 —— 归一化是 `C27` 的职责，
    这里不做，避免把未经验证的归一化结果当成"标注"。
    """

    name = "keyword"

    def __call__(self, sample: Sample) -> dict[str, Any]:
        text = sample.text or ""
        entities: list[dict[str, Any]] = []

        for m in _TIME_RE.finditer(text):
            entities.append({"type": "time", "text": m.group(0).strip(), "norm": None})
        for w in _find_words(text, _PLACE_WORDS):
            entities.append({"type": "place", "text": w, "norm": None})
        for w in _find_words(text, _ORG_WORDS):
            entities.append({"type": "org", "text": w, "norm": None})
        for w in _find_words(text, _MATTER_WORDS):
            entities.append({"type": "matter", "text": w, "norm": None})

        return {
            "entities": entities,
            # 下面两项**故意留空**：打分归 C28，分类归人工/后续规则
            "importance": None,
            "category": None,
        }


class NullLabeler:
    """不标注：只把状态推进到 `prelabeled`，产出纯人工标注用的空白初稿。"""

    name = "null"

    def __call__(self, sample: Sample) -> dict[str, Any]:
        return {}


# ---------------------------------------------------------------- 注入与批处理

def resolve_labeler(spec: str) -> Labeler:
    """把 `--labeler` 的字符串解析成可调用对象。

    - `keyword` / `null`：内置
    - `pkg.module:attr`：从模块里取（用于注入 `C27`/`C28` 等生产实现）
    """
    spec = (spec or "keyword").strip()
    if spec == "keyword":
        return KeywordTagger()
    if spec == "null":
        return NullLabeler()
    if ":" not in spec:
        raise ValueError(
            f"无法识别的 labeler：{spec!r}（内置 keyword/null，或写 '包.模块:函数名'）"
        )
    module_name, attr = spec.split(":", 1)
    mod = importlib.import_module(module_name)
    fn = getattr(mod, attr)
    if not callable(fn):
        raise ValueError(f"{spec} 不是可调用对象")
    return fn


def prelabel_samples(samples: Iterable[Sample], labeler: Labeler | None = None) -> list[Sample]:
    """对样本做预标注（就地填 `labels`，并把状态推进到 `prelabeled`）。

    已有人工标注（`human` / `reviewed`）的样本**跳过** —— 重跑预标注不得覆盖人工成果。
    """
    tagger = labeler or KeywordTagger()
    for s in samples:
        if s.status in ("human", "reviewed"):
            continue
        produced = tagger(s) or {}
        merged = dict(s.labels or {})
        for k, v in produced.items():
            # 只填空位：不覆盖已有值（人工或上一轮已填）
            if merged.get(k) in (None, "", [], {}):
                merged[k] = v
        s.labels = merged
        s.annotation = {
            **(s.annotation or {}),
            "status": "prelabeled",
            "by": getattr(tagger, "name", "external"),
        }
    return list(samples)
