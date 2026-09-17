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

⚠️ **"换了模型名"不等于"隔离了"**：只要 `XJT_EXTRACT_BASE_URL` 还指向同一台 Ollama，
两个模型就共享同一个进程的显存与请求队列（Ollama 按需 load/evict，两个模型同时驻留时
显存照样互相挤）。真正的隔离是**换地址**（另一台机器 / 另一个容器）。默认值（留空 = 沿用
`ollama_*`）就是"隔离未生效"，`isolation_report()` 会如实说出来，`/health/detail` 照实显示。

**并发闸门**：抽取常被批量调用（批量导入、定时任务），`XJT_EXTRACT_MAX_CONCURRENCY`
（默认 2；0 = 不限）限制同时在飞的抽取请求数，避免批量抽取把对话模型与显存挤掉；
`XJT_EXTRACT_KEEP_ALIVE`（默认空 = Ollama 默认）可控制抽取模型驻留时长。

**连接池复用**：出站 `AsyncClient` 按"是否绕代理"缓存复用（回环要 `trust_env=False`、
外网要保持 httpx 默认，两者不能共用一个客户端），不会每次都重建连接。

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

import asyncio
import contextlib
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


def _first_text(*candidates: Optional[str]) -> str:
    """取第一个**非空白**候选值。

    配置里写了个空格（`XJT_EXTRACT_MODEL=" "`）不该被当成"已配置"：
    它既不回落、又会变成一个非法模型名 —— 所以先 `strip()` 再判空。
    """
    for c in candidates:
        s = (c or "").strip()
        if s:
            return s
    return ""


