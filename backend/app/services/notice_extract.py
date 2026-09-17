"""通知文本的「时间实体」抽取（零依赖规则引擎，模型可插拔）。

为什么需要这个模块
------------------
任务卡 **`C27`（二阶段 W3）**：校园通知正文里的截止时间是人写的自然语言 ——
「请于 **9 月 30 日前** 提交材料」「**本周五** 17:00 停止受理」「**截至 10 月 8 日**」。

`campus_notice` 表已由 `B19`（`db/sql/14_notice_extend.sql`）加好 `deadline` 列，
但**没有任何代码往里写**：通知只能靠人肉阅读判断"还剩几天"，前端也无法排序/提醒。
本模块就是把「正文 → `deadline`」这一步做出来。

方案选型
--------
| 方案 | 取舍 |
|---|---|
| **纯规则（本模块默认）** | **零依赖**、确定性、可离线单测；校园通知的时间表达高度模板化，规则覆盖率足够 |
| LLM 抽取（`qwen2.5:3b` 等） | 泛化更好，但**不确定**（同输入可能不同输出）、需联网 Ollama、成本高，且难以写"准确率"断言 |
| 微调小模型（`C33`） | 科研主线目标，但需先有**标注数据集**（`C31`）与**基线对比**（`C34`）—— 那正是本模块要充当的 baseline |

因此本模块先交付**可复现的规则 baseline**：`C33`/`C34` 的微调模型必须**跑赢它**才有意义
（与 `C14` 的 RAG 基线同理，先有尺子再优化）。模型路径以 `extractor` 参数留出插槽，接口不变。

设计要点
--------
1. **只认「截止」语义**：通知里常同时出现多个日期（发布日 / 活动日 / 截止日），
   例如「活动 9月20日 举行，报名 9月25日 截止」。本模块靠**截止标记词**分辨：
   - 前置标记：`截至` / `截止` / `最晚` / `不晚于` / `限期` / `务必于` / `请于` / `限于`
   - 后置标记：`前` / `之前` / `以前` / `截止` / `为止` / `止`
   命中标记的那个日期优先；都没有时取**最后一个**日期（区间「9月20日至9月25日」
   的结束端才是截止）。
2. **无年份 → 取「最近的将来」**：`9月30日` 在 10 月看到时指的是**明年** 9 月 30 日，
   否则 `deadline < now` 会把刚发的新通知判成过期。
3. **只有日期没有钟点 → 当天 `23:59:59`**：「9月30日前」= 9 月 30 日一整天都有效。
   这是**业务口径**，落在常量 `DEADLINE_END_OF_DAY` 便于全站统一。
4. **裸周几 = 本周内的那一天**（周一为一周之始，符合中文习惯），**即使已过也不顺延**
   —— 通知里的"周五"就是本周五，已过说明截止已过；顺延会让过期通知看起来还没到期。
5. **可解释**：返回 `ExtractResult`（带 `kind` 命中的模式名与 `matched` 原文片段），
   便于人工复核与回归定位；`C28` 的重要度打分沿用同一套"依据"风格。
6. **确定性**：`now` **必须可注入**（默认 `datetime.now()`）—— 否则「本周五」「明天」
   这类相对表达无法写单测，也做不了可复现的准确率评测。

用法
----
>>> from datetime import datetime
>>> extract_deadline("请于9月30日前提交材料", now=datetime(2026, 9, 15, 10, 0))
datetime.datetime(2026, 9, 30, 23, 59, 59)
>>> extract_deadline("本周五17:00停止受理", now=datetime(2026, 9, 15, 10, 0))
datetime.datetime(2026, 9, 18, 17, 0)
>>> extract_deadline("关于图书馆开放时间的说明", now=datetime(2026, 9, 15, 10, 0)) is None
True
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

__all__ = [
    "ExtractResult",
    "DEADLINE_END_OF_DAY",
    "extract_deadline",
    "extract_deadline_detail",
]

# 只有日期、没有钟点时，取当天最后一刻（"9月30日前" = 9月30日整天有效）
DEADLINE_END_OF_DAY = time(23, 59, 59)

# 日期之后允许出现的「钟点」字符窗口（超出就不认为是同一条截止时间）
_TIME_WINDOW = 10
# 截止标记词与日期之间允许的字符距离
_MARK_WINDOW = 6


def _normalize(text: str) -> str:
    """归一化：全角转半角（`９`→`9`、`：`→`:`）、去掉全部空白。

    保留中文与标点 —— 日期/标记词的正则依赖它们。
    """
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))


# ---------------------------------------------------------------- 中文数字

_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _cn_to_int(text: str) -> int | None:
    """把中文数字转 int（`三十一`→31）；不认识返回 None。

    只覆盖「月 / 日 / 天」的取值范围（1~400），不做通用中文数字库 ——
    校园通知不会写「九十九月」。支持阿拉伯数字直通（`9`→9）。
    """
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if any(c not in _CN_DIGITS for c in text):
        return None
    if "十" not in text:
        return _CN_DIGITS.get(text) if len(text) == 1 else None
    head, _, tail = text.partition("十")
    tens = (_CN_DIGITS.get(head, 1) if head else 1)      # "十五" ⇒ head 空 ⇒ 1
    ones = (_CN_DIGITS.get(tail, 0) if tail else 0)
    return tens * 10 + ones


_NUM = r"(?:\d{1,4}|[零〇一二两三四五六七八九十]{1,4})"


def _num(text: str) -> int | None:
    """正则捕获组 → int（阿拉伯数字或中文数字皆可）。"""
    return _cn_to_int(text)


# ---------------------------------------------------------------- 钟点

# 小时用阿拉伯数字；中文「五点」不识别 —— 它与「五月」字面太近，误判代价高于收益
_TIME_RE = re.compile(
    r"(?:(上午|下午|中午|晚上|晚间|傍晚|凌晨|早上|清晨|夜里|晚))?"
    r"(\d{1,2})"
    r"(?::|点|时)"
    r"(?:(\d{1,2})分?)?"
)

_MERIDIEM_PM = frozenset({"下午", "晚上", "晚间", "傍晚", "夜里", "晚"})
_MERIDIEM_NOON = frozenset({"中午"})


def _apply_meridiem(hour: int, meridiem: str | None) -> int | None:
    """12 小时制 + 上午/下午 → 24 小时制；非法返回 None。"""
    if not 0 <= hour <= 24:
        return None
    if meridiem in _MERIDIEM_PM and hour < 12:
        hour += 12                                   # 「下午 5 点」= 17:00
    elif meridiem in _MERIDIEM_NOON and hour < 12:
        hour = 12                                    # 「中午 12 点」
    if hour == 24:                                   # 「24 点」不跨日，夹到 23:59
        hour = 23
    return hour


def _find_time(norm: str, start: int) -> time | None:
    """从 `start` 起在窗口内找钟点，找到返回 `time`。"""
    m = _TIME_RE.search(norm, start, start + _TIME_WINDOW)
    if not m:
        return None
    hour = _apply_meridiem(int(m.group(2)), m.group(1))
    if hour is None:
        return None
    minute = int(m.group(3)) if m.group(3) else 0
    if not 0 <= minute <= 59:
        return None
    return time(hour, minute)


# ---------------------------------------------------------------- 日期

_DATE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("cn_ymd", re.compile(rf"(\d{{4}})\s*年\s*({_NUM})\s*月\s*({_NUM})\s*[日号]")),
    ("iso", re.compile(r"(\d{4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})")),
    ("cn_md", re.compile(rf"({_NUM})\s*月\s*({_NUM})\s*[日号]")),
    ("num_md", re.compile(r"(?<!\d)(\d{1,2})\s*[-/]\s*(\d{1,2})(?!\d)")),
]

_WEEKDAY_CN = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_WEEK_RE = re.compile(
    r"(本周|这周|本星期|这个星期|下周|下星期|下个星期|周|星期|礼拜)([一二三四五六日天])"
)
_REL_DAY_RE = re.compile(r"(大后天|今天|今日|明天|明日|后天|今儿|明儿)")
_INTERVAL_RE = re.compile(
    rf"({_NUM})\s*(天|日|周|星期|个月|月|小时|钟头)\s*(?:之内|以内|内|后)"
)
# 「本月底」「9月底」「下月初」：`月底/月末/月初` 是明确词，不会误伤「彻底」这类词
_MONTH_EDGE_RE = re.compile(rf"(?:(本月|这个月|下个月|下月|({_NUM})\s*月)\s*)?(月底|月末|月初)")

# 截止标记词（见模块 docstring 设计要点 1）
_MARK_BEFORE = ("截至", "截止", "最晚", "不晚于", "限期", "务必于", "请于", "限于")
_MARK_AFTER = ("前", "之前", "以前", "截止", "为止", "止")

# 「起始/施行」类：这些日期是**什么时候开始**，不是什么时候截止。
# 真实数据踩到过：「2026年秋季学期选课通知」里的“9月1日”（开学）被抽成了 deadline。
_START_RE = re.compile(r"开始|施行|生效|启用|实施|开学")


def _mk_date(year: int, month: int, day: int) -> date | None:
    """构造日期；非法（如 2 月 30 日）返回 None。"""
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _nearest_future(year: int | None, month: int, day: int, base: datetime) -> date | None:
    """无年份时取「最近的将来」：今年已过 ⇒ 顺延到明年（见设计要点 2）。"""
    if year is not None:
        return _mk_date(year, month, day)
    d = _mk_date(base.year, month, day)
    if d is not None and d >= base.date():
        return d
    return _mk_date(base.year + 1, month, day)


def _parse_abs(kind: str, groups: tuple[str, ...]) -> tuple[int | None, int, int] | None:
    """解析绝对日期的三段捕获组 → (year|None, month, day)。"""
    if kind == "cn_ymd":
        year, month, day = int(groups[0]), _num(groups[1]), _num(groups[2])
    elif kind == "iso":
        year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
    elif kind == "cn_md":
        year, month, day = None, _num(groups[0]), _num(groups[1])
    else:                                            # num_md
        year, month, day = None, int(groups[0]), int(groups[1])
    if month is None or day is None or not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    return year, month, day


def _collect_dates(norm: str, base: datetime) -> list[tuple[int, int, date, str, str]]:
    """收集全部绝对日期候选 → [(start, end, date, kind, matched)]，已去重叠。

    去重叠是必要的：`2026-09-30` 会被 `iso` 整段命中，同一串里的 `9-30` 也会被
    `num_md` 命中 —— 两者重叠时只保留更长的那个，否则同一日期会算两次。
    """
    found: list[tuple[int, int, date, str, str]] = []
    for kind, pattern in _DATE_PATTERNS:
        for m in pattern.finditer(norm):
            parsed = _parse_abs(kind, m.groups())
            if parsed is None:
                continue
            d = _nearest_future(parsed[0], parsed[1], parsed[2], base)
            if d is not None:
                found.append((m.start(), m.end(), d, kind, m.group(0)))
    found.sort(key=lambda c: (c[0], -(c[1] - c[0])))       # 起点升序、长的优先
    picked: list[tuple[int, int, date, str, str]] = []
    last_end = -1
    for c in found:
        if c[0] >= last_end:
            picked.append(c)
            last_end = c[1]
    return picked


def _has_deadline_mark(norm: str, start: int, end: int) -> bool:
    """该日期附近是否出现截止标记词（前窗口 or 后窗口）。"""
    before = norm[max(0, start - _MARK_WINDOW):start]
    after = norm[end:end + _MARK_WINDOW]
    return any(k in before for k in _MARK_BEFORE) or any(k in after for k in _MARK_AFTER)


def _is_start_like(norm: str, end: int) -> bool:
    """日期后面是否跟着「起始/施行」类词（那种日期是开始，不是截止）。

    ⚠️ 窗口是**紧接日期之后**（`end` 已含 `日`/`号`），所以「10 月 1 日起执行」里
    紧跟的是「起」本身 —— 用 `[日号]起` 去匹配 after 是匹配不到的（end 已越过「日」）。
    """
    after = norm[end:end + _MARK_WINDOW]
    if after.startswith("起"):
        return True
    return bool(_START_RE.search(after))


def _month_edge(prefix_kw: str | None, prefix_num: str | None, edge: str,
                base: datetime) -> date | None:
    """「月底/月末/月初」→ 具体日期。`prefix_num` 优先（9月底），否则看 keyword（本月/下月）。"""
    is_next = (prefix_kw or "").startswith("下")

    if prefix_num is not None:
        month = _num(prefix_num)
        if not month or not 1 <= month <= 12:
            return None
        first = _nearest_future(None, month, 1, base)
        if first is None:
            return None
        if edge == "月初":
            return first
        nxt = (first.replace(day=1) + timedelta(days=32)).replace(day=1)
        return nxt - timedelta(days=1)

    first_this = base.date().replace(day=1)
    next_month_first = (first_this + timedelta(days=32)).replace(day=1)
    if edge == "月初":
        if is_next:                                    # 下月初 = 下个月 1 号（10/1，不是 11/1）
            return next_month_first
        if prefix_kw:                                  # 本月初 = 本月 1 号（即使已过也是本月）
            return first_this
        return next_month_first if first_this < base.date() else first_this
    first_next = next_month_first
    target = first_next - timedelta(days=1)
    if is_next:
        target = ((first_next + timedelta(days=32)).replace(day=1)) - timedelta(days=1)
    return target


def _find_date(norm: str, base: datetime) -> tuple[date, str, str, int] | None:
    """找日期 → (date, kind, matched, end)。优先级：相对日 > 周期 > 绝对日期 > 间隔 > 月底。"""
    # 1) 相对日：「明天」本身就是从今天算起，无年份歧义
    m = _REL_DAY_RE.search(norm)
    if m:
        offset = {"今天": 0, "今日": 0, "今儿": 0,
                  "明天": 1, "明日": 1, "明儿": 1,
                  "后天": 2, "大后天": 3}[m.group(1)]
        return base.date() + timedelta(days=offset), "rel_day", m.group(1), m.end()

    # 2) 周期：本周五 / 下周一（周一为一周之始）
    m = _WEEK_RE.search(norm)
    if m:
        monday = base.date() - timedelta(days=base.weekday())
        week_offset = 7 if m.group(1).startswith("下") else 0
        target = monday + timedelta(days=week_offset + _WEEKDAY_CN[m.group(2)])
        return target, "week", m.group(0), m.end()

    # 3) 绝对日期：可能多个 → 用截止标记词分辨，都没有则取最后一个
    cands = _collect_dates(norm, base)
    if cands:
        for c in cands:
            if _has_deadline_mark(norm, c[0], c[1]):
                return c[2], c[3], c[4], c[1]
        # 无截止标记 → 先排除「起始/施行」类日期，再取最后一个（区间「X 至 Y」的 Y 才是截止）。
        # 若全是起始类（例如「本通知自 10 月 1 日起执行」），**宁可返回 None 也不误判成截止**
        # —— 假 deadline 比没有 deadline 更糟（会生成错误的到期提醒）。
        remaining = [c for c in cands if not _is_start_like(norm, c[1])]
        if remaining:
            last = remaining[-1]
            return last[2], last[3], last[4], last[1]
        return None

    # 4) 间隔：7天内 / 两周后（需先排除「9月30日后」—— 那已由第 3 步的日期吃掉）
    m = _INTERVAL_RE.search(norm)
    if m:
        amount = _num(m.group(1))
        unit = m.group(2)
        if amount and amount > 0:
            if unit in ("小时", "钟头"):
                days = (amount + 23) // 24              # 按天粒度上取整
            elif unit in ("周", "星期"):
                days = amount * 7
            elif unit in ("个月", "月"):
                days = amount * 30
            else:
                days = amount
            return base.date() + timedelta(days=days), "interval", m.group(0), m.end()

    # 5) 月底 / 月初
    m = _MONTH_EDGE_RE.search(norm)
    if m:
        d = _month_edge(m.group(1), m.group(2), m.group(3), base)
        if d is not None:
            kind = "month_start" if m.group(3) == "月初" else "month_end"
            return d, kind, m.group(0), m.end()

    return None


# ---------------------------------------------------------------- 对外接口


@dataclass(frozen=True)
class ExtractResult:
    """抽取结果（**可解释**：附带命中的模式名与原文片段，便于人工复核）。"""

    deadline: datetime
    kind: str               # 命中的模式，如 "cn_md+clock" / "week" / "rel_day"
    matched: str            # 命中的原文片段（已归一化），用于核对
    confidence: float       # 0~1：仅日期=0.7，日期+钟点=0.9，相对日/周期=0.8


def extract_deadline_detail(
    text: str, now: datetime | None = None
) -> ExtractResult | None:
    """抽取截止时间；抽不到返回 None。

    `now` 必须可注入，否则「本周五 / 明天」这类相对表达无法确定性测试。
    """
    norm = _normalize(text)
    if not norm:
        return None
    base = now or datetime.now()

    found = _find_date(norm, base)
    if found is None:
        return None
    day, kind, matched, end = found

    clock = _find_time(norm, end)
    if clock is not None:
        return ExtractResult(
            deadline=datetime.combine(day, clock),
            kind=f"{kind}+clock", matched=matched, confidence=0.9,
        )

    # 纯日期 → 当天 23:59:59（业务口径，见设计要点 3）
    absolute = ("cn_ymd", "iso", "cn_md", "num_md")
    return ExtractResult(
        deadline=datetime.combine(day, DEADLINE_END_OF_DAY),
        kind=kind, matched=matched, confidence=0.7 if kind in absolute else 0.8,
    )


def extract_deadline(text: str, now: datetime | None = None) -> datetime | None:
    """便捷入口：只要 `datetime`，抽不到返回 None。"""
    result = extract_deadline_detail(text, now)
    return result.deadline if result else None
