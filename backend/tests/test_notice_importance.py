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

from app.services.notice_importance import MAX_SCORE, MIN_SCORE, score_importance

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

def test_score_always_within_1_to_5() -> None:
    for case in CASES:
        assert MIN_SCORE <= _score(case).score <= MAX_SCORE


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
    assert MIN_SCORE <= result.score <= MAX_SCORE


def test_neutral_notice_gets_fallback_reason() -> None:
    """真正无信号时，给兜底依据而不是空 tuple（验收要求"每条附依据"）。"""
    result = score_importance("关于校园环境美化的说明", "近期对绿化带进行补种。", now=BASE)
    assert result.score == MIN_SCORE
    assert result.reasons == ("无强信号：信息性通知",)


def test_reasons_are_readable_and_match_signals() -> None:
    result = score_importance("紧急通知：全校停电检修", "明日上午全校停电。", now=BASE)
    assert result.score >= 4
    assert any("停电" in r for r in result.reasons)      # 依据要能追到具体词
    assert all(isinstance(r, str) and r for r in result.reasons)
    assert result.signals.get("urgency", 0) >= 1


def test_empty_input_is_safe() -> None:
    result = score_importance("", "", now=BASE)
    assert result.score == MIN_SCORE
    assert result.reasons
