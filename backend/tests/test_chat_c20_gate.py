#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`C20` 引用校验在对话链路（SSE）里的行为测试。

不依赖数据库与 Ollama：把 `cpp_bridge` / `build_system_prompt` / `model_client`
全部换成桩，只验证一个问题 —— **闸门是不是真的接上了、真的拦住了**。

    pytest backend/tests/test_chat_c20_gate.py

作者：成员3 · C20
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.core.deps import get_current_user
from app.main import app
from app.routers import chat as chat_router

SRC_TITLE = "图书馆规则"


class _Bridge:
    """记录所有写库参数，便于断言「落库的是清洗后的文本」。"""

    def __init__(self) -> None:
        self.inserted: list[list] = []
        self.seq = 0

    def query(self, sql: str, params=None) -> list:
        return []

    def execute(self, sql: str, params=None) -> list:
        self.seq += 1
        self.inserted.append(list(params or []))
        return [1, self.seq]


class _Model:
    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks
        self.calls = 0

    async def stream_chat(self, messages):
        self.calls += 1
        for c in self.chunks:
            yield c


@pytest.fixture
def env(monkeypatch):
    bridge = _Bridge()
    state: dict = {"sources": []}

    async def fake_prompt(question: str):
        return "SYS", state["sources"]

    monkeypatch.setattr(chat_router, "cpp_bridge", bridge)
    monkeypatch.setattr(chat_router, "build_system_prompt", fake_prompt)
    app.dependency_overrides[get_current_user] = lambda: {"id": 1}
    yield bridge, state
    app.dependency_overrides.clear()


def _post(question: str) -> str:
    resp = TestClient(app).post("/api/v1/chat/send", json={"content": question})
    assert resp.status_code == 200, resp.text
    return resp.text


def _event_payload(body: str, marker: str) -> dict:
    for line in body.splitlines():
        if line.startswith("data: ") and marker in line:
            return json.loads(line[len("data: "):])
    raise AssertionError(f"SSE 里没有找到含 {marker!r} 的 data 行")


def test_refuses_without_calling_model(env, monkeypatch):
    """检索分数过低 → 直接拒答，**不浪费一次生成**。"""
    bridge, state = env
    state["sources"] = [{"title": SRC_TITLE, "content": "8:00-22:00", "score": 0.05}]
    model = _Model(["这段不该出现"])
    monkeypatch.setattr(chat_router, "model_client", model)

    body = _post("计算机学院院长的办公室电话是多少？")

    assert "event: refused" in body
    assert "event: sources" not in body, "拒答时不应展示（无关的）参考资料"
    assert "event: chunk" not in body
    assert model.calls == 0, "拒答时不应调用模型"
    assert bridge.inserted[-1][1] == chat_router.REFUSE_TEXT


def test_no_score_does_not_refuse(env, monkeypatch):
    """关键词降级路径（没有 score）不拒答 —— 覆盖率闸门已被实测否掉。"""
    _, state = env
    state["sources"] = [{"title": SRC_TITLE, "content": "图书馆开放时间为每天 8:00-22:00。"}]
    model = _Model(["图书馆开放时间为每天 8:00-22:00。"])
    monkeypatch.setattr(chat_router, "model_client", model)

    body = _post("东西丢了去哪里找？")

    assert "event: refused" not in body
    assert model.calls == 1


def test_fabricated_citation_is_removed_before_save(env, monkeypatch):
    """模型编造的 `[学生手册]` 要被剔除，且**落库的是清洗后的文本**。"""
    bridge, state = env
    state["sources"] = [
        {"title": SRC_TITLE, "content": "图书馆开放时间为每天 8:00-22:00。", "score": 0.9}
    ]
    answer = "图书馆开放时间为每天 8:00-22:00。[图书馆规则][学生手册]"
    model = _Model([answer])
    monkeypatch.setattr(chat_router, "model_client", model)

    body = _post("图书馆几点关门")

    assert model.calls == 1
    assert "event: citations" in body
    payload = _event_payload(body, "fabricated")
    assert payload["fabricated"] == ["学生手册"]
    assert "学生手册" not in payload["final"]
    assert "图书馆规则" in payload["final"]
    # 关键：库里存的是修好的文本，不是模型原话
    assert "学生手册" not in bridge.inserted[-1][1]
    assert bridge.inserted[-1][1] == payload["final"]


def test_compliant_answer_is_untouched(env, monkeypatch):
    """反向对照：引用全部合规时，不能多下发事件、也不能改动一个字。"""
    bridge, state = env
    state["sources"] = [
        {"title": SRC_TITLE, "content": "图书馆开放时间为每天 8:00-22:00。", "score": 0.9}
    ]
    answer = "图书馆开放时间为每天 8:00-22:00。[图书馆规则]"
    model = _Model([answer])
    monkeypatch.setattr(chat_router, "model_client", model)

    body = _post("图书馆几点关门")

    assert "event: citations" not in body
    assert bridge.inserted[-1][1] == answer
