"""论坛审核闭环（B6 验收用例）。

覆盖：
- 干净文本发帖 → 自动过审（audit_status=1）→ **换账号列表立即可见**；
- 敏感词发帖 → 错误码 3003 拒绝（依赖 db/sql/11_audit.sql 词库）。
"""
from __future__ import annotations

import time

import pytest

from app.db import cpp_bridge

pytestmark = pytest.mark.integration


def test_clean_topic_visible_to_other_user(client, hdr_a, hdr_b):
    title = f"B12集成用例-求自习地点-{int(time.time())}"
    created = client.post(
        "/api/v1/topics",
        headers=hdr_a,
        json={"title": title, "content": "想找安静的晚自习教室，求推荐。", "category": "学习"},
    ).json()
    assert created["code"] == 0, created
    assert created["data"]["audit_status"] == 1, f"干净文本应自动过审：{created}"

    listing = client.get(
        "/api/v1/topics", params={"page": 1, "size": 50}, headers=hdr_b
    ).json()
    titles = [item["title"] for item in listing["data"]["items"]]
    assert title in titles, "发帖后换账号应立即可见（B6 核心闭环）"


def test_sensitive_word_rejected(client, hdr_a):
    if not cpp_bridge.query("SELECT word FROM audit_word WHERE word = '代考'", []):
        pytest.skip("审核词库未导入（db/sql/11_audit.sql），跳过敏感词用例")
    resp = client.post(
        "/api/v1/topics",
        headers=hdr_a,
        json={"title": "代考包过", "content": "专业代考，勿扰", "category": "其他"},
    ).json()
    assert resp["code"] == 3003, resp
