"""B27 显式新建会话 契约用例：`POST /chat/conversations` 与 `PATCH` 重命名。

对应任务单 §3.2 **B27**：点「新建」即返回 `conversation_id`；**空会话也能正常对话**。
（前端 F14 的 AI 侧边栏依赖它：不能只靠「发消息时顺带建会话」。）

设计说明：
- 全程复用 conftest 的 `hdr_a`/`hdr_b` 会话级夹具，**不额外登录** ——
  `/auth/wechat-login` 有 SEC-10 限流 10 次/分钟，用例逐个登录会被限流打成一片红。
- `/chat/send` 会调 RAG 与模型：本文件把 `build_system_prompt` 与 `stream_chat`
  换成确定性替身（monkeypatch），既能验证「空会话可对话」的真实代码路径，
  又不依赖 Ollama 是否在线、不受模型耗时影响。
- 用例自建会话并**在结束时用真实接口删掉**，不留下垃圾会话。
"""

from __future__ import annotations

import json

import pytest

from app.routers import chat

pytestmark = pytest.mark.integration

_PREFIX = "pytest-b27-"


@pytest.fixture()
def convs(client, hdr_a):
    """会话工厂：记录本用例创建的会话 id，结束时逐个删除（走真实 DELETE 接口）。"""
    created: list[int] = []

    def _make(title: str | None = None) -> int:
        body = {} if title is None else {"title": title}
        resp = client.post("/api/v1/chat/conversations", headers=hdr_a, json=body)
        assert resp.status_code == 200, resp.text
        cid = int(resp.json()["data"]["conversation_id"])
        assert cid > 0, "新建会话必须返回有效的 conversation_id"
        created.append(cid)
        return cid

    try:
        yield _make
    finally:
        for cid in created:
            client.delete(f"/api/v1/chat/conversations/{cid}", headers=hdr_a)


def _titles_of(client, hdr) -> dict[int, str]:
    data = client.get("/api/v1/chat/conversations", headers=hdr).json()["data"]
    return {int(it["id"]): it["title"] for it in data["items"]}


# ------------------------------------------------------------------ 新建 ----

def test_create_without_body_returns_id_and_default_title(client, hdr_a, convs):
    """不带请求体也能建（前端「点新建」常常不打 body），标题取默认值。"""
    resp = client.post("/api/v1/chat/conversations", headers=hdr_a)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    cid = int(data["conversation_id"])
    assert cid > 0
    assert data["title"] == "新对话"

    assert _titles_of(client, hdr_a).get(cid) == "新对话", "新建会话应出现在列表里"
    client.delete(f"/api/v1/chat/conversations/{cid}", headers=hdr_a)


def test_create_with_custom_title(client, hdr_a, convs):
    """自定义标题原样保留（前端可先建会话再填标题）。"""
    cid = convs(f"{_PREFIX}自定义标题")
    assert _titles_of(client, hdr_a)[cid] == f"{_PREFIX}自定义标题"


def test_create_requires_auth(client):
    """未带 token → 401 / 2001。"""
    resp = client.post("/api/v1/chat/conversations", json={})
    assert resp.status_code == 401, resp.text
    assert resp.json()["code"] == 2001


def test_created_conversation_is_isolated_from_other_users(client, hdr_a, hdr_b, convs):
    """新建的会话只属于自己：别人的会话列表里看不到（SEC-03 口径）。"""
    cid = convs(f"{_PREFIX}归属")
    assert cid not in _titles_of(client, hdr_b)


# ------------------------------------------------- 空会话可正常对话（核心）----

def test_empty_conversation_can_chat(client, hdr_a, convs, monkeypatch):
    """**B27 的核心验收**：新建后尚未有任何消息的会话，直接对话可用且落在同一会话。"""
    cid = convs(f"{_PREFIX}空会话对话")

    # 新建时确实没有消息
    first = client.get(f"/api/v1/chat/conversations/{cid}/messages", headers=hdr_a).json()
    assert first["data"]["items"] == []

    # 模型与 RAG 换成确定性替身，避免用例依赖 Ollama 在线与耗时
    async def _fake_prompt(text: str):
        return "你是校捷通助手", [{"title": "替身来源", "score": 1.0}]

    async def _fake_stream(messages):
        yield "这是替身回答"

    monkeypatch.setattr(chat, "build_system_prompt", _fake_prompt)
    monkeypatch.setattr(chat.model_client, "stream_chat", _fake_stream)

    resp = client.post(
        "/api/v1/chat/send",
        headers=hdr_a,
        json={"conversation_id": cid, "content": "你好"},
    )
    assert resp.status_code == 200, resp.text
    assert "event: done" in resp.text, resp.text
    done_line = [ln for ln in resp.text.splitlines() if ln.startswith("data: ") and "conversation_id" in ln][-1]
    assert json.loads(done_line[6:])["conversation_id"] == cid, "回答必须落在新建的那个会话里"

    # 消息确实写进了该会话（一问一答）
    items = client.get(f"/api/v1/chat/conversations/{cid}/messages", headers=hdr_a).json()["data"]["items"]
    assert [it["role"] for it in items] == ["user", "assistant"]
    assert items[1]["content"] == "这是替身回答"


# ------------------------------------------------------------------ 重命名 ----

def test_rename_conversation(client, hdr_a, convs):
    """重命名生效，并体现在会话列表里。"""
    cid = convs(f"{_PREFIX}旧标题")

    resp = client.patch(
        f"/api/v1/chat/conversations/{cid}", headers=hdr_a, json={"title": f"{_PREFIX}新标题"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["title"] == f"{_PREFIX}新标题"
    assert _titles_of(client, hdr_a)[cid] == f"{_PREFIX}新标题"


def test_rename_rejects_blank_and_too_long_title(client, hdr_a, convs):
    """空标题拒绝（不静默复位）；超过列宽 100 也拒绝，而不是让 MySQL 截断/报错。"""
    cid = convs(f"{_PREFIX}边界")

    blank = client.patch(f"/api/v1/chat/conversations/{cid}", headers=hdr_a, json={"title": "   "})
    assert blank.status_code == 400, blank.text
    assert blank.json()["code"] == 1001

    long_title = client.patch(
        f"/api/v1/chat/conversations/{cid}", headers=hdr_a, json={"title": "长" * 101}
    )
    assert long_title.status_code == 400, long_title.text
    assert long_title.json()["code"] == 1001
    assert _titles_of(client, hdr_a)[cid] == f"{_PREFIX}边界", "被拒的重命名不得改库"


def test_rename_requires_ownership(client, hdr_a, hdr_b, convs):
    """他人会话重命名 → 1001（与「不存在」同码，不泄漏他人会话是否存在）。"""
    cid = convs(f"{_PREFIX}越权")

    resp = client.patch(
        f"/api/v1/chat/conversations/{cid}", headers=hdr_b, json={"title": "被改掉了"}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == 1001
    assert _titles_of(client, hdr_a)[cid] == f"{_PREFIX}越权", "越权不得改库"


def test_rename_deleted_conversation_rejected(client, hdr_a, convs):
    """已删除的会话不能再改名。"""
    cid = convs(f"{_PREFIX}已删除")
    assert client.delete(f"/api/v1/chat/conversations/{cid}", headers=hdr_a).status_code == 200

    resp = client.patch(f"/api/v1/chat/conversations/{cid}", headers=hdr_a, json={"title": "x"})
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == 1001
