# -*- coding: utf-8 -*-
"""B29 通知接入抽取 + B30 落库回填 —— 单元 + 真库集成测试。

两条验收口径在这里被直接钉住：

- B29：「入库后 `deadline`/`materials` 被自动填充」→ `test_ingest_*`
- B30：「**抽取失败不阻塞入库**（降级为空值）」→ `test_extraction_failure_*`
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from app.db import cpp_bridge
from app.services import notice_ingest
from app.services.notice_ingest import (
    backfill_notices,
    extract_materials,
    extract_materials_detail,
    ingest_notice,
)

BASE = datetime(2026, 9, 15, 10, 0)


# ------------------------------------------------------------------ 夹具 ----

@pytest.fixture(autouse=True)
def _ensure_pool(client):
    """借 `client` 夹具触发 lifespan 里的连接池初始化 —— 本文件的真库用例都需要它。

    （纯函数用例也会跟着走这一步：环境不可用时整体 skip，与 conftest 的约定一致。）
    """
    return client


@pytest.fixture
def cleanup_notices():
    """收集本用例写入的 `campus_notice` id，结束后删除（不碰别人的数据）。"""
    created: list[int] = []
    yield created
    if created:
        placeholders = ",".join("?" for _ in created)
        cpp_bridge.execute(f"DELETE FROM campus_notice WHERE id IN ({placeholders})", created)


def _insert_blank_notice(title: str, content: str) -> int:
    """直接插一条**不带扩展字段**的通知 —— 模拟 B29 之前的存量数据。"""
    _, nid = cpp_bridge.execute(
        "INSERT INTO campus_notice (title, content, source, category, target_grade) "
        "VALUES (?, ?, 'pytest', '测试', '')",
        [title, content],
    )
    return int(nid)


def _row(nid: int) -> dict:
    rows = cpp_bridge.query(
        "SELECT deadline, materials, importance FROM campus_notice WHERE id = ?", [nid]
    )
    assert rows, f"通知 {nid} 不存在"
    return rows[0]


def _nullish(value) -> bool:
    """jt_db 把 SQL NULL 读成空串（B21 那两个接口也踩过同一个坑），统一判「空」。"""
    return value is None or value == ""


# ============================================== 材料清单抽取（纯函数）====

@pytest.mark.parametrize("text,expected", [
    ("报名需提交材料：成绩单、推荐信", "成绩单、推荐信"),
    ("办理时需携带：校园卡", "校园卡"),
    ("材料清单：报名表", "报名表"),
    ("所需材料：一寸照片 2 张", "一寸照片 2 张"),
    ("需提交材料：身份证、学生证、成绩单", "身份证、学生证、成绩单"),
])
def test_extract_materials_hits(text, expected):
    assert extract_materials(text) == expected


@pytest.mark.parametrize("text", [
    "",
    "   ",
    None,
    # 裸的「材料」不是标记词 —— 否则这两个最常见的误命中会一直抽错
    "材料科学与工程学院关于举办学术讲座的通知",
    "材料力学课程调整通知",
    # 没写成「标记词 + 冒号」的形式就不抽：中文没有词边界，硬猜会把说明当成材料
    "请携带身份证、学生证到教务处办理",
    "所需材料 一寸照片 2 张",
    "请携带",                      # 标记词后面没有内容
    "请携带。",                    # 只有一个句号
    "关于图书馆开放时间的说明",     # 完全没有材料标记
])
def test_extract_materials_misses(text):
    assert extract_materials(text) is None


def test_materials_confidence_reflects_enumeration():
    """有枚举分隔符（顿号/逗号）更像一份清单，置信度更高。"""
    assert extract_materials_detail("需提交材料：成绩单、推荐信").confidence == 0.9
    assert extract_materials_detail("需提交材料：一份简历").confidence == 0.7
    assert extract_materials_detail("需提交材料：成绩单、推荐信").matched == "需提交材料"


def test_materials_stops_at_sentence_end():
    """清单只取到句子结束，不会把后面整段都吞进来。"""
    text = "需提交材料：报名表、成绩单。逾期不再受理，请务必按时办理。"
    assert extract_materials(text) == "报名表、成绩单"


def test_materials_too_long_is_rejected():
    """超长内容多半不是一份「清单」—— 宁可不抽，也不要污染数据。"""
    assert extract_materials("需提交材料：" + "材料" * 100) is None


# ====================================================== B29 · 入库接线 ====

def test_ingest_notice_extracts_all_three_fields(cleanup_notices):
    """B29 验收：入库后 `deadline`/`materials` 被自动填充（`importance` 一并落库）。"""
    result = ingest_notice(
        title="关于2026年秋季学期选课的通知",
        content="请于9月30日前完成选课，逾期不予受理。需提交材料：选课申请表、成绩单。",
        source="教务处",
        category="选课",
        now=BASE,
    )
    cleanup_notices.append(result.notice_id)

    assert result.notice_id > 0
    assert result.deadline is not None and result.deadline.date() == date(2026, 9, 30)
    assert result.materials == "选课申请表、成绩单"
    assert 1 <= int(result.importance) <= 5
    assert result.origin == {"deadline": "extract", "materials": "extract",
                             "importance": "extract"}
    assert result.errors == []

    # 落库核对（不只是看返回值）
    row = _row(result.notice_id)
    assert str(row["deadline"]).startswith("2026-09-30")
    assert row["materials"] == "选课申请表、成绩单"
    assert int(row["importance"]) == int(result.importance)


def test_ingest_notice_prefers_given_over_extraction(cleanup_notices):
    """调用方已知准确值时必须用它的 —— 别让抽取器去猜系统自己生成的文案。"""
    result = ingest_notice(
        title="系统生成的推送标题",
        content="正文里写着 9月30日，但真实截止时间是 10 月 1 日",
        source="pytest",
        deadline=datetime(2026, 10, 1, 9, 0),
        importance=5,
        now=BASE,
    )
    cleanup_notices.append(result.notice_id)

    assert result.origin["deadline"] == "given"
    assert result.origin["importance"] == "given"
    row = _row(result.notice_id)
    assert str(row["deadline"]).startswith("2026-10-01")
    assert int(row["importance"]) == 5


def test_ingest_notice_without_deadline_is_not_an_error(cleanup_notices):
    """抽不到就是 NULL，不是错误 —— 大部分通知本来就没有截止时间。"""
    result = ingest_notice(title="关于图书馆开放时间的说明", content="即日起执行。",
                           source="pytest", now=BASE)
    cleanup_notices.append(result.notice_id)

    assert result.notice_id > 0
    assert result.deadline is None
    assert result.origin["deadline"] == "none"
    assert result.errors == []
    assert result.importance is not None          # 重要度总能给出（有兜底分）


def test_ingest_notice_truncates_overlong_title(cleanup_notices):
    """标题超过 `VARCHAR(128)` 必须截断（否则 MySQL 直接报 1406），但要有痕迹。"""
    result = ingest_notice(title="长" * 200, content="正文", source="pytest", now=BASE)
    cleanup_notices.append(result.notice_id)

    assert result.notes and "截断" in result.notes[0]
    row = cpp_bridge.query("SELECT CHAR_LENGTH(title) AS n FROM campus_notice WHERE id = ?",
                           [result.notice_id])[0]
    assert int(row["n"]) == 128


# ======================================= B30 · 抽取失败不阻塞入库 ====

def test_extraction_failure_does_not_block_insert(monkeypatch, cleanup_notices):
    """B30 验收：抽取服务整体挂掉时，入库仍必须成功、字段降级为空值。"""
    def boom(*_args, **_kwargs):
        raise RuntimeError("抽取服务挂了")

    monkeypatch.setattr(notice_ingest, "extract_deadline", boom)
    monkeypatch.setattr(notice_ingest, "extract_materials", boom)
    monkeypatch.setattr(notice_ingest, "score_importance", boom)

    result = ingest_notice(title="抽取全挂的通知", content="9月30日前提交材料：身份证。",
                           source="pytest", now=BASE)
    cleanup_notices.append(result.notice_id)

    assert result.notice_id > 0, "抽取失败绝不该影响入库"
    assert (result.deadline, result.materials, result.importance) == (None, None, None)
    assert len(result.errors) == 3, "三次抽取失败都要留下痕迹"
    assert all("RuntimeError" in e for e in result.errors)

    # 真的写进库了（不是"返回了 id 但没落库"）
    row = _row(result.notice_id)
    assert all(_nullish(row[c]) for c in ("deadline", "materials", "importance"))


def test_ingest_skips_extended_columns_when_absent(monkeypatch, cleanup_notices):
    """未导入 `14_notice_extend.sql` 的环境：不写扩展列、行为与旧版一致。

    往不存在的列 INSERT 会直接报错，所以这里必须按探测结果动态拼列。
    """
    monkeypatch.setattr(notice_ingest, "notice_extended_columns",
                        lambda refresh=False: set())

    result = ingest_notice(title="无扩展列环境", content="9月30日前提交", source="pytest",
                           now=BASE)
    cleanup_notices.append(result.notice_id)

    assert result.notice_id > 0
    assert set(result.origin.values()) == {"skipped"}
    assert (result.deadline, result.materials, result.importance) == (None, None, None)
    assert result.errors == [], "列不存在是预期内的降级，不是错误"


# ============================================================== B30 · 回填 ====

def test_backfill_dry_run_does_not_write(cleanup_notices):
    nid = _insert_blank_notice("回填探针：选课通知", "请于9月30日前完成选课。")
    cleanup_notices.append(nid)

    out = backfill_notices(dry_run=True, now=BASE)
    assert out["supported"] is True
    assert out["dry_run"] is True
    assert out["scanned"] >= 1
    assert _nullish(_row(nid)["deadline"]), "dry-run 绝不能写库"
    # preview 只留前 20 条 → 本用例的行可能被排在后面，两种都算通过
    assert any(p["id"] == nid for p in out["preview"]) or len(out["preview"]) >= 20, \
        "dry-run 要能预览将要写入的内容"


def test_backfill_fills_legacy_rows(cleanup_notices):
    """B30 的「回填」：存量行补上抽取结果。"""
    nid = _insert_blank_notice(
        "回填探针：四六级报名", "请于9月30日前完成报名。需提交材料：学生证、报名费凭证。"
    )
    cleanup_notices.append(nid)

    out = backfill_notices(only_missing=True, now=BASE)
    assert out["updated"] >= 1
    assert out["errors"] == []

    row = _row(nid)
    assert str(row["deadline"]).startswith("2026-09-30")
    assert row["materials"] == "学生证、报名费凭证"
    # ⚠️ 别写成 `is not None`：jt_db 把 NULL 读成空串，那种断言对 '' 恒真
    # —— importance 的补写曾经静默失效，测试却照过。
    assert not _nullish(row["importance"]), "importance 也该被回填"
    assert 1 <= int(row["importance"]) <= 5


def test_backfill_does_not_overwrite_existing_values(cleanup_notices):
    """已经抽好（或运营手工改过）的值不能被回填覆盖。"""
    nid = _insert_blank_notice("回填探针：已有值", "请于9月30日前完成。")
    cleanup_notices.append(nid)
    cpp_bridge.execute(
        "UPDATE campus_notice SET deadline = ?, materials = ? WHERE id = ?",
        ["2025-01-01 00:00:00", "保持原样", nid],
    )

    backfill_notices(only_missing=True, now=BASE)

    row = _row(nid)
    assert str(row["deadline"]).startswith("2025-01-01")
    assert row["materials"] == "保持原样"


def test_backfill_without_extended_columns_is_noop(monkeypatch):
    monkeypatch.setattr(notice_ingest, "notice_extended_columns",
                        lambda refresh=False: set())
    out = backfill_notices(dry_run=True)
    assert out["supported"] is False
    assert out["scanned"] == 0 and out["updated"] == 0
