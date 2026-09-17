"""B29 论坛关键词搜索：`GET /topics?keyword=` 的服务层。

分工（**别把检索 SQL 搬到 Python 来**）：
- 检索本体在 C++ `ForumDAO.search_topics()`（C26，成员3）：净化用户输入为 boolean 检索式
  （去运算符、按空白拆词、每词前缀 `+` 得 AND 语义）、`MATCH(title, content) AGAINST(... IN
  BOOLEAN MODE)` 命中、按 `relevance` 倒序；净化后无可用词则**自动退化为普通分页**。
  本模块只做「部署前置探测 + 调用」，两份 SQL 只会互相漂移。
- 索引出处：`db/sql/19_topic_fulltext.sql`（FULLTEXT + **ngram parser**，与
  `services/zh_tokenizer.py` 的 2-gram 口径一致；默认 parser 按空格切词，中文整句必然 0 命中）。

**索引未就绪时失败关闭**：`topic` 上没有 FULLTEXT 索引却执行 `MATCH ... AGAINST`，
MySQL 会报 `ERROR 1191 Can't find FULLTEXT index matching the column list`，
前端只会看到一句 500。这里改为显式探测 + 明确报错 + 可检索的运维日志。

契约：`docs/api.md` §8。
"""

from __future__ import annotations

import logging
from typing import Optional

from app.core.response import err_server
from app.db import cpp_bridge

log = logging.getLogger("uvicorn")

#: 索引探测结果缓存（None = 尚未探测）
_capability: Optional[bool] = None


def reset_capability_cache() -> None:
    """清空探测缓存（执行 `19_topic_fulltext.sql` 后或用例需要时调用）。"""
    global _capability
    _capability = None


def fulltext_ready() -> bool:
    """`topic` 上是否已有 FULLTEXT 索引（即 19_ 是否已执行）。

    DDL 属部署期一次性变更，故缓存结果；探测本身失败按「未就绪」处理（失败关闭）。
    """
    global _capability
    if _capability is None:
        try:
            rows = cpp_bridge.query(
                "SELECT COUNT(*) AS n FROM information_schema.STATISTICS "
                "WHERE table_schema = DATABASE() AND table_name = 'topic' "
                "AND index_type = 'FULLTEXT'",
                [],
            )
            _capability = bool(rows and int(rows[0]["n"] or 0) > 0)
        except Exception as exc:  # noqa: BLE001 - 探测失败不缓存，下次重试
            log.warning("FULLTEXT 索引探测失败：%s", exc)
            return False
    return bool(_capability)


def search(keyword: str, page: int, size: int, category: str = "") -> list[dict]:
    """关键词检索，返回该页条目（每条额外带 `relevance`，按相关度倒序）。

    可见性由 DAO 统一保证（`status=0 AND is_deleted=0 AND audit_status=1`）：
    待审 / 被拒 / 已删除的帖子既不出现在列表里，也**搜不出来**。
    """
    if not fulltext_ready():
        log.warning(
            "拒绝关键词检索：topic 上没有 FULLTEXT 索引，"
            "请先执行 db/sql/19_topic_fulltext.sql"
        )
        raise err_server("关键词搜索暂不可用，请稍后再试")
    return cpp_bridge.forum_dao().search_topics(page, size, keyword, category, True)
