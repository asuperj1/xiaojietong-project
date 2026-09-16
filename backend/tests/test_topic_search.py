"""B29 论坛关键词搜索（`GET /topics?keyword=`）契约用例。

**两个硬前置**（不满足会直接报错，这是有意为之 —— 功能确实不可用，不该用 skip 掩盖）：
1. C++ 已重编译：`ForumDAO.search_topics` 是 C26 新增的方法，旧 `jt_db.pyd` 上没有它；
2. `db/sql/19_topic_fulltext.sql` 已执行：缺 ngram FULLTEXT 索引时 `MATCH ... AGAINST`
   会报 `ERROR 1191`，服务层已改为**失败关闭**（500 + 5001，见最后一个用例）。

**为什么用「轮询 + 正向对照」，而不是 `OPTIMIZE TABLE topic`**：
19_ 的部署注意 ③ 指出 InnoDB 全文索引**增量写入有延迟**，刚 INSERT 的行可能短暂搜不到。
本文件插入「应命中 / 只在正文命中 / 待审 / 已删除」四条**同关键词**数据后，
**轮询等待前两条出现**（出现即证明这一批已进索引），再断言后两条**搜不出来** ——
正向对照让负向断言具备判别力；否则「搜不到」可能只是索引还没刷新，属假绿。
"""

from __future__ import annotations

import time

import pytest

from app.db import cpp_bridge
from app.services import topic_search

pytestmark = pytest.mark.integration

# 冷僻关键词：2-gram 索引按双字切分，故至少 2 字；用「苜蓿」这类罕用词避免与真实数据撞车
_KW = "苜蓿检索锚点"
_NONE_KW = "绦虫蜉蝣无命中词"


def _insert_topic(
    uid: int, title: str, content: str, *, audit_status: int, is_deleted: int = 0
) -> int:
    _, tid = cpp_bridge.execute(
        "INSERT INTO topic (author_id, title, content, category, audit_status, status, is_deleted) "
        "VALUES (?, ?, ?, '综合', ?, 0, ?)",
        [uid, title, content, audit_status, is_deleted],
    )
    return int(tid)


@pytest.fixture()
def seeded(client, user_a):
    """四条同关键词的测试帖：应命中 / 只在正文命中 / 待审 / 已删除；用完即删。"""
    uid = int(user_a["user"]["id"])
    ids = [
        _insert_topic(uid, f"{_KW} 标题命中帖", "正文不含关键词", audit_status=1),
        _insert_topic(uid, "普通标题帖", f"只在正文出现的 {_KW}", audit_status=1),
        _insert_topic(uid, f"{_KW} 待审帖", "待审内容", audit_status=0),
        _insert_topic(uid, f"{_KW} 已删帖", "软删除内容", audit_status=1, is_deleted=1),
    ]
    try:
        yield {
            "uid": uid,
            "expect": [ids[0], ids[1]],  # 应命中（标题 / 正文各一条）
            "hidden": [ids[2], ids[3]],  # 待审 / 已删除：必须搜不到
        }
    finally:
        for tid in ids:
            cpp_bridge.execute("DELETE FROM `topic` WHERE `id` = ?", [tid])


def _search(client, hdr, keyword: str = _KW, **params) -> dict:
    resp = client.get("/api/v1/topics", headers=hdr, params={"keyword": keyword, **params})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _ids(data: dict) -> list[int]:
    return [int(it["id"]) for it in data["items"]]


def _search_when_indexed(client, hdr, expect: list[int], timeout: float = 8.0) -> dict:
    """轮询搜索结果，直到「应命中」的帖子都出现（即该批数据已进全文索引）。

    索引刷新延迟通常 < 1s；超时即判**失败**（而不是当成"没命中"）——
    否则后续的负向断言会变成假绿。
    """
    deadline = time.time() + timeout
    last: dict = {"items": []}
    while True:
        last = _search(client, hdr, size=50)
        if set(expect) <= set(_ids(last)):
            return last
        if time.time() > deadline:
            pytest.fail(f"{timeout}s 内全文索引仍未收录应命中的帖子 {expect}；当前命中 {_ids(last)}")
        time.sleep(0.2)


# ------------------------------------------------------------------ 命中 ----

def test_keyword_hits_title_and_content(client, hdr_a, seeded):
    """标题命中 + 正文命中都要能搜到，并带 `relevance`（相关度，倒序排序依据）。"""
    data = _search_when_indexed(client, hdr_a, seeded["expect"])

    items = {int(it["id"]): it for it in data["items"]}
    assert set(seeded["expect"]) <= set(items), "标题命中与正文命中的帖子都应出现"
    for tid in seeded["expect"]:
        assert "relevance" in items[tid], "带关键词检索时每条应附 relevance"
        assert float(items[tid]["relevance"]) > 0


def test_pending_and_deleted_topics_are_not_searchable(client, hdr_a, seeded):
    """**反向对照**：同关键词的待审帖 / 已删除帖必须搜不到（与列表口径一致）。"""
    data = _search_when_indexed(client, hdr_a, seeded["expect"])

    found = set(_ids(data))
    for tid in seeded["hidden"]:
        assert tid not in found, f"id={tid}（待审/已删除）不应出现在搜索结果里"


def test_no_match_returns_empty_without_error(client, hdr_a, seeded):
    """搜不到 → `code=0` + 空列表（**不报错**，前端好处理）。"""
    data = _search(client, hdr_a, keyword=_NONE_KW)
    assert _ids(data) == []
    assert data["total"] == 0


def test_symbol_only_keyword_degrades_to_normal_list(client, hdr_a, seeded):
    """纯符号关键词（净化后无可用词）→ 退化为普通分页，而不是返回空表。"""
    plain = client.get("/api/v1/topics", headers=hdr_a).json()["data"]
    degraded = _search(client, hdr_a, keyword="+++")
    assert _ids(degraded) == _ids(plain), "净化后无词应等价于不带关键词的列表"


def test_search_pagination(client, hdr_a, seeded):
    """分页在搜索路径上同样生效（page=2 与 page=1 不是同一批）。"""
    _search_when_indexed(client, hdr_a, seeded["expect"])

    page1 = _search(client, hdr_a, size=1, page=1)
    page2 = _search(client, hdr_a, size=1, page=2)
    assert len(page1["items"]) == 1 and len(page2["items"]) == 1
    assert _ids(page1) != _ids(page2), "第 2 页不应重复第 1 页"


def test_search_requires_auth(client):
    """未带 token → 401 / 2001。"""
    resp = client.get("/api/v1/topics", params={"keyword": _KW})
    assert resp.status_code == 401, resp.text
    assert resp.json()["code"] == 2001


# -------------------------------------------------------- 索引未就绪降级 ----

def test_fails_closed_without_fulltext_index(client, hdr_a, monkeypatch):
    """19_ 未执行 → 明确报 5001，而不是把 MySQL 的 ERROR 1191 原样抛给前端。"""
    monkeypatch.setattr(topic_search, "_capability", False)

    resp = client.get("/api/v1/topics", headers=hdr_a, params={"keyword": _KW})
    assert resp.status_code == 500, resp.text
    assert resp.json()["code"] == 5001
