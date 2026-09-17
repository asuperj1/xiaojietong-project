# -*- coding: utf-8 -*-
"""C36 结构化抽取服务化 —— 统一入口 / 与对话模型隔离 / 不静默降级。

**全离线**：不需要 DB，也不需要真的 Ollama —— 桩服务就是按 Ollama `/api/chat` 的
响应形态写的，真起 uvicorn 走真 HTTP。

三条主线：

1. **统一调用**：后端不再各自手写 `httpx + payload`（改造前有 3 处），抽取收成
   `extract_json()` 一个入口；`secondhand_ai._model_describe` 已改接到它。
2. **与对话模型隔离**：`XJT_EXTRACT_*` 独立配置，可指向另一个模型/另一台机器。
   反向对照见 `test_isolation_*`：对话模型名换成另一个时，抽取请求里**必须**
   出现抽取模型名 —— 若实现偷懒复用了 `ollama_model`，用例会红。
3. **失败语义**：只可能拿到 `ExtractResult` 或抛 `ExtractUnavailable`/`ExtractFailure`，
   **绝不**返回 `{}` 冒充抽取成功（对话可以回占位文案，抽取不行）。
"""
from __future__ import annotations

import json

import pytest
from fastapi import Body, FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.net import is_loopback, proxy_bypass_kwargs
from app.services import extract_model as em
from app.services.extract_model import (
    ExtractFailure,
    ExtractModelClient,
    ExtractUnavailable,
)
from live_server import free_port, live_server

INSTRUCTION = "从校园通知里抽取时间与地点，只输出 JSON。"


# ============================================================ 桩服务 ====


class StubOllama:
    """按 Ollama /api/chat 的响应形态返回；记录收到的请求体。"""

    def __init__(self, content: str = '{"title": "九成新教材"}', *, status: int = 200,
                 model_names: tuple[str, ...] = ("xjt-3b",),
                 content_missing: bool = False) -> None:
        self.content = content
        self.status = status
        self.model_names = model_names
        self.content_missing = content_missing
        self.calls: list[dict] = []
        self.tags_calls = 0
        app = FastAPI()

        @app.post("/api/chat")
        async def chat(payload: dict = Body(...)):     # noqa: B008 - FastAPI 惯例
            self.calls.append(payload)
            if self.status != 200:
                return JSONResponse(status_code=self.status, content={"error": "boom"})
            if self.content_missing:
                return {"done": True}
            return {"message": {"content": self.content}, "done": True}

        @app.get("/api/tags")
        async def tags():
            self.tags_calls += 1
            return {"models": [{"name": n} for n in self.model_names]}

        self.app = app

    def last(self) -> dict:
        assert self.calls, "桩服务没收到任何请求"
        return self.calls[-1]


class StubExtractService:
    """自建抽取服务（`XJT_EXTRACT_BACKEND=http` 这条路）。"""

    def __init__(self, payload=None, *, status: int = 200, raw_text: str = "") -> None:
        self.payload = payload if payload is not None else {"data": {"title": "抽到了"}}
        self.status = status
        self.raw_text = raw_text
        self.calls: list[dict] = []
        app = FastAPI()

        @app.post("/extract")
        async def extract(request: Request, body: dict = Body(...)):  # noqa: B008
            self.calls.append({"body": body, "auth": request.headers.get("authorization", "")})
            if self.raw_text:
                return JSONResponse(status_code=self.status, content=self.raw_text)
            return JSONResponse(status_code=self.status, content=self.payload)

        self.app = app


@pytest.fixture(autouse=True)
def _reset():
    """每个用例前后还原单例，避免互相污染。"""
    em.reset_client()
    yield
    em.reset_client()


# ======================================================== 可用性判断 ====


def test_availability_ollama_default_falls_back_to_chat_config():
    """**默认零行为变化**：不配 XJT_EXTRACT_* 时沿用 ollama_* 。"""
    c = ExtractModelClient()
    ok, why = c.availability()
    assert ok is True and why == ""
    assert c.base_url == settings.ollama_base_url.rstrip("/")
    assert c.model == settings.ollama_model
    assert c.backend == "ollama"


def test_availability_isolated_config_wins(monkeypatch):
    monkeypatch.setattr(settings, "extract_base_url", "http://10.0.0.9:11434")
    monkeypatch.setattr(settings, "extract_model", "xjt-extract-1.5b")
    c = ExtractModelClient()
    assert c.base_url == "http://10.0.0.9:11434"      # 不复用 ollama_base_url
    assert c.model == "xjt-extract-1.5b"              # 不复用 ollama_model
    assert c.model != settings.ollama_model


