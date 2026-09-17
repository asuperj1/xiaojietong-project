"""`C28` 通知重要度打分 —— 行为锁 + 合理性评测。

**任务卡验收原文**：「1~5 分；**每条附命中依据**（可解释）」——
因此本文件除了"分数对不对"，还专门断言"**依据必须存在且可读**"。

评测集：`tests/fixtures/notice_importance_cases.json`（20 条，取材于 `campus_notice` 真实文本）。
期望值是**区间**而非精确值：打分是启发式，锁死精确值会过拟合到当前阈值上，
后续调参时会变成"改一个词就红一片"。

基准时间 `2026-09-15T10:00:00`，用于 `deadline` 驱动的紧迫度。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.services import notice_importance
from app.services.notice_importance import score_importance

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "notice_importance_cases.json"
_DATA = json.loads(_FIXTURE.read_text(encoding="utf-8"))
BASE = datetime.fromisoformat(_DATA["base"])
CASES = _DATA["cases"]


def _deadline(case: dict) -> datetime | None:
    return datetime.fromisoformat(case["deadline"]) if case.get("deadline") else None


def _score(case: dict):
    return score_importance(
        case["title"], case["content"], deadline=_deadline(case), now=BASE
    )


# ---------------------------------------------------------------- 评测集

@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_case_within_expected_band(case: dict) -> None:
    result = _score(case)
    low, high = case["expected_band"]
    assert low <= result.score <= high, (
        f"[{case['id']}·{case['group']}] {case['title']}\n"
        f"  期望 {low}~{high}，实际 {result.score}\n  依据 {result.reasons}"
    )


def test_every_notice_has_reasons() -> None:
    """验收硬指标：**每条通知都必须给出依据**（否则前端无法解释"为什么置顶"）。"""
    missing = [c["id"] for c in CASES if not _score(c).reasons
               or all(not r.strip() for r in _score(c).reasons)]
    assert not missing, f"这些通知没有任何依据：{missing}"


def test_band_agreement_rate(capsys) -> None:
    """合理性指标（非任务卡硬指标）—— 作为后续调参时可对比的基线数字。"""
    hit = [c["id"] for c in CASES
           if c["expected_band"][0] <= _score(c).score <= c["expected_band"][1]]
    rate = len(hit) / len(CASES)
    with capsys.disabled():
        print(f"\n[C28] 打分落在期望区间 = {len(hit)}/{len(CASES)} = {rate:.1%}")
    assert rate >= 0.85, f"落区间率 {rate:.1%} 低于 85%（未落区间：{set(c['id'] for c in CASES) - set(hit)}）"


# ---------------------------------------------------------------- 行为锁

def test_score_scale_is_literally_1_to_5() -> None:
    """验收原文是「**1~5 分**」—— 这里写**字面量**，不去 import 被测模块的常量。

    原写法 `assert MIN_SCORE <= s <= MAX_SCORE` 是**循环论证**：把 `MIN_SCORE` 改成 2、
    `MAX_SCORE` 改成 4，真实分数跟着变，用例照样全绿（实测）。尺度是**对外的契约**，
    必须由测试独立钉住，而不是问实现"你的尺度是多少"。
    """
    assert (notice_importance.MIN_SCORE, notice_importance.MAX_SCORE) == (1, 5)
    for case in CASES:
        assert 1 <= _score(case).score <= 5


def test_max_score_is_actually_reachable() -> None:
    """反向对照：上界 5 必须**真的能被打到** —— 否则"1~5 分"实际是"1~4 分"。"""
    top = score_importance(
        "紧急：国家奖学金申请材料逾期不再受理",
        "请全体同学今日立即提交，逾期不再受理，未交者取消资格。",
        now=BASE,
    )
    assert top.score == 5, (top.score, top.reasons, top.signals)


def test_min_score_is_actually_floored() -> None:
    """下界 1 的 clamp **生产可达**（原 fixture 里从未触发，属零覆盖）。

    裸分 = base(1) + 影响面窄(-1) = 0，靠 `max(MIN_SCORE, ...)` 兜回 1。
    """
    r = score_importance("个别班级活动通知", "", now=BASE)
    assert r.signals.get("audience") == -1, r.signals
    assert r.score == 1


def test_deterministic() -> None:
    """同输入必得同分数、同依据（规则打分的立身之本）。

    注：`ImportanceResult.signals` 是 dict ⇒ 结果对象本身不可哈希，
    故比较 `(score, reasons)` —— 这两个正是对外的可观测面。
    """
    case = CASES[0]
    seen = {(_score(case).score, _score(case).reasons) for _ in range(30)}
    assert len(seen) == 1


def test_category_weights_only_once() -> None:
    """类别**只取一次**：命中多个关键词不得重复加权，否则"罗列式通知"会虚高。"""
    one = score_importance("期末考试安排", "", now=BASE)
    many = score_importance("期末考试、奖学金、学位论文与选课安排汇总", "", now=BASE)
    assert one.score == many.score, (one, many)


def test_critical_outranks_major_category() -> None:
    """同时含关键与重要事务时，按**关键**事务计一次。"""
    result = score_importance("选课报名通知", "", now=BASE)
    assert result.signals.get("category") == 2


def test_expired_deadline_does_not_inflate_score() -> None:
    """已过期的 deadline 不得靠"紧迫"刷高分 —— 那是历史通知，不该压住新通知。"""
    past = score_importance("提交材料", "请在系统内提交",
                            deadline=BASE - timedelta(days=1), now=BASE)
    soon = score_importance("提交材料", "请在系统内提交",
                            deadline=BASE + timedelta(hours=6), now=BASE)
    assert past.score < soon.score


def test_deadline_is_optional() -> None:
    """与 `C27` 解耦：不传 deadline 也能独立工作（两个模块可各自上线）。"""
    result = score_importance("期末考试安排", now=BASE)
    assert 1 <= result.score <= 5


def test_neutral_notice_gets_fallback_reason() -> None:
    """真正无信号时，给兜底依据而不是空 tuple（验收要求"每条附依据"）。"""
    result = score_importance("关于校园环境美化的说明", "近期对绿化带进行补种。", now=BASE)
    assert result.score == 1          # 字面量：见 test_score_scale_is_literally_1_to_5
    assert result.reasons == ("无强信号：信息性通知",)


def test_reasons_are_readable_and_match_signals() -> None:
    result = score_importance("紧急通知：全校停电检修", "明日上午全校停电。", now=BASE)
    assert result.score >= 4
    assert any("停电" in r for r in result.reasons)      # 依据要能追到具体词
    assert all(isinstance(r, str) and r for r in result.reasons)
    assert result.signals.get("urgency", 0) >= 1


def test_empty_input_is_safe() -> None:
    result = score_importance("", "", now=BASE)
    assert result.score == 1
    assert result.reasons


# ==================== 补零覆盖分支（评审 P1：28/57 变异存活的主要来源）=========
#
# 原来 20 条 fixture 里：**弱后果分支命中 0 次**、**1~3 天 deadline 档从未走到**、
# **urgency 的上限 min(2,…) 从未触发**。缺了这三条，把对应分支删掉/改权重
# 测试都不会红（实测：`_CONSEQUENCE_WEAK` 整张词表删空 → 31 条仍全绿）。


def test_weak_consequence_branch_adds_exactly_one() -> None:
    """**弱后果**分支（`_CONSEQUENCE_WEAK`，+1）—— 原 fixture 零命中。

    与强后果（`_CONSEQUENCE_STRONG`，+2）分别断言，确保两条分支都存在且权重不同。
    """
    weak = score_importance("图书馆座位预约提醒", "请务必按时到场，否则座位将被释放。", now=BASE)
    assert weak.signals.get("consequence") == 1, weak.signals
    assert any("强制措辞" in r for r in weak.reasons)

    strong = score_importance("图书馆座位预约提醒", "逾期不再受理。", now=BASE)
    assert strong.signals.get("consequence") == 2, strong.signals
    assert strong.score > weak.score, "强后果应比弱后果高"


def test_deadline_bucket_1_to_3_days() -> None:
    """`_urgency_from_deadline` 的 **1~3 天档（返回 1）** —— 原 fixture 从未走到。

    三个档位分别钉住：>3 天 → 0、1~3 天 → 1、≤1 天 → 2。
    """
    for days, expect in ((5, 0), (2, 1), (0.5, 2)):
        r = score_importance("提交材料", "请在系统内提交",
                             deadline=BASE + timedelta(days=days), now=BASE)
        assert r.signals.get("urgency", 0) == expect, (days, r.signals, r.reasons)


def test_urgency_is_capped_at_2() -> None:
    """`urgency = min(2, 紧迫词(1) + 截止临近(2))` —— 上限必须真的生效。

    原 fixture 从未触发该上限（评审实测：`min(2,…)` 改成 `min(1,…)`/`min(3,…)` 全绿）。
    """
    both = score_importance("紧急通知", "请立即处理，逾期不再受理",
                            deadline=BASE + timedelta(hours=2), now=BASE)
    assert both.signals.get("urgency") == 2, both.signals   # 1 + 2 被夹到 2，不是 3

    only_urgent = score_importance("紧急通知", "请立即处理", now=BASE)
    assert only_urgent.signals.get("urgency") == 1, only_urgent.signals


def test_reminder_signal_fires_and_adds_one() -> None:
    """`_REMINDER`（提醒办理，+1）—— 原 fixture 未单独钉住该信号。

    变异实测：把 `_REMINDER` 整张词表删空，改造前 31 条全绿。
    """
    with_reminder = score_importance("关于选课的通知", "请于 9 月 20 日前完成选课", now=BASE)
    assert with_reminder.signals.get("reminder") == 1, with_reminder.signals
    assert any("提醒办理" in r for r in with_reminder.reasons)

    without = score_importance("关于选课的通知", "选课系统已开放", now=BASE)
    assert "reminder" not in without.signals, without.signals
    assert with_reminder.score == without.score + 1, "提醒语义应恰好 +1"


def test_audience_wide_signal_adds_one() -> None:
    """`_AUDIENCE_WIDE`（影响面广，+1）—— 与窄影响面（-1）对称钉住。

    变异实测：把 `_AUDIENCE_WIDE` 整张词表删空，改造前全绿（`_AUDIENCE_NARROW` 那条
    能拦是因为下界 clamp 兜住了，并不是这条用例在起作用）。
    """
    wide = score_importance("关于选课的通知", "请全体同学注意", now=BASE)
    assert wide.signals.get("audience") == 1, wide.signals
    assert any("面向面广" in r for r in wide.reasons)


def test_minor_category_adds_zero_points() -> None:
    """`_CATEGORY_MINOR`（一般事务）权重是 **0**：只留一条依据，**不加分**。

    原 fixture 里没有"仅命中 MINOR"的用例 ⇒ 把权重 0 改成 1 不会被发现（变异实测存活）。
    """
    minor = score_importance("图书馆讲座预告", "", now=BASE)   # 「讲座」「预告」均属 MINOR
    assert minor.signals.get("category") == 0, minor.signals
    assert any("仅涉及一般事务" in r for r in minor.reasons), minor.reasons
    assert minor.score == 1, minor


# ============================ 类别词表：短词遮蔽长词（评审：新发现）==========


def test_shadowed_minor_entry_never_fires() -> None:
    """⚠️ 记录一个**已知的语义问题**（本轮只记录、未改，属产品决策）。

    类别判定是「critical → major → minor，命中即 `break`」，所以短词会遮住同族长词。
    实测 `_CATEGORY_MINOR` 里的 `"推荐书目"` 被 `_CATEGORY_MAJOR` 的 `"推荐"` **完全遮住** ——
    任何含 `"推荐书目"` 的文本必然也含 `"推荐"` ⇒ 那条**永远不可能命中**。

    本条用例的作用是**把现状钉住**：将来若有人调整词表让 `推荐书目` 能命中，
    这里会红，从而迫使那次调整是**有意的**而不是顺手的。

    另外注意 `"推荐"` 在校园语境里歧义很大（「新书推荐」信息性 vs「推荐免试研究生」重要事务），
    放在 MAJOR 会系统性抬高书单类通知的分。
    """
    assert "推荐书目" in notice_importance._CATEGORY_MINOR
    shadowed = [m for m in notice_importance._CATEGORY_MAJOR if m in "推荐书目"]
    assert shadowed == ["推荐"], f"遮蔽关系变了：{shadowed}（若已修好请更新本条用例）"

    r = score_importance("图书馆推荐书目已更新", "", now=BASE)
    assert r.signals.get("category") == 1, r.signals        # 判成 MAJOR（+1），不是 MINOR（+0）
    assert "重要事务" in r.reasons[0]


# ============================ deadline / now 的类型与 tz 归一（评审 P3）=======


def test_deadline_type_and_timezone_are_normalized() -> None:
    """ `deadline` 的四种形态都不应抛异常（评审 P3：原来全部 `TypeError`）。

    上游形态并不统一：`notice_extract` 给 naive、DAO 可能给 `date`、JSON 来的可能是字符串。
    本模块的定位是「打分」，不该因为多了一个 `tzinfo` 就整个挂掉。
    """
    from datetime import date

    # 四种形态都要落在**同一个紧迫档（≤1 天 ⇒ urgency 2）**：
    #   naive / tz-aware / ISO 是 BASE 当天 14:00（距 BASE 4 小时）
    #   date 会被补成当日零点 —— 必须取**次日**，否则 15 日零点早于 BASE(15 日 10:00) 会被判过期
    same_instant = datetime(2026, 9, 15, 14, 0, 0)
    expected = None
    for label, dl in (
        ("naive", same_instant),
        ("tz-aware（本地时区）", same_instant.astimezone()),
        ("date 对象", date(2026, 9, 16)),          # 16 日零点，距 BASE 14 小时
        ("ISO 字符串", "2026-09-15T14:00:00"),
    ):
        r = score_importance("提交材料", "请在系统内提交", deadline=dl, now=BASE)
        assert r.signals.get("urgency") == 2, (label, r.signals)
        expected = r.score if expected is None else expected
        assert r.score == expected, f"{label} 与其它形态结果不一致"


def test_unparseable_deadline_degrades_instead_of_raising() -> None:
    """反向对照：解析不了的 deadline **降级成"没有截止时间"**，而不是抛异常。"""
    r = score_importance("提交材料", "", deadline="不是日期", now=BASE)
    assert r.signals.get("urgency", 0) == 0

    r2 = score_importance("提交材料", "", deadline=None, now=BASE)
    assert r2.score == r.score


# ============================ docstring 由测试执行 ==========================


def test_module_doctests_pass() -> None:
    """把模块 docstring 里的示例**纳入 CI**。

    此前 docstring 的示例是错的（写 `图书馆新书推荐 → 1 分`，实际 2 分），且**无人发现** ——
    因为仓库没开 `--doctest-modules`，doctest 从不运行。这条用例把它接上。
    """
    import doctest

    result = doctest.testmod(notice_importance, verbose=False)
    assert result.failed == 0, f"docstring 示例与实际行为不符：{result.failed} 条失败"
    assert result.attempted > 0, "docstring 里没有可执行的示例（等于没测）"
