"""聊天会话越权隔离（B12 用例①：审计 SEC-03 / SEC-04）。

验证：他人会话不可读、不可写入；越权返回 1001。
"""
from __future__ import annotations

import pytest

from app.db import cpp_bridge

pytestmark = pytest.mark.integration


def test_conversation_isolation(client, hdr_a, hdr_b, user_a):
    uid = int(user_a["user"]["id"])
    _, conv_id = cpp_bridge.execute(
        "INSERT INTO ai_conversation (user_id, title) VALUES (?, ?)",
        [uid, "B12越权用例会话"],
    )

    # 本人可读（对照组）
    own = client.get(f"/api/v1/chat/conversations/{conv_id}/messages", headers=hdr_a).json()
    assert own["code"] == 0, own

    # 他人读取 → 拒绝（SEC-03）
    other = client.get(f"/api/v1/chat/conversations/{conv_id}/messages", headers=hdr_b).json()
    assert other["code"] == 1001, f"越权读会话应被拒：{other}"

    # 他人向该会话发消息 → 归属校验先于模型调用（SEC-04）
    send = client.post(
        "/api/v1/chat/send",
        headers=hdr_b,
        json={"conversation_id": conv_id, "content": "hi"},
    ).json()
    assert send["code"] == 1001, f"越权发消息应被拒：{send}"