@pytest.mark.parametrize("cfg,expect", [
    (dict(backend="none"), "已明确关闭"),
    (dict(backend="http", http_url=""), "XJT_EXTRACT_HTTP_URL"),
    (dict(backend="http", http_url="ftp://x/y"), "http"),
    (dict(backend="ollama", base_url="not-a-url"), "http"),
    (dict(backend="weird"), "未知"),
])
def test_availability_reports_explicit_reason(cfg, expect):
    ok, why = ExtractModelClient(**cfg).availability()
    assert ok is False and expect in why


def test_none_backend_never_calls_out():
    """`none` 必须在**发请求之前**就拒绝，而不是打个空请求再报错。"""
    with pytest.raises(ExtractUnavailable) as exc:
        import asyncio
        asyncio.run(ExtractModelClient(backend="none").extract_json("x", instruction=INSTRUCTION))
    assert "关闭" in str(exc.value)


# ================================================ Ollama 路（真回环）====


def _client_for(stub: StubOllama, base: str, **kw) -> ExtractModelClient:
    return ExtractModelClient(base_url=base, model="xjt-extract-1.5b", timeout=5.0, **kw)


@pytest.mark.asyncio
async def test_ollama_success_and_payload_shape():
    stub = StubOllama('{"title": "九成新教材", "price": 20}')
    with live_server(stub.app) as base:
        result = await _client_for(stub, base).extract_json("九成新教材转让", instruction=INSTRUCTION)
    assert result.data == {"title": "九成新教材", "price": 20}
    assert result.backend == "ollama" and result.truncated is False

    sent = stub.last()
    assert sent["format"] == "json"          # 要 JSON 就别让模型自由发挥
    assert sent["stream"] is False
    assert sent["options"]["temperature"] == pytest.approx(0.2)
    assert sent["messages"][0]["content"] == INSTRUCTION
    assert sent["messages"][1]["content"] == "九成新教材转让"


@pytest.mark.asyncio
async def test_isolation_extract_model_is_used_not_chat_model(monkeypatch):
    """**隔离的核心证据**：抽取请求里的 model 必须是抽取模型。

    反向对照：把对话模型也改成另一个名字，断言请求里**不出现**它 ——
    若实现图省事复用了 `settings.ollama_model`，这里必红。
    """
    monkeypatch.setattr(settings, "ollama_model", "xjt-3b-chat")
    monkeypatch.setattr(settings, "extract_model", "xjt-extract-1.5b")
    stub = StubOllama('{"ok": true}')
    with live_server(stub.app) as base:
        monkeypatch.setattr(settings, "extract_base_url", base)
        await em.extract_json("任意正文", instruction=INSTRUCTION)
    assert stub.last()["model"] == "xjt-extract-1.5b"
    assert stub.last()["model"] != settings.ollama_model


@pytest.mark.asyncio
async def test_isolation_can_point_at_a_different_host(monkeypatch):
    """隔离也意味着能指向**另一台** Ollama：请求必须打到抽取地址，而不是对话地址。"""
    monkeypatch.setattr(settings, "ollama_base_url", f"http://127.0.0.1:{free_port()}")
    stub = StubOllama('{"ok": true}')
    with live_server(stub.app) as base:
        monkeypatch.setattr(settings, "extract_base_url", base)
        await em.extract_json("任意正文", instruction=INSTRUCTION)
    assert len(stub.calls) == 1


@pytest.mark.asyncio
async def test_fenced_json_is_parsed():
    """真实模型爱加 ```json 围栏 —— 必须能剥掉，否则线上会大面积失败。"""
    stub = StubOllama('```json\n{"title": "围栏"}\n```')
    with live_server(stub.app) as base:
        result = await _client_for(stub, base).extract_json("x", instruction=INSTRUCTION)
    assert result.data == {"title": "围栏"}


@pytest.mark.asyncio
@pytest.mark.parametrize("content,why", [
    ("这不是 JSON", "合法 JSON"),
    ('[1, 2, 3]', "顶层不是对象"),
])
async def test_bad_model_output_is_failure_not_placeholder(content, why):
    """**不许静默降级**：模型没吐出合法 JSON → 抛 `ExtractFailure`，绝不返回 `{}`。"""
    stub = StubOllama(content)
    with live_server(stub.app) as base:
        with pytest.raises(ExtractFailure) as exc:
            await _client_for(stub, base).extract_json("x", instruction=INSTRUCTION)
    assert why in str(exc.value)


