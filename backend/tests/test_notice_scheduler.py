"""B18 分层推送调度：纯逻辑单元测试（不依赖数据库与 Celery）。

覆盖档位选择（含"调度停摆后恢复"的降级语义）、配置解析、私密标记判定与文案。
"""

from __future__ import annotations

from datetime import datetime

from app.services.notice_scheduler import (
    PRIVATE_TARGET_PREFIX,
    _marker,
    _stage_wording,
    _to_dt,
    due_stage,
    is_private_audience,
    parse_stages,
)


def test_due_stage_picks_most_urgent_eligible():
    stages = ["D7", "D2"]
    assert due_stage(8, stages) is None          # 还没进 7 天窗口
    assert due_stage(7, stages) == "D7"
    assert due_stage(5, stages) == "D7"
    assert due_stage(3, stages) == "D7"
    assert due_stage(2, stages) == "D2"
    assert due_stage(1, stages) == "D2"
    assert due_stage(0, stages) == "D2"          # D0 未启用时用最紧急的 D2
    assert due_stage(-5, stages) == "D2"


def test_due_stage_with_day0_enabled():
    stages = ["D7", "D2", "D0"]
    assert due_stage(2, stages) == "D2"
    assert due_stage(0, stages) == "D0"
    assert due_stage(-3, stages) == "D0"


def test_due_stage_catchup_does_not_replay_stale_stage():
    """调度停摆数天后恢复：只命中当前最紧急档位，不补发过期档位（防轰炸）。"""
    assert due_stage(1, ["D7", "D2"]) == "D2"
    assert due_stage(-1, ["D7", "D2"]) == "D2"


def test_parse_stages_normalizes_and_falls_back():
    assert parse_stages("d7, D2 ,xx") == ["D7", "D2"]
    assert parse_stages("D7，D0") == ["D7", "D0"]        # 全角逗号
    assert parse_stages("") == ["D7", "D2"]              # 全非法 → 默认
    assert parse_stages(["D0"]) == ["D0"]
    assert parse_stages(("d2",)) == ["D2"]


def test_private_audience_detection():
    assert is_private_audience(f"{PRIVATE_TARGET_PREFIX}reminder:12:D7") is True
    assert is_private_audience(f"{PRIVATE_TARGET_PREFIX}notice:5:D2") is True
    assert is_private_audience("2024级") is False
    assert is_private_audience("") is False
    assert is_private_audience(None) is False


def test_marker_format_is_stable():
    assert _marker("reminder", 12, "D7") == "__push:reminder:12:D7"
    assert _marker("notice", 5, "D2") == "__push:notice:5:D2"


def test_stage_wording_matches_real_days_left():
    deadline = datetime(2026, 9, 30, 18, 0)
    assert "还有 7 天" in _stage_wording("D7", 7, deadline)
    assert "明天到期" in _stage_wording("D2", 1, deadline)
    assert "今天到期" in _stage_wording("D2", 0, deadline)
    assert "已逾期 2 天" in _stage_wording("D0", -2, deadline)
    assert "2026-09-30 18:00" in _stage_wording("D7", 7, deadline)


def test_to_dt_accepts_datetime_and_strings():
    dt = datetime(2026, 9, 30, 18, 0)
    assert _to_dt(dt) == dt
    assert _to_dt("2026-09-30 18:00:00") == dt
    assert _to_dt("2026-09-30") == datetime(2026, 9, 30, 0, 0)
    assert _to_dt(None) is None
    assert _to_dt("不是时间") is None
