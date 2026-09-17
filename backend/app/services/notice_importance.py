"""通知重要度打分（1~5 分，**每条附命中依据**）。

为什么需要这个模块
------------------
任务卡 **`C28`（二阶段 W3）**：`campus_notice` 表已由 `B19` 加好 `importance` 列，
但**没有任何代码写入** —— 于是通知列表只能按时间倒序，考务/缴费类的高价值通知
会被"新书推荐""活动预告"淹没；前端也无法做"重要通知置顶/红点"。

本模块给出**可解释**的规则 baseline：每条打分都附带命中的依据（`reasons`），
便于人工复核、页面展示"为什么重要"，也让后续调参有据可依。

方案选型
--------
| 方案 | 取舍 |
|---|---|
| **规则打分（本模块默认）** | **零依赖**、确定性、**可解释**（每个分数都能追到具体依据）、可离线单测 |
| LLM 打分 | 单条成本高、不确定；且"为什么 4 分"难以稳定复现，不利于前端口径统一 |
| 微调分类模型 | 科研主线（`C31`~`C36`）的目标之一，但需先有标注集；本模块同样充当其 baseline |

设计要点
--------
1. **五维信号，累加后夹到 1~5**：事务类别 / 后果强度 / 影响面 / 时效紧迫度 / 提醒语义。
   每维命中都写入 `reasons`，`signals` 保留分维度贡献，便于解释与调参。
2. **类别匹配是"最高优先级命中"**：`critical` > `major` > `minor`，**同类内只取一次**
   —— 否则"关于选课考试安排的通知"会因为命中多个词被重复加权，分数虚高。
3. **时效紧迫度与 `C27` 解耦**：`deadline` 由**调用方传入**（`notice_extract` 或别处产生），
   本模块不自己解析时间 —— 两个模块可各自独立上线，组合使用时效果更好。
4. **确定性**：`now` 可注入；同一输入必得同一分数与同一组依据。

用法
----
>>> score_importance("国家奖学金申请材料提交提醒",
...                  "请符合条件的同学于 9 月 30 日前提交，逾期不再受理。").score
5
>>> r = score_importance("校历已发布")
>>> r.score, r.reasons
(1, ('无强信号：信息性通知',))

⚠️ 本 docstring 里的示例由 `tests/test_notice_importance.py::test_module_doctests_pass`
   实际执行 —— 改评分逻辑时改坏了这里，测试会红（此前这个示例是错的且无人发现，
   因为仓库没开 `--doctest-modules`）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

__all__ = ["ImportanceResult", "score_importance", "DEFAULT_SCORE"]

# 基准分：普通通知 1 分起步，再按各维信号累加（最后夹到 MIN~MAX）
DEFAULT_SCORE = 1
MIN_SCORE = 1
MAX_SCORE = 5

# ---------------------------------------------------------------- 信号词表
# 事务类别：按「影响学习/毕业/安全/钱财」的程度分级，**同类内只取第一个命中**
_CATEGORY_CRITICAL = (
    "选课", "退课", "考试", "补考", "重修", "成绩", "毕业", "学位", "论文", "答辩",
    "学费", "缴费", "欠费", "奖学金", "助学金", "助学贷款", "档案", "学籍", "注册",
    "停电", "停水", "停暖", "消防", "安全", "防疫", "疫苗", "体检", "心理健康",
    "一卡通", "户籍", "身份证", "宿舍", "退宿", "报到", "实习协议",
)
_CATEGORY_MAJOR = (
    "报名", "申请", "审核", "材料", "推荐", "双选", "招聘", "实习", "面试",
    "闭馆", "开放时间", "检修", "升级", "维护", "施工", "调整", "迁移", "搬迁",
    "会议", "宣讲", "培训", "招募", "认证", "证书", "四六级", "竞赛",
)
_CATEGORY_MINOR = (
    "活动", "讲座", "比赛", "社团", "展览", "演出", "新书", "推荐书目", "读书",
    "沙龙", "分享会", "开放日", "预告", "花絮", "征稿", "摄影", "运动会",
)
# ⚠️ **词表重叠（已知，未改）**：类别判定是「critical → major → minor，命中即 `break`」，
#    所以**短词会遮住同族的长词**。实测本表的 `"推荐书目"` 被 `_CATEGORY_MAJOR` 的
#    `"推荐"` 完全遮住 —— 任何含 `"推荐书目"` 的文本必然也含 `"推荐"`，
#    于是这条**永远不可能命中**（已加测试 `test_shadowed_minor_entry_never_fires` 钉住现状）。
#
#    这是**语义问题而非笔误**：`"推荐"` 在校园语境里歧义很大 ——
#    「新书推荐 / 推荐书目」是信息性的（该判 minor），而「推荐免试研究生」是重要事务。
#    把它放 MAJOR 会系统性抬高书单类通知的分。**改它会影响打分结果，属产品决策**，
#    故本轮只记录不改；若要修，建议把 `"推荐"` 从词表撤掉、改用更具体的词
#    （如 `"推免"/"推荐免试"` 归 MAJOR，`"推荐书目"/"新书推荐"` 归 MINOR）。

# 后果强度：写明了「不照做会怎样」的通知，重要度显著更高
_CONSEQUENCE_STRONG = (
    "逾期不再受理", "不予受理", "不再受理", "取消资格", "视为放弃", "影响毕业",
    "记入档案", "计入信用", "记入信用", "后果自负", "统一收缴", "封停", "停用",
    "停课", "处分", "追责", "作废", "责任自负",
)
_CONSEQUENCE_WEAK = ("务必", "必须", "严禁", "禁止", "不得", "否则", "逾期", "限时")

# 影响面
_AUDIENCE_WIDE = ("全校", "全体", "所有同学", "广大同学", "各学院", "各班级", "师生", "本科生", "研究生")
_AUDIENCE_NARROW = ("个别", "部分同学", "某班", "少数")

# 提醒语义（通知是为了让人"去做一件事"）
_REMINDER = ("提醒", "请于", "请及时", "务必于", "截止", "最后期限", "尽快", "抓紧")

# 时效紧迫（文本内直接出现的紧迫词）
_URGENT_TEXT = ("紧急", "立即", "马上", "今天", "今日", "明天", "明日", "本周内", "三日内", "24小时")

# 无强信号时的兜底依据
_NEUTRAL_REASON = "无强信号：信息性通知"


@dataclass(frozen=True)
class ImportanceResult:
    """打分结果（**可解释**：`reasons` 说明每一分为何而来）。"""

    score: int                                   # 1~5
    reasons: tuple[str, ...]                     # 命中依据（人类可读）
    signals: dict[str, int] = field(default_factory=dict)   # 各维度贡献，便于调参


def _first_hit(text: str, words: tuple[str, ...]) -> str | None:
    """返回第一个命中的词（按词表顺序，保证确定性）。"""
    for w in words:
        if w in text:
            return w
    return None


def _as_naive_datetime(value) -> datetime | None:
    """把 `deadline` / `now` 归一到**本地 naive datetime**，避免相减时炸。

    为何要这一步：`(deadline - ref).total_seconds()` 对下列输入会抛 `TypeError`，
    而调用方（`notice_extract` / 上游 DAO）拿到的形态并不统一：

        tz-aware deadline + naive now  -> TypeError: can't subtract offset-naive and offset-aware
        date 对象（非 datetime）        -> TypeError: unsupported operand type(s) for -
        ISO 字符串                     -> TypeError: unsupported operand type(s) for -

    本模块的定位是「打分」，不该因为上游多给了一个 `tzinfo` 就整个挂掉 ——
    所以在这里统一收口：aware 转本地再去 tz；`date` 补成当天零点；字符串按 ISO 解析。
    仍解析不了的直接返回 `None`（等同于"没有截止时间"），**不抛异常**。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone().replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date):                      # datetime 是 date 子类，前面已拦
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        try:
            return _as_naive_datetime(datetime.fromisoformat(value.strip()))
        except ValueError:
            return None
    return None


