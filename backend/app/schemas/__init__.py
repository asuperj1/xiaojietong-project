"""Pydantic 契约包（B20 起）。

当前内容：
- :mod:`app.schemas.notice` —— 通知领域契约（`campus_notice` 响应与 B19 扩展字段）

约定：契约集中在此包，**只增不改**；router/service 里的散落常量应逐步收敛到这里
（例如 `services/notice_scheduler._EXTENDED_NAMES` 已改为引用本包的
`EXTENDED_FIELD_NAMES`，保证 SQL / DAO / 契约三方列名一致）。
"""