@pytest.mark.asyncio
async def test_missing_message_content_is_failure():
    stub = StubOllama(content_missing=True)
    with live_server(stub.app) as base:
        with pytest.raises(ExtractFailure) as exc:
            await _client_for(stub, base).extract_json("x", instruction=INSTRUCTION)
    assert "message.content" in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 404, 500, 503])
async def test_upstream_error_is_failure_not_unavailable(status):
    """上游 4xx/5xx = 本次抽取失败（`ExtractFailure`），不是"服务不可用" —— 两者要分得清。"""
    stub = StubOllama(status=status)
    with live_server(stub.app) as base:
        with pytest.raises(ExtractFailure):
            await _client_for(stub, base).extract_json("x", instruction=INSTRUCTION)


@pytest.mark.asyncio
async def test_connect_error_is_unavailable():
    """服务没起 → "不可用"，与"这次抽失败了"区分开（便于运维定位）。"""
    c = ExtractModelClient(base_url=f"http://127.0.0.1:{free_port()}", model="m", timeout=1.0)
    with pytest.raises(ExtractUnavailable):
        await c.extract_json("x", instruction=INSTRUCTION)


@pytest.mark.asyncio
async def test_truncation_is_reported_not_silent():
    """超长输入被截断时必须**标出来**（`truncated`），不能悄悄丢内容。"""
    stub = StubOllama('{"ok": true}')
    with live_server(stub.app) as base:
        c = _client_for(stub, base, max_chars=10)
        result = await c.extract_json("一二三四五六七八九十十一十二十三", instruction=INSTRUCTION)
    assert result.truncated is True
    assert stub.last()["messages"][1]["content"] == "一二三四五六七八九十"


@pytest.mark.asyncio
async def test_missing_instruction_is_rejected():
    """抽取必须有明确指令 —— 指令决定抽什么字段，缺了就没有意义。"""
    c = ExtractModelClient(base_url="http://127.0.0.1:9", model="m")
    with pytest.raises(ExtractFailure) as exc:
        await c.extract_json("x")
    assert "instruction" in str(exc.value)


@pytest.mark.asyncio
async def test_probe_reports_model_not_registered():
    """`probe()` 是**会发请求**的预检：模型没注册要明确说出来（并给出怎么办）。"""
    stub = StubOllama(model_names=("other-model",))
    with live_server(stub.app) as base:
        c = _client_for(stub, base)
        ok, why = await c.probe()
    assert ok is False and "register_model" in why
    assert stub.tags_calls == 1


@pytest.mark.asyncio
async def test_probe_ok_when_model_present():
    stub = StubOllama(model_names=("xjt-extract-1.5b",))
    with live_server(stub.app) as base:
        ok, why = await _client_for(stub, base).probe()
    assert ok is True and why == ""


# ================================================ 自建抽取服务路 ====


@pytest.mark.asyncio
async def test_http_backend_accepts_wrapped_data():
    stub = StubExtractService({"data": {"time": "9月30日"}})
    with live_server(stub.app) as base:
        c = ExtractModelClient(backend="http", http_url=f"{base}/extract", timeout=5.0)
        result = await c.extract_json("正文", instruction=INSTRUCTION)
    assert result.data == {"time": "9月30日"} and result.backend == "http"


@pytest.mark.asyncio
async def test_http_backend_accepts_bare_object():
    """顶层直接就是抽取结果也接受（契约里两条都写明）。"""
    stub = StubExtractService({"time": "本周五"})
    with live_server(stub.app) as base:
        c = ExtractModelClient(backend="http", http_url=f"{base}/extract", timeout=5.0)
        result = await c.extract_json("正文", instruction=INSTRUCTION)
    assert result.data == {"time": "本周五"}


@pytest.mark.asyncio
async def test_http_backend_sends_token_and_instruction():
    stub = StubExtractService({"data": {"x": 1}})
    with live_server(stub.app) as base:
        c = ExtractModelClient(backend="http", http_url=f"{base}/extract",
                               api_key="s3cret", timeout=5.0)
        await c.extract_json("正文", instruction=INSTRUCTION, schema_hint='{"x": 数字}')
    call = stub.calls[0]
    assert call["auth"] == "Bearer s3cret"
    assert call["body"]["text"] == "正文"
    assert '{"x": 数字}' in call["body"]["instruction"]     # schema 附在指令里，不污染正文


