"""通知领域 Pydantic 契约（B20）。

**为什么要单独一个包**：B20 的字段契约此前只散落在
``services/notice_scheduler._EXTENDED_NAMES``（service 内部常量）与文档里，
没有任何**可被类型系统/测试校验**的落点。本包把它集中成唯一的声明源：

- :data:`EXTENDED_FIELD_NAMES` —— B19 ``14_notice_extend.sql`` 的 3 个列名
  （**必须与 SQL 严格一致**，`db/cpp_driver/test/test_all_dao.py` 与
  `backend/tests/test_notice_extend_contract.py` 都会对着它断言）；
- :class:`NoticeExtendFields` —— 3 个**可空**扩展字段（未导入 DDL / 未抽取时为 ``None``）；
- :class:`NoticeOut` —— 通知响应契约（旧字段 + 可选扩展字段，向后兼容）；
- :func:`coerce_extended_fields` —— 把 jt_db 返回的原始行**规范化**为契约形状
  （缺列补 ``None``、``importance`` 字符串转 ``int``），供路由层直接调用。

约定：**契约只增不改**（旧字段一律保留），删除/改名属于破坏性变更，须走 PR 评审。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

#: B19 `db/sql/14_notice_extend.sql` 新增的 3 列（顺序与 SQL 中 AFTER 链一致）
EXTENDED_FIELD_NAMES: tuple[str, str, str] = ("deadline", "materials", "importance")


class NoticeExtendFields(BaseModel):
    """B19 扩展字段（**全部可空**：未导入 DDL、或通知尚未被抽取/打分时为 None）。"""

    model_config = ConfigDict(extra="ignore")

    deadline: Optional[datetime] = Field(
        default=None, description="截止时间（抽取结果：报名/申请/提交截止）"
    )
    materials: Optional[str] = Field(
        default=None, description="办理材料清单（抽取结果，多条换行分隔）"
    )
    importance: Optional[int] = Field(
        default=None, ge=1, le=5, description="重要度 1~5（抽取/打分结果），None=未打分"
    )


class NoticeOut(BaseModel):
    """通知响应契约（`/life/notices`、`/life/notice-feed`、`/life/notices/unread` 共用部分）。

    ``score`` / ``reason`` / ``matched_tags`` / ``is_read`` 等投递维度字段由各接口
    自行附加（``extra="allow"`` 保证不丢字段）。
    """

    model_config = ConfigDict(extra="allow")

    id: int
    title: str = ""
    content: str = ""
    source: str = ""
    category: str = ""
    target_grade: str = ""
    publish_time: Optional[datetime] = None
    deadline: Optional[datetime] = None
    materials: Optional[str] = None
    importance: Optional[int] = Field(default=None, ge=1, le=5)


def coerce_extended_fields(row: dict[str, Any]) -> dict[str, Any]:
    """把一行通知**就地**规范化为契约形状，返回同一 dict（便于链式调用）。

    - 扩展列不存在（未导入 DDL）→ 补 ``None``（前端字段恒定，不会一会有一会没有）；
    - ``importance`` 为字符串（jt_db 可能把 TINYINT 返回为 ``"4"``）→ 转 ``int``；
    - 非法值（如 ``"abc"`` / 越界）→ 收敛为 ``None`` 而不是抛错（响应不该因脏数据 500）。
    """
    payload = {name: row.get(name) for name in EXTENDED_FIELD_NAMES}
    try:
        parsed = NoticeExtendFields.model_validate(payload)
    except Exception:  # noqa: BLE001 - 脏数据降级为 None，绝不影响接口可用性
        parsed = NoticeExtendFields()
    for name in EXTENDED_FIELD_NAMES:
        row[name] = getattr(parsed, name)
    return row


__all__ = [
    "EXTENDED_FIELD_NAMES",
    "NoticeExtendFields",
    "NoticeOut",
    "coerce_extended_fields",
]
