"""`C27` 通知时间实体抽取 —— 行为锁 + 准确率验收。

评测集：`tests/fixtures/notice_time_cases.json`（40 条，含 5 条负样本）。
基准时间 `2026-09-15T10:00:00`（**周二**）—— 相对表达（本周五 / 明天）必须靠它
才能有确定结果，因此 `extract_deadline(text, now=...)` 的 `now` 注入面是必测项。

验收标准（任务卡 `C27`）：准确率 **≥ 85%**。

为什么这个文件里既有「逐条 parametrize」又有「准确率断言」
--------------------------------------------------------
逐条断言负责**定位**（哪条错、错在哪个错），准确率断言负责**验收**（整体指标）。
只留其一会退化成：「全绿但不知道指标」或「知道指标但不知错在哪」。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from app.services.notice_extract import (
    DEADLINE_END_OF_DAY,
    extract_deadline,
    extract_deadline_detail,
)

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "notice_time_cases.json"
_DATA = json.loads(_FIXTURE.read_text(encoding="utf-8"))
BASE = datetime.fromisoformat(_DATA["base"])          # 2026-09-15T10:00:00（周二）
CASES = _DATA["cases"]


def _expected(case: dict) -> datetime | None:
    return datetime.fromisoformat(case["expect"]) if case["expect"] else None


# ---------------------------------------------------------------- 评测集逐条

@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_case_matches_fixture(case: dict) -> None:
    got = extract_deadline(case["text"], now=BASE)
    assert got == _expected(case), (
        f"[{case['id']}·{case['group']}] {case['text']}\n"
        f"  期望 {_expected(case)}\n  实际 {got}"
    )


def test_accuracy_meets_c27_target(capsys) -> None:
    """验收：准确率 ≥ 85%，并打印分组明细（可写进验收报告）。"""
    hits = 0
    misses: list[str] = []
    by_group: dict[str, list[int]] = {}
    for case in CASES:
        ok = extract_deadline(case["text"], now=BASE) == _expected(case)
        bucket = by_group.setdefault(case["group"], [0, 0])
        bucket[0] += int(ok)
        bucket[1] += 1
        hits += int(ok)
        if not ok:
            misses.append(case["id"])
    rate = hits / len(CASES)

    with capsys.disabled():                      # 指标要能在 -q 下也看得到
        print(f"\n[C27] 时间实体抽取准确率 = {hits}/{len(CASES)} = {rate:.1%}")
        for group, (h, n) in sorted(by_group.items()):
            print(f"       {group:<14} {h}/{n}")
        if misses:
            print(f"       未命中：{', '.join(misses)}")

    assert rate >= 0.85, f"准确率 {rate:.1%} 低于验收线 85%（未命中：{misses}）"


# ---------------------------------------------------------------- 行为锁

def test_negative_cases_return_none() -> None:
    """负样本必须**抽不到** —— 否则会给无截止时间的通知编出一个假 deadline。"""
    negatives = [c for c in CASES if c["group"] == "negative"]
    assert negatives, "评测集里必须有负样本"
    for case in negatives:
        assert extract_deadline(case["text"], now=BASE) is None, case["text"]


def test_is_deterministic() -> None:
    """同一输入 + 同一 `now` 必须恒定（规则引擎的立身之本）。"""
    text = "请于9月30日前提交材料"
    results = {extract_deadline(text, now=BASE) for _ in range(50)}
    assert results == {datetime(2026, 9, 30, 23, 59, 59)}


def test_result_is_explainable() -> None:
    """`kind` / `matched` / `confidence` 是可解释依据，供人工复核与回归定位。"""
    plain = extract_deadline_detail("报名9月25日截止", now=BASE)
    assert plain is not None
    assert plain.kind == "cn_md"
    assert plain.matched == "9月25日"
    assert plain.confidence == 0.7

    with_clock = extract_deadline_detail("9月30日17:00前提交", now=BASE)
    assert with_clock is not None
    assert with_clock.kind == "cn_md+clock"
    assert with_clock.confidence == 0.9

    relative = extract_deadline_detail("明天截止", now=BASE)
    assert relative is not None and relative.kind == "rel_day"


def test_clock_overrides_end_of_day() -> None:
    """有钟点用钟点；只有日期才是当天 23:59:59（设计要点 3）。"""
    assert extract_deadline("9月30日17:00前提交", now=BASE) == datetime(2026, 9, 30, 17, 0)
    assert extract_deadline("9月30日前提交", now=BASE) == datetime.combine(
        date(2026, 9, 30), DEADLINE_END_OF_DAY
    )


def test_invalid_date_is_ignored() -> None:
    """不存在的日期必须返回 None，不能抛异常、也不能被"修正"成相近日期。"""
    assert extract_deadline("2月30日前提交", now=BASE) is None
    assert extract_deadline("13月40日截止", now=BASE) is None


def test_empty_or_none_input() -> None:
    assert extract_deadline("", now=BASE) is None
    assert extract_deadline("   \n  ", now=BASE) is None
    assert extract_deadline(None, now=BASE) is None          # type: ignore[arg-type]


def test_marker_word_picks_the_deadline_not_the_event_date() -> None:
    """多日期时靠截止标记词分辨（设计要点 1）—— 且与出现顺序无关。"""
    assert extract_deadline("活动9月20日举行，报名9月25日截止", now=BASE) == datetime(
        2026, 9, 25, 23, 59, 59
    )
    # 反向对照：把两个日期调换顺序，仍然必须选「截止」那个
    assert extract_deadline("报名9月25日截止，活动9月20日举行", now=BASE) == datetime(
        2026, 9, 25, 23, 59, 59
    )


def test_without_marker_takes_last_date() -> None:
    """无标记时取最后一个日期 —— 区间「X 至 Y」的结束端才是截止。"""
    assert extract_deadline("9月20日至9月25日报名", now=BASE) == datetime(
        2026, 9, 25, 23, 59, 59
    )


def test_no_year_takes_nearest_future() -> None:
    """无年份 ⇒ 取最近的将来（设计要点 2）。"""
    assert extract_deadline("9月30日前提交", now=BASE).year == 2026        # 还没到 ⇒ 今年
    assert extract_deadline("9月30日前提交", now=datetime(2026, 10, 1, 10, 0)).year == 2027


def test_weekday_does_not_shift_when_already_passed() -> None:
    """裸周几即使已过也不顺延（设计要点 4）—— 否则过期通知会显示"还没到期"。"""
    friday = datetime(2026, 9, 18, 10, 0)                # 周五当天
    assert extract_deadline("周五前完成", now=friday).date() == date(2026, 9, 18)
    sunday = datetime(2026, 9, 20, 10, 0)                # 周日当天
    assert extract_deadline("周五前完成", now=sunday).date() == date(2026, 9, 18)


def test_full_width_and_whitespace_are_normalized() -> None:
    """全角数字/冒号、夹空白的写法必须与半角等价（通知正文常从 Word 粘贴）。"""
    assert extract_deadline("９月３０日 １７：００ 前提交", now=BASE) == datetime(
        2026, 9, 30, 17, 0
    )
