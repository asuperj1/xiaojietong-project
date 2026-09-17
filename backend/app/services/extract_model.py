"""结构化抽取模型客户端（C36）—— 后端**统一调用**、与**对话模型隔离**。

## 要解决的问题（实测）

C36 之前，后端有三处各自手写了一遍 Ollama `/api/chat` 调用：

| 位置 | 用途 | 形态 |
|---|---|---|
| `services/model_client.py` | 对话 | 流式 |
| `services/agent_executor.py` | 工具调用规划 | tools + JSON |
| `services/secondhand_ai.py` | 描述/定价抽取 | `format=json` |

它们共用**同一套** `ollama_base_url` / `ollama_model`，因此抽取与对话会互相挤
（同一个模型的显存与请求队列），也无法只给抽取换模型；其中 `secondhand_ai` 与
`agent_executor` 还漏了回环代理绕过（见 `app/core/net.py`）。本模块把**抽取**这一路
收成一个入口。

## 与对话模型隔离

隔离靠**配置**，因此在同一份代码上就能做到"抽取走另一台机器 / 另一个模型"：

```
XJT_EXTRACT_BASE_URL=http://10.0.0.9:11434   # 另一台 Ollama
XJT_EXTRACT_MODEL=xjt-extract-1.5b           # 抽取专用小模型
```

留空则回落到 `ollama_base_url` / `ollama_model` —— **默认零行为变化**。

## 失败语义：不静默降级

对话可以在模型不可用时回一句占位文案（用户看得见），**抽取不行**：编不出实体就得说编不出。
因此本模块只有两种结局：拿到 `ExtractResult`，或抛异常：

- `ExtractUnavailable` —— 现在用不了（没配地址 / `none` / 连不上）
- `ExtractFailure` —— 服务是好的，但这次抽取失败（4xx/5xx / 非 JSON / 模型没吐合法 JSON）

**绝不**返回 `{}` 或占位 JSON 冒充"抽取成功"。调用方若愿意降级（例如 `secondhand_ai`
回退到模板文案 + 统计定价），由调用方**显式**决定，并把 `source` 标出来 —— 降级要对用户可见。

零业务依赖：不 import `app.db` / `app.routers`，便于离线单测与独立脚本使用。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

import httpx

from app.core.config import settings
from app.core.net import proxy_bypass_kwargs

__all__ = [
    "ExtractFailure", "ExtractModelClient", "ExtractResult", "ExtractUnavailable",
    "extract_json", "get_client", "reset_client", "set_client",
]


class ExtractUnavailable(RuntimeError):
    """抽取模型现在不可用（未配置 / 关闭 / 服务不可达）。"""


class ExtractFailure(RuntimeError):
    """服务可用，但本次抽取失败。"""


@dataclass(frozen=True)
class ExtractResult:
    """一次成功的抽取结果。"""

    data: dict
    model: str = ""
    backend: str = ""
    truncated: bool = False
    raw: dict = field(default_factory=dict)


def _strip_code_fence(text: str) -> str:
    """去掉模型爱加的 ```json ... ``` 包裹。

    这是**真实模型**的常见输出形态，不是"猜"：只剥最外层围栏，剥不掉就原样送去解析，
    解析失败照样按 `ExtractFailure` 处理（不靠模糊匹配硬凑 JSON）。
    """
    s = (text or "").strip()
    if not s.startswith("```"):
        return s
    s = s[3:]
    if s[:4].lower() == "json":
        s = s[4:]
    s = s.strip()
    if s.endswith("```"):
        s = s[:-3]
    return s.strip()


def _parse_json_object(content: str, *, where: str) -> dict:
    """把模型输出解析成 JSON 对象；失败一律 `ExtractFailure`。"""
    try:
        data = json.loads(_strip_code_fence(content))
    except Exception as exc:  # noqa: BLE001 - 模型输出不可控
        raise ExtractFailure(
            f"{where}没有返回合法 JSON：{type(exc).__name__}: {exc}；"
            f"原文前 200 字：{(content or '')[:200]!r}"
        ) from exc
    if not isinstance(data, dict):
        raise ExtractFailure(f"{where}返回的 JSON 顶层不是对象（是 {type(data).__name__}）")
    return data


class ExtractModelClient:
    """抽取模型客户端：`ollama`（本机/内网 Ollama）或 `http`（自建抽取服务）。"""

    name = "extract"

    def __init__(
        self,
        *,
        backend: str = "",
        base_url: str = "",
        model: str = "",
        timeout: Optional[float] = None,
        http_url: str = "",
        api_key: str = "",
        max_chars: Optional[int] = None,
    ) -> None:
        # 参数留空 → 取配置；显式传入则优先（单测与多实例复用都靠这个）
        self.backend = (backend or getattr(settings, "extract_backend", "ollama") or "ollama").strip().lower()
        self.base_url = (base_url or getattr(settings, "extract_base_url", "")
                         or settings.ollama_base_url or "").rstrip("/")
        self.model = model or getattr(settings, "extract_model", "") or settings.ollama_model
        self.timeout = float(timeout if timeout is not None
                             else getattr(settings, "extract_timeout", 60.0))
        self.http_url = (http_url or getattr(settings, "extract_http_url", "") or "").strip()
        self.api_key = api_key or getattr(settings, "extract_http_api_key", "")
        self.max_chars = int(max_chars if max_chars is not None
                             else getattr(settings, "extract_max_chars", 4000))

    # ------------------------------------------------------------ 可用性 ----

    def availability(self) -> tuple[bool, str]:
        """静态判断能否使用（**不发请求、无副作用、可反复调用**）。"""
        if self.backend == "none":
            return False, "抽取模型已明确关闭（XJT_EXTRACT_BACKEND=none）"
        if self.backend == "http":
            if not self.http_url:
                return False, "XJT_EXTRACT_BACKEND=http 但未配置 XJT_EXTRACT_HTTP_URL"
            if not self.http_url.startswith(("http://", "https://")):
                return False, f"XJT_EXTRACT_HTTP_URL 必须是 http/https，当前为 {self.http_url!r}"
            return True, ""
        if self.backend == "ollama":
            if not self.base_url:
                return False, "未配置 XJT_EXTRACT_BASE_URL（也未配置 ollama_base_url）"
            if not self.base_url.startswith(("http://", "https://")):
                return False, f"抽取服务地址必须是 http/https，当前为 {self.base_url!r}"
            return True, ""
        return False, f"未知的 XJT_EXTRACT_BACKEND={self.backend!r}（可选 ollama / http / none）"

    async def probe(self) -> tuple[bool, str]:
        """**会发请求**的预检：问 Ollama 要模型清单，报告抽取模型是否已注册。

        与 `availability()` 分开正是为了不把"有副作用"混进"轻量预检"（B32 的教训）。
        """
        ok, why = self.availability()
        if not ok or self.backend != "ollama":
            return ok, why
        url = f"{self.base_url}/api/tags"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0),
                                         **proxy_bypass_kwargs(url)) as client:
                resp = await client.get(url)
        except Exception as exc:  # noqa: BLE001 - 连不上属于不可用
            return False, f"无法连接 {url}：{type(exc).__name__}: {exc}"
        if resp.status_code != 200:
            return False, f"{url} 返回 HTTP {resp.status_code}"
        try:
            names = [str(m.get("name", "")) for m in (resp.json().get("models") or [])]
        except Exception as exc:  # noqa: BLE001
            return False, f"模型清单不是合法 JSON：{type(exc).__name__}: {exc}"
        if self.model not in names:
            return False, (f"Ollama 在跑，但没有模型 {self.model!r}（已有：{names or '空'}）。"
                           f"见 ai/finetune/register_model.py 与 docs/模型分发与部署.md")
        return True, ""

    # ------------------------------------------------------------ 抽取 ----

    def _prepare(self, text: str) -> tuple[str, bool]:
        """截断超长输入。**截断会标记出来**（`truncated`），不静默丢内容。"""
        body = text or ""
        if self.max_chars > 0 and len(body) > self.max_chars:
            return body[: self.max_chars], True
        return body, False

    async def extract_json(
        self,
        text: str,
        *,
        instruction: str = "",
        schema_hint: str = "",
        temperature: float = 0.2,
        timeout: Optional[float] = None,
    ) -> ExtractResult:
        """把 `text` 抽成 JSON 对象。失败抛 `ExtractUnavailable` / `ExtractFailure`。

        `instruction` 是任务说明（进 system），`schema_hint` 是期望字段（会附在 system 末尾，
        **不污染正文**，便于同一段正文用不同字段集重复抽取）。
        """
        ok, why = self.availability()
        if not ok:
            raise ExtractUnavailable(why)

        body, truncated = self._prepare(text)
        system = (instruction or "").strip()
        if schema_hint:
            system = f"{system}\n期望字段（JSON）：{schema_hint}".strip()
        if not system:
            raise ExtractFailure("未提供抽取指令（instruction）")

        if self.backend == "http":
            return await self._extract_via_http(body, system, schema_hint, truncated,
                                                timeout if timeout is not None else self.timeout)
        return await self._extract_via_ollama(body, system, temperature, truncated,
                                              timeout if timeout is not None else self.timeout)

    # ------------------------------------------------- 后端：Ollama ----

    async def _extract_via_ollama(self, body: str, system: str, temperature: float,
                                  truncated: bool, timeout: float) -> ExtractResult:
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": body},
            ],
            "stream": False,
            "format": "json",              # 要 JSON 就别让模型自由发挥
            "options": {"temperature": temperature},
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout),
                                         **proxy_bypass_kwargs(url)) as client:
                resp = await client.post(url, json=payload)
        except ExtractUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - 连不上属于"不可用"
            raise ExtractUnavailable(
                f"无法连接抽取模型服务 {url}：{type(exc).__name__}: {exc}"
            ) from exc

        status = int(getattr(resp, "status_code", 0))
        if status >= 500:
            raise ExtractFailure(f"抽取模型服务返回 HTTP {status}：{(resp.text or '')[:200]}")
        if status >= 400:
            raise ExtractFailure(f"抽取模型服务拒绝了本次请求（HTTP {status}）：{(resp.text or '')[:200]}")
        try:
            raw = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise ExtractFailure(f"抽取模型服务返回的不是 JSON：{(resp.text or '')[:200]}") from exc
        if not isinstance(raw, dict):
            raise ExtractFailure("抽取模型服务返回的 JSON 顶层不是对象")
        message = raw.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise ExtractFailure(f"Ollama 响应缺少 message.content：{str(raw)[:200]}")

        data = _parse_json_object(content, where="抽取模型")
        return ExtractResult(data=data, model=self.model, backend="ollama",
                             truncated=truncated, raw=raw)

    # ------------------------------------------------- 后端：自建服务 ----

    async def _extract_via_http(self, body: str, system: str, schema_hint: str,
                                truncated: bool, timeout: float) -> ExtractResult:
        """打自建抽取服务。契约（`XJT_EXTRACT_HTTP_URL`）：

        请求 `POST` JSON：`{"text": ..., "instruction": ..., "schema": ...}`
        响应 JSON **对象**：顶层就是抽取结果；或 `{"data": {...}}` 包一层都接受。
        """
        url = self.http_url
        payload = {"text": body, "instruction": system, "schema": schema_hint}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout),
                                         **proxy_bypass_kwargs(url)) as client:
                resp = await client.post(url, json=payload, headers=headers)
        except ExtractUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ExtractUnavailable(
                f"无法连接抽取服务 {url}：{type(exc).__name__}: {exc}"
            ) from exc

        status = int(getattr(resp, "status_code", 0))
        if status >= 500:
            raise ExtractFailure(f"抽取服务返回 HTTP {status}：{(resp.text or '')[:200]}")
        if status >= 400:
            raise ExtractFailure(f"抽取服务拒绝了本次请求（HTTP {status}）：{(resp.text or '')[:200]}")
        try:
            raw = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise ExtractFailure(f"抽取服务返回的不是 JSON：{(resp.text or '')[:200]}") from exc
        if not isinstance(raw, dict):
            raise ExtractFailure("抽取服务返回的 JSON 顶层不是对象")

        inner = raw.get("data")
        data = inner if isinstance(inner, dict) else raw
        return ExtractResult(data=data, model=str(raw.get("model") or self.model),
                             backend="http", truncated=truncated, raw=raw)


# ---------------------------------------------------------------- 单例 ----
#
# 与 `services/asr` 同一套做法：**惰性构建 + 可注入**。
# 不在 import 期固化成常量，否则运维改了配置要重启才生效、单测也没法换。

_client: Optional[ExtractModelClient] = None


def get_client() -> ExtractModelClient:
    """取当前配置的客户端（首次调用时构建并缓存）。"""
    global _client
    if _client is None:
        _client = ExtractModelClient()
    return _client


def set_client(client: Optional[ExtractModelClient]) -> None:
    """注入客户端（主要给单测）；传 None 等于 reset。"""
    global _client
    _client = client


def reset_client() -> None:
    """清掉缓存，下次 `get_client()` 重新按配置构建。"""
    set_client(None)


async def extract_json(text: str, *, instruction: str = "", schema_hint: str = "",
                       temperature: float = 0.2) -> ExtractResult:
    """便捷入口：用当前配置的客户端抽一次。"""
    return await get_client().extract_json(text, instruction=instruction,
                                           schema_hint=schema_hint,
                                           temperature=temperature)