@pytest.mark.asyncio
async def test_http_backend_errors():
    """自建服务路同样分得清"失败"与"不可用"。"""
    stub = StubExtractService({"detail": "boom"}, status=500)
    with live_server(stub.app) as base:
        c = ExtractModelClient(backend="http", http_url=f"{base}/extract", timeout=5.0)
        with pytest.raises(ExtractFailure):
            await c.extract_json("正文", instruction=INSTRUCTION)
    c2 = ExtractModelClient(backend="http", http_url=f"http://127.0.0.1:{free_port()}/x",
                            timeout=1.0)
    with pytest.raises(ExtractUnavailable):
        await c2.extract_json("正文", instruction=INSTRUCTION)


@pytest.mark.asyncio
async def test_http_backend_non_json_is_failure():
    stub = StubExtractService(raw_text="<html>oops</html>")
    with live_server(stub.app) as base:
        c = ExtractModelClient(backend="http", http_url=f"{base}/extract", timeout=5.0)
        with pytest.raises(ExtractFailure) as exc:
            await c.extract_json("正文", instruction=INSTRUCTION)
    assert "JSON" in str(exc.value)


# ================================== 与既有调用方对接（真集成）====


@pytest.mark.asyncio
async def test_secondhand_describe_uses_unified_entry(monkeypatch):
    """**"后端可统一调用"的证据**：`secondhand_ai._model_describe` 走新入口并把 JSON 交回来。"""
    from app.services import secondhand_ai

    monkeypatch.setattr(settings, "extract_backend", "ollama")
    monkeypatch.setattr(settings, "extract_model", "xjt-extract-1.5b")
    monkeypatch.setattr(settings, "extract_base_url", "")
    em.reset_client()

    verdict = {"title": "优化标题", "description": "描述", "selling_points": ["a"],
               "suggested_price": 20, "price_min": 18, "price_max": 22, "reason": "同类均价"}
    stub = StubOllama(json.dumps(verdict, ensure_ascii=False))
    with live_server(stub.app) as base:
        monkeypatch.setattr(settings, "extract_base_url", base)
        em.reset_client()
        got = await secondhand_ai._model_describe({"标题": "教材", "分类": "教材"})

    assert got == verdict
    assert stub.last()["model"] == "xjt-extract-1.5b"


@pytest.mark.asyncio
async def test_secondhand_describe_degrades_safely_on_bad_json(monkeypatch):
    """模型吐坏 JSON 时：抽取层**抛异常**，调用方**显式**降级为 None（不炸发布流程）。"""
    from app.services import secondhand_ai

    stub = StubOllama("模型今天不想说 JSON")
    with live_server(stub.app) as base:
        monkeypatch.setattr(settings, "extract_base_url", base)
        em.reset_client()
        assert await secondhand_ai._model_describe({"标题": "教材"}) is None


@pytest.mark.asyncio
async def test_secondhand_describe_degrades_when_disabled(monkeypatch):
    """`XJT_EXTRACT_BACKEND=none` → 仍然优雅降级（改造前后一致）。"""
    from app.services import secondhand_ai

    monkeypatch.setattr(settings, "extract_backend", "none")
    em.reset_client()
    assert await secondhand_ai._model_describe({"标题": "教材"}) is None


# ================================================ 回环代理绕过 ====


def test_is_loopback_truth_table():
    for url in ("http://127.0.0.1:11434/api/chat", "http://127.0.0.5:1/x",
                "http://localhost:11434", "http://[::1]:11434"):
        assert is_loopback(url) is True, url
    for url in ("http://10.0.0.9:11434", "https://ollama.example.com",
                "http://172.217.1.1", "", "not-a-url"):
        assert is_loopback(url) is False, url


def test_proxy_bypass_kwargs_only_for_loopback():
    """**反向对照**：非回环地址不能一刀切关掉代理，否则经代理访问外部模型的部署会挂。"""
    assert proxy_bypass_kwargs("http://127.0.0.1:11434/api/chat") == {"trust_env": False}
    assert proxy_bypass_kwargs("https://ollama.example.com/api/chat") == {}


@pytest.mark.asyncio
async def test_loopback_bypass_effective_against_a_real_proxy(monkeypatch):
    """端到端：机器上配了真实代理时，回环上的抽取服务仍能直达。

    没有这条时，Windows 注册表里的系统代理会把 `127.0.0.1:11434` 也接走并回 502，
    表现为"本机 Ollama 明明活着，却一直抽取失败"。
    """
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")   # 必然连不上
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    stub = StubOllama('{"ok": true}')
    with live_server(stub.app) as base:
        c = ExtractModelClient(base_url=base, model="m", timeout=5.0)
        result = await c.extract_json("x", instruction=INSTRUCTION)
    assert result.data == {"ok": True}
