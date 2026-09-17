#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C35 提示工程：prompt 变体阶梯（纯字符串，无 I/O、无网络、无副作用）。

【为什么要单独一个模块】
    prompt 文本是本次实验的**唯一自变量**，必须能被单独 review、单独单测：
      · `V0` 必须与 C34 的 `extract_bench.SYSTEM_PROMPT` **逐字节相同**（否则对照组就不干净）；
      · `V1~V3` 必须是"在 V0 上追加"，而不是各写一份 —— 否则做了哪几档对比、
        每档加了什么，靠肉眼比长文本是比不出来的。
    所以这里只放字符串与它们的构造关系，跑实验的逻辑在 `prompt_opt.py`。

【阶梯设计：每一档只补 C34 已定位的一条缺口】
    C34 的 README 明确把三件事交给 C35，因为它们是 zero-shot 低分的主因
    （不是模型抽取能力差）：

    | 现象（C34 实测）                    | 根因                        | 本模块的修法        |
    |-------------------------------------|-----------------------------|---------------------|
    | 年份猜成 2022（期望 2026-10-20）    | prompt 没给当前日期         | `V1` 基准日与年份规则 |
    | importance 系统性偏低（期望 4/5，预测 2/3） | prompt 没给评分细则  | `V2` 重要度判定标准 |
    | category 出现组合值（"通知/竞赛"）  | 没有受控词表                | `V3` 受控词表        |

    另外 `V3` 一并补上 C34 列出的另两类难点所需的口径：`deadline` 何时该填 `null`
    （评测集里 4 条该填 null，硬编日期会被判 FP）与 `entities` 的字段口径。

【为什么分四档而不是只做「优化前 / 优化后」】
    两档只能回答"有没有用"，回答不了"是哪一条在起作用"。
    分档后每一档相对前一档的增量就是那条标准的净贡献 —— 这才是可写进报告的结论。
    而且分档几乎不增加成本：每档 24 条 case × ~2s。

【不做什么（诚实边界）】
    受控词表取的是**评测集实际使用的 5 类**。这在真实部署里是正当先验
    （类目来自库表/前端栏目，不是猜的），但它确实缩小了模型的搜索空间 ——
    因此报告里对 `V3` 的增益会**单独标注**这一条，不与"日期/细则"混算。

作者：成员3（C++ 数据层 / 模型微调 / 数据库）· C35
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent

#: 阶梯顺序（从朴素到优化）。报告与实验都按这个顺序输出，便于纵向比对。
VARIANTS: tuple[str, ...] = ("V0-naive", "V1-date", "V2-rubric", "V3-optimized")


def _load_sibling(name: str):
    """按路径加载同目录模块。

    与 `tests/test_extract_bench.py` 同一手法：不写 `from ai.eval import ...`，
    因为 `ai/` 是否有 `__init__.py` 取决于 C31 是否已合并 ——
    按路径加载可让本模块独立于 `ai/` 的包结构变化。
    """
    spec = importlib.util.spec_from_file_location(f"{name}_by_path", EVAL_DIR / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


#: C34 的原始 prompt（**唯一出处**，不复制文本 —— 避免两处口径漂移）
NAIVE_SYSTEM_PROMPT: str = _load_sibling("extract_bench").SYSTEM_PROMPT

#: 评测集的基准日。与 `ai/eval/extract_cases.json` 的 `reference_date` 必须一致，
#: 否则相对时间（"本周五"）的期望值就变了 —— 那是评测集冻结的意义。
#: 一致性由 `tests/test_prompt_opt.py::test_reference_date_matches_dataset` 守着。
REFERENCE_DATE = "2026-09-16"
REFERENCE_WEEKDAY = "星期三"

#: 受控类目：与评测集使用的 5 类一致（真实部署里来自库表/前端栏目）
CATEGORIES: tuple[str, ...] = ("奖学金", "活动", "讲座", "竞赛", "通知")

# ---------------------------------------------------------------- 各档补齐 ----

_DATE_BLOCK = f"""【今天】{REFERENCE_DATE}（{REFERENCE_WEEKDAY}）。
日期规则（务必遵守）：
1. 通知里没写年份的日期，一律按**今天所在年份**（2026）理解，不要凭猜测填别的年份；
2. 若按上面规则推算出的日期**早于今天**，则理解为**下一年**；
3. `deadline` 与 `entities[].norm` 统一用 `YYYY-MM-DD`（不要带时分秒）。"""

_RUBRIC_BLOCK = """【importance 判定（3~5，逐条对照后给分）】
5 = 报名/选课/放假/申请**截止**类硬期限：错过就失去资格或直接影响学业安排；
4 = 需要学生**按指定时间去办理或参加**（提交材料、听讲座、系统维护、献血、初赛等），
    但不是上述资格类截止；
3 = 信息通报，或可自主参加、不影响学业的活动预告（成绩公布、闭馆通知、游园、展演等）。
参考：多数通知落在 4；只有明确的信息通报才是 3。"""

_VOCAB_BLOCK = f"""【category 必须是下面之一，且只能选一个】{' / '.join(CATEGORIES)}
不要输出组合值（如"通知/竞赛"），也不要自造类别；拿不准就选最接近的那个。"""

_OUTPUT_BLOCK = """【其余字段口径】
- `deadline`：只填通知里**最相关的那个截止/时间要求**；如果整条通知根本没有时间要求
  （例如只是公布名单、说明办理方式），必须填 `null` —— **不要为了填满而编造日期**。
- `entities`：只抽 `type="time"` 的时间实体；`text` 用**原文片段**，`norm` 用 `YYYY-MM-DD`；
  一句话里出现多个时间就都抽出来；没有任何时间则填 `[]`。"""


def _blocks(variant: str) -> list[str]:
    """返回该档在 V0 之上追加的补充块（顺序即阶梯顺序）。"""
    if variant not in VARIANTS:
        raise KeyError(f"未知的 prompt 变体：{variant!r}（可选：{', '.join(VARIANTS)}）")
    idx = VARIANTS.index(variant)
    blocks: list[str] = []
    if idx >= 1:
        blocks.append(_DATE_BLOCK)
    if idx >= 2:
        blocks.append(_RUBRIC_BLOCK)
    if idx >= 3:
        blocks.append(_VOCAB_BLOCK)
        blocks.append(_OUTPUT_BLOCK)
    return blocks


def get_system_prompt(variant: str) -> str:
    """取该档的 system prompt。

    `V0-naive` **逐字节等于** C34 的 `SYSTEM_PROMPT`（不追加任何东西），
    这样本实验的"优化前"就是 C34 那个已发布的 baseline 本身。
    """
    blocks = _blocks(variant)
    if not blocks:
        return NAIVE_SYSTEM_PROMPT
    return "\n\n".join([NAIVE_SYSTEM_PROMPT, *blocks])


def added_summary(variant: str) -> str:
    """该档相对 V0 多了什么（一行，写进报告）。"""
    mapping = {
        "V0-naive": "（C34 原样，无补充）",
        "V1-date": "补基准日 + 无年份按 2026 理解 + 跨年规则",
        "V2-rubric": "再补 importance 3~5 判定标准",
        "V3-optimized": "再补 category 受控词表 + deadline 何时为 null + entities 口径",
    }
    return mapping[variant]


def ablation_rows() -> list[dict]:
    """供报告渲染的阶梯表。"""
    return [
        {"variant": v, "added": added_summary(v),
         "prompt_chars": len(get_system_prompt(v))}
        for v in VARIANTS
    ]