def _urgency_from_deadline(deadline, now) -> int:
    """按「距截止还有多久」给紧迫分（0~2）。

    接受 `datetime` / `date` / ISO 字符串（见 `_as_naive_datetime`）；
    一律归一后再相减，故 tz-aware 与 naive 混用不会再抛 `TypeError`。
    """
    dl = _as_naive_datetime(deadline)
    if dl is None:
        return 0
    ref = _as_naive_datetime(now) or datetime.now()
    days = (dl - ref).total_seconds() / 86400.0
    if days < 0:
        return 0          # 已过期不再计入紧迫（不该让它刷高分）
    if days <= 1:
        return 2
    if days <= 3:
        return 1
    return 0


def score_importance(
    title: str,
    content: str = "",
    *,
    deadline: datetime | None = None,
    now: datetime | None = None,
) -> ImportanceResult:
    """给一条通知打 1~5 分，并给出每一分的依据。

    `deadline` 由调用方传入（通常来自 `notice_extract.extract_deadline`）——
    本模块**不自己解析时间**，以保证两个模块可独立上线（见设计要点 3）。
    """
    text = f"{title or ''} {content or ''}"
    score = DEFAULT_SCORE
    reasons: list[str] = []
    signals: dict[str, int] = {"base": DEFAULT_SCORE}

    # 1) 事务类别：critical > major > minor，同类只取一次
    for label, words, weight in (
        ("关键事务", _CATEGORY_CRITICAL, 2),
        ("重要事务", _CATEGORY_MAJOR, 1),
        ("一般事务", _CATEGORY_MINOR, 0),
    ):
        hit = _first_hit(text, words)
        if hit:
            if weight:
                score += weight
                signals["category"] = weight
                reasons.append(f"涉及{label}：{hit}")
            else:
                signals.setdefault("category", 0)
                reasons.append(f"仅涉及{label}：{hit}")
            break

    # 2) 后果强度
    strong = _first_hit(text, _CONSEQUENCE_STRONG)
    weak = _first_hit(text, _CONSEQUENCE_WEAK)
    if strong:
        score += 2
        signals["consequence"] = 2
        reasons.append(f"写明后果：{strong}")
    elif weak:
        score += 1
        signals["consequence"] = 1
        reasons.append(f"强制措辞：{weak}")

    # 3) 影响面
    wide = _first_hit(text, _AUDIENCE_WIDE)
    narrow = _first_hit(text, _AUDIENCE_NARROW)
    if wide:
        score += 1
        signals["audience"] = 1
        reasons.append(f"面向面广：{wide}")
    elif narrow:
        score -= 1
        signals["audience"] = -1
        reasons.append(f"影响面窄：{narrow}")

    # 4) 时效紧迫
    urgent = _first_hit(text, _URGENT_TEXT)
    by_deadline = _urgency_from_deadline(deadline, now)
    urgency = min(2, (1 if urgent else 0) + by_deadline)
    if urgency:
        score += urgency
        signals["urgency"] = urgency
        if by_deadline:
            reasons.append(f"距截止很近（+{by_deadline}）")
        if urgent:
            reasons.append(f"紧迫措辞：{urgent}")

    # 5) 提醒语义
    remind = _first_hit(text, _REMINDER)
    if remind:
        score += 1
        signals["reminder"] = 1
        reasons.append(f"提醒办理：{remind}")

    final = max(MIN_SCORE, min(MAX_SCORE, score))
    if not reasons:
        reasons.append(_NEUTRAL_REASON)
    return ImportanceResult(score=final, reasons=tuple(reasons), signals=signals)
