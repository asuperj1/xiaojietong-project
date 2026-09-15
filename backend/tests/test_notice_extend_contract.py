"""B20 字段契约 —— `campus_notice` 的 `deadline` / `materials` / `importance`。

**为什么契约落在测试里，而不是一个模型模块**：本仓**没有响应模型层**
（`backend/app/schemas/` 目录不存在；30 个 `BaseModel` 全部内联在 `routers/*.py`，
且只有请求模型 `*In`，响应统一走 `ok(dict)`）。因此再造一个没人调用的
`schemas/notice_extend.py` 只会是死代码 —— 契约的正确落地形态是**可执行断言**。

覆盖三类不变量：

1. **DDL ↔ Python 常量必须一致**（`db/sql/14_notice_extend.sql` 与
   `notice._EXTENDED_NAMES` / `notice_scheduler.EXTENDED_COLUMN_NAMES`）
   —— 任何一方单方面改名，本文件立刻变红；
2. **未导入 DDL 时向后兼容**：不 SELECT 这 3 列、不发起补查 SQL、接口不出现这 3 个字段，
   **但旧字段一个都不能少**；
3. **导入 DDL 后的正向路径**：列出现时必须 SELECT 到、并合并进通知行（用假探测 + 假查询验证）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services import notice
from app.services.notice_scheduler import EXTENDED_COLUMN_NAMES

# backend/tests/test_notice_extend_contract.py -> <repo>/db/sql/14_notice_extend.sql
_DDL_PATH = Path(__file__).resolve().parents[2] / "db" / "sql" / "14_notice_extend.sql"

# 旧接口本来就返回的字段（向后兼容断言用；不含 target_grade —— DAO 路径拿不到它）
_BASE_KEYS = ("id", "title", "content", "source", "category", "publish_time")


def _ddl_added_columns() -> set[str]:
    """从 B19 的 DDL 里抽出它新增的列名（形如 ``ADD COLUMN `x` ``）。

    必须**要求反引号**：文件头注释里有一句 ``ADD COLUMN IF NOT EXISTS``（说明幂等写法），
    不限定反引号会把 ``IF`` 当成列名捕获进来。
    """
    sql = _DDL_PATH.read_text(encoding="utf-8")
    return set(re.findall(r"ADD COLUMN\s+`(\w+)`", sql))


# ---------------------------------------------------------------- 1) 契约一致性

def test_ddl_exists():
    """B19 的 DDL 必须存在 —— 它是契约的唯一权威来源。"""
    assert _DDL_PATH.is_file(), f"缺少 B19 的 DDL：{_DDL_PATH}"


def test_ddl_columns_match_python_contract():
    """DDL 新增的列，必须与 Python 侧两处常量**完全一致**。

    这是本文件最核心的一条：把「表结构」和「代码里写死的列名」绑在一起，
    防止出现「DDL 叫 `dead_line`、代码里写 `deadline`」这类静默失效。
    """
    assert _ddl_added_columns() == set(notice._EXTENDED_NAMES), (
        "DDL 与 notice._EXTENDED_NAMES 不一致；改名必须两边同时改"
    )
    assert set(EXTENDED_COLUMN_NAMES) == set(notice._EXTENDED_NAMES), (
        "notice_scheduler.EXTENDED_COLUMN_NAMES 与 notice._EXTENDED_NAMES 不一致"
    )


def test_ddl_declares_deadline_index():
    """`idx_deadline` 必须存在 —— B18/B29/B30 都按 `deadline` 查询，缺索引会全表扫。"""
    sql = _DDL_PATH.read_text(encoding="utf-8")
    assert re.search(r"ADD KEY\s+`idx_deadline`", sql), "DDL 未创建 idx_deadline"


def test_extended_columns_are_a_known_set():
    """契约是闭集：只允许这 3 个字段，防止有人往里塞第 4 个却不同步 DDL。"""
    assert set(notice._EXTENDED_NAMES) == {"deadline", "materials", "importance"}


# ---------------------------------------------------- 2) 未导入 DDL：向后兼容

def test_notice_columns_omits_extended_before_ddl(monkeypatch):
    """列不存在时**不得**出现在 SELECT 里（否则 SQL 直接报错）。"""
    monkeypatch.setattr(notice, "notice_extended_columns", lambda *a, **k: set())
    cols = notice.notice_columns()
    assert cols == ", ".join(notice._BASE_COLUMNS)
    for name in notice._EXTENDED_NAMES:
        assert name not in cols


def test_notice_columns_alias_prefix_applies_to_extended(monkeypatch):
    """带别名时新列也要加前缀（`notice-feed` / `unread` 是 JOIN 查询）。"""
    monkeypatch.setattr(notice, "notice_extended_columns", lambda *a, **k: set())
    cols = notice.notice_columns("n")
    assert "n.deadline" not in cols          # 未导入 ⇒ 不该出现
    assert "n.id" in cols and "n.publish_time" in cols


def test_attach_extended_fields_is_noop_before_ddl(monkeypatch):
    """未导入 DDL ⇒ **一条补查 SQL 都不能发**，且原样返回（旧行为）。"""
    monkeypatch.setattr(notice, "notice_extended_columns", lambda *a, **k: set())

    def _boom(*_a, **_k):
        raise AssertionError("未导入 DDL 时不应发起补查 SQL")

    monkeypatch.setattr(notice.cpp_bridge, "query", _boom)
    rows = [{"id": "40", "title": "t"}]
    assert notice.attach_extended_fields(rows) == rows


def test_life_notices_has_no_extended_keys_before_ddl(client, hdr_a):
    """集成：当前库未导入 DDL ⇒ `/life/notices` 不得出现这 3 个字段。

    **反向对照**：同时断言旧字段在 —— 否则「没有新字段」可能只是因为返回了空对象。
    """
    resp = client.get("/api/v1/life/notices?page=1&size=5", headers=hdr_a)
    assert resp.status_code == 200, resp.text
    items = resp.json()["data"]["items"]
    if not items:
        pytest.skip("campus_notice 当前无可见行，无法做向后兼容集成断言")
    for it in items:
        for name in notice._EXTENDED_NAMES:
            assert name not in it, f"未导入 DDL 却返回了 {name} —— 向后兼容被破坏"
        for base in _BASE_KEYS:
            assert base in it, f"旧字段 {base} 丢失（反向对照失败：不能是返回空对象）"


# -------------------------------------------------- 3) 导入 DDL 后：正向路径

def test_notice_columns_includes_extended_when_ddl_applied(monkeypatch):
    """列存在时必须 SELECT 到（与上面的 noop 用例互为反向对照）。"""
    monkeypatch.setattr(
        notice, "notice_extended_columns", lambda *a, **k: set(notice._EXTENDED_NAMES)
    )
    cols = notice.notice_columns("n")
    for name in notice._EXTENDED_NAMES:
        assert f"n.{name}" in cols
    assert "n.id" in cols                     # 旧字段仍必须在


def test_attach_extended_fields_merges_when_ddl_applied(monkeypatch):
    """列存在时按 id 合并进通知行；**旧字段必须保留**。"""
    monkeypatch.setattr(
        notice, "notice_extended_columns", lambda *a, **k: set(notice._EXTENDED_NAMES)
    )
    fake = [
        {
            "id": "40",
            "deadline": "2026-09-20 18:00:00",
            "materials": "报名表\n成绩单",
            "importance": "4",
        }
    ]
    monkeypatch.setattr(notice.cpp_bridge, "query", lambda sql, params=None: fake)
    rows = [{"id": "40", "title": "t", "content": "c"}]
    out = notice.attach_extended_fields(rows)
    assert out[0]["deadline"] == "2026-09-20 18:00:00"
    assert out[0]["materials"] == "报名表\n成绩单"
    assert out[0]["importance"] == "4"
    assert out[0]["title"] == "t" and out[0]["content"] == "c"


def test_attach_extended_fields_fills_none_for_rows_missing_in_extra(monkeypatch):
    """补查没覆盖到的行：3 个字段填 `None`（**不能**缺 key，否则前端字段不一致）。"""
    monkeypatch.setattr(
        notice, "notice_extended_columns", lambda *a, **k: set(notice._EXTENDED_NAMES)
    )
    monkeypatch.setattr(
        notice.cpp_bridge,
        "query",
        lambda sql, params=None: [{"id": "40", "deadline": None, "materials": None, "importance": None}],
    )
    rows = [{"id": "40", "title": "t"}, {"id": "41", "title": "t2"}]
    out = notice.attach_extended_fields(rows)
    for row in out:
        for name in notice._EXTENDED_NAMES:
            assert name in row, f"{name} 缺失 —— 同一页内字段应一致存在"