def _model_names(payload: object) -> list[str]:
    """从 Ollama `/api/tags` 的响应里取模型名；`{"name": null}` / 非对象条目一律跳过。

    别写成 `str(m.get("name", ""))` —— `dict.get` 的默认值只在**键不存在**时生效，
    键存在但值为 `null` 时会得到字面量 `"None"`，污染错误信息。
    """
    models = payload.get("models") if isinstance(payload, dict) else None
    names: list[str] = []
    for m in models or []:
        name = m.get("name") if isinstance(m, dict) else None
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return names


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
        max_concurrency: Optional[int] = None,
        keep_alive: str = "",
    ) -> None:
        # 参数留空（或只写了空格）→ 取配置；显式传入则优先（单测与多实例复用都靠这个）
        self.backend = (_first_text(backend, getattr(settings, "extract_backend", ""))
                        or "ollama").lower()
        self.base_url = _first_text(base_url, getattr(settings, "extract_base_url", ""),
                                    settings.ollama_base_url).rstrip("/")
        self.model = _first_text(model, getattr(settings, "extract_model", ""),
                                 settings.ollama_model)
        self.timeout = float(timeout if timeout is not None
                             else getattr(settings, "extract_timeout", 60.0))
        self.http_url = _first_text(http_url, getattr(settings, "extract_http_url", ""))
        self.api_key = _first_text(api_key, getattr(settings, "extract_http_api_key", ""))
        self.max_chars = int(max_chars if max_chars is not None
                             else getattr(settings, "extract_max_chars", 4000))
        self.max_concurrency = int(max_concurrency if max_concurrency is not None
                                   else getattr(settings, "extract_max_concurrency", 2))
        self.keep_alive = _first_text(keep_alive, getattr(settings, "extract_keep_alive", ""))
        # 惰性构造（`asyncio.Semaphore` / `AsyncClient` 都要求有事件循环时才安全创建）
        self._sem: Optional[asyncio.Semaphore] = None
        self._clients: dict[bool, httpx.AsyncClient] = {}

    # ------------------------------------------------------------ 隔离 ----

    def isolation_report(self) -> dict:
        """如实报告"隔离到底有没有生效"（给运维与 `/health/detail` 看）。

        本模块标题里的"隔离"靠配置实现，因此默认值（留空 = 沿用 `ollama_*`）下它是**零**。
        这里不粉饰：`isolated` 只在**换了地址**（另一台机器 / 另一个容器）时为真 ——
        "只换了模型名但同地址"仍然共享显存与请求队列，所以照样算未隔离。
        """
        chat_url = (settings.ollama_base_url or "").rstrip("/")
        chat_model = settings.ollama_model
        same_host = self.base_url.rstrip("/") == chat_url
        same_model = self.model == chat_model
        if not same_host:
            note = "抽取指向另一台机器：请求队列与显存独立"
        elif not same_model:
            note = ("抽取换了模型，但仍在同一台 Ollama 上：两个模型共享显存与队列"
                    "（Ollama 按需 load/evict）；要真隔离需把 XJT_EXTRACT_BASE_URL 指向另一台机器")
        else:
            note = "抽取与对话同地址同模型 —— 隔离未生效（留空即为此默认值）"
        return {
            "isolated": not same_host,
            "same_host": same_host,
            "same_model": same_model,
            "chat_base_url": chat_url,
            "chat_model": chat_model,
            "note": note,
        }

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
            client = self._http_client(url)
            resp = await client.get(url, timeout=httpx.Timeout(5.0))
        except Exception as exc:  # noqa: BLE001 - 连不上属于不可用
            return False, f"无法连接 {url}：{type(exc).__name__}: {exc}"
        if resp.status_code != 200:
            return False, f"{url} 返回 HTTP {resp.status_code}"
        try:
            names = _model_names(resp.json())
        except Exception as exc:  # noqa: BLE001
            return False, f"模型清单不是合法 JSON：{type(exc).__name__}: {exc}"
        if not self._is_registered(names):
            return False, (f"Ollama 在跑，但没有模型 {self.model!r}（已有：{names or '空'}）。"
                           f"见 ai/finetune/register_model.py 与 docs/模型分发与部署.md")
        return True, ""

    def _is_registered(self, names: list[str]) -> bool:
        """模型清单是否包含本次要用的模型。

        Ollama 的清单带 tag（`xjt-3b:latest`），而配置里通常写不带 tag 的名字；
        Ollama 收到 `model="xjt-3b"` 时会按 `xjt-3b:latest` 解析，因此：

        - 配置**不带** tag → 比 base name（与 `routers/health.py` 同口径）
        - 配置**带** tag → 必须精确命中（`xjt-3b:v2` 不能被 `xjt-3b:latest` 顶替）
        """
        if self.model in names:
            return True
        if ":" in self.model:
            return False
        return any(n.split(":")[0] == self.model for n in names)

    # ------------------------------------------------------------ 出站 ----

    def _http_client(self, url: str) -> httpx.AsyncClient:
        """按"是否绕过代理"缓存 `AsyncClient`，复用连接池。

        `trust_env` 是**客户端级**参数（回环必须 False、外网要保持 httpx 默认），
        所以两种情形各缓存一个客户端，不能混用一个；超时按请求传入（`timeout=`），
        因此同一个池可以服务不同超时的调用。
        """
        key = "trust_env" not in proxy_bypass_kwargs(url)
        client = self._clients.get(key)
        if client is None or client.is_closed:
            client = httpx.AsyncClient(**proxy_bypass_kwargs(url))
            self._clients[key] = client
        return client

    async def aclose(self) -> None:
        """关掉内部复用的连接池（长驻进程一般不需要；测试与优雅退出可用）。"""
        for client in list(self._clients.values()):
            with contextlib.suppress(Exception):
                await client.aclose()
        self._clients.clear()

    @contextlib.asynccontextmanager
    async def _concurrency_gate(self):
        """并发闸门：限制同时在飞的抽取请求数（`max_concurrency <= 0` 时不限）。

        抽取是**批量**负载（批量导入 / 定时任务），没有闸门时几十个请求会同时压向同一个
        Ollama，把对话模型与显存挤掉。
        """
        if self.max_concurrency <= 0:
            yield
            return
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.max_concurrency)
        async with self._sem:
            yield

    # ------------------------------------------------------------ 抽取 ----

    def _prepare(self, text: str) -> tuple[str, bool]:
        """截断超长输入。**截断会标记出来**（`truncated`），不静默丢内容。

        ⚠️ 这是**按字符**的通用护栏，会从任意位置切断 —— 所以调用方**不能**把"整份 JSON
        当正文"直接喂进来（会得到非法 JSON）。需要保证结构完整时，请先在调用方裁剪
        （见 `services/secondhand_ai.py::_fit_payload`）。
        """
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

        async with self._concurrency_gate():
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
        if self.keep_alive:
            # 控制抽取模型驻留时长（如 "5m" / 0 = 用完即卸），显存紧的部署靠它
            payload["keep_alive"] = self.keep_alive
        try:
            resp = await self._http_client(url).post(url, json=payload,
                                                     timeout=httpx.Timeout(timeout))
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

        `data` 存在但**不是对象** → `ExtractFailure`：那说明响应不符合契约，
        把整个信封当结果返回会让调用方拿到混着传输层字段的噪声（本模块不干这种事）。
        """
        url = self.http_url
        payload = {"text": body, "instruction": system, "schema": schema_hint}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
        try:
            resp = await self._http_client(url).post(url, json=payload, headers=headers,
                                                     timeout=httpx.Timeout(timeout))
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
        if "data" in raw and not isinstance(inner, dict):
            kind = type(inner).__name__
            raise ExtractFailure(
                f"抽取服务返回的 data 字段不是对象（是 {kind}）"
                f'：契约要求 {{"data": {{...}}}}；原文前 200 字：{str(raw)[:200]}'
            )
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
