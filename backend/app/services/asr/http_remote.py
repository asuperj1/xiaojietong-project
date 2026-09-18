"""外部 HTTP ASR 后端（B32）。

约定：把音频以 multipart 形式 POST 给 `XJT_ASR_HTTP_URL`，期望返回 JSON：

```json
{ "text": "今天图书馆几点关门", "language": "zh", "duration_ms": 4200 }
```

只有 `text` 是必需的，其余可选。这样任何"能接音频、能吐 JSON"的服务
（自建 whisper-server / 云厂商一句话识别 / 内网 GPU 机）都能直接挂上来。

失败分类（对应路由层的错误码）：

- **连不上 / 地址没配** → `AsrUnavailable` → 5002（服务不可用）
- **上游 4xx/5xx、返回非 JSON、缺 text** → `AsrFailure` → 5003（转写失败）

注意 `post` 可注入：单测不需要真的起一个 ASR 服务。

**回环地址必须绕过代理**（C30 实测踩到）：`httpx` 默认 `trust_env=True`，会读环境变量
**以及 Windows 注册表里的系统代理**（`urllib.request.getproxies()` 的返回值）。若机器上
配了代理，发往 `http://127.0.0.1:9001/transcribe` 的请求会被**代理**接走并回 502，
本服务明明活着却报「转写失败 5003」—— 而"网络受限所以本机自建识别服务"恰恰就是
配了代理的场景，不能指望运维自己去设 `NO_PROXY`。因此对回环地址显式 `trust_env=False`；
非回环地址保持 httpx 默认行为（内网/云端识别仍需走代理的情况不受影响）。
"""
from __future__ import annotations

import ipaddress
import urllib.parse
from typing import Callable, Optional

import httpx

from .base import AsrFailure, AsrResult, AsrUnavailable

#: 视为"本机"的主机名。回环地址永远不需要代理。
#: ⚠️ `0.0.0.0` **不在这里** —— 它是"监听全部网卡"的 bind 地址，不是回环地址。
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _is_loopback(url: str) -> bool:
    """URL 是否指向本机。

    ⚠️ **不能用 `host.startswith("127.")` 判回环** —— 那是**字符串前缀**而不是 IP 段：
    `127.evil.com` / `127.attacker.test` 都是合法 DNS 名，却会被判成"本机"，
    于是对**远程**主机关掉代理，与"非回环保持 httpx 默认"的意图正好相反。
    用 `ipaddress` 按真实 IP 段判断（顺带覆盖 `127.0.0.0/8` 里除 .0.0.1 之外的地址）。
    """
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False          # 不是 IP 字面量（是域名）⇒ 交给 DNS 解析，不当作本机


class HttpRemoteBackend:
    """把音频转发给外部 ASR 服务。"""

    name = "http"

    def __init__(
        self,
        url: str = "",
        *,
        timeout: float = 15.0,
        api_key: str = "",
        field_name: str = "file",
        post: Optional[Callable[..., httpx.Response]] = None,
    ) -> None:
        self.url = (url or "").strip()
        self.timeout = float(timeout)
        self.api_key = api_key
        self.field_name = field_name
        self._post = post or httpx.post

    # ------------------------------------------------------------ 可用性 ----

    def availability(self) -> tuple[bool, str]:
        if not self.url:
            return False, "未配置 XJT_ASR_HTTP_URL（外部语音识别服务地址）"
        if not self.url.startswith(("http://", "https://")):
            return False, f"XJT_ASR_HTTP_URL 必须是 http/https 地址，当前为 {self.url!r}"
        return True, ""

    # ------------------------------------------------------------ 转写 ----

    def transcribe(self, audio: bytes, *, filename: str = "", language: str = "zh",
                   prompt: str = "") -> AsrResult:
        ok, why = self.availability()
        if not ok:
            raise AsrUnavailable(why)

        files = {self.field_name: (filename or "audio.bin", audio)}
        data = {"language": language}
        if prompt:
            data["prompt"] = prompt
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
        # 回环地址绕过代理（见模块 docstring）；非回环保持 httpx 默认（trust_env=True）
        bypass = {"trust_env": False} if _is_loopback(self.url) else {}

        try:
            resp = self._post(self.url, files=files, data=data, headers=headers,
                              timeout=self.timeout, **bypass)
        except AsrUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - 连不上属于"服务不可用"
            raise AsrUnavailable(
                f"无法连接 ASR 服务 {self.url}：{type(exc).__name__}: {exc}"
            ) from exc

        status = int(getattr(resp, "status_code", 0))
        body = getattr(resp, "text", "") or ""
        # 5xx 一律归 `AsrFailure`（下游 5003）—— 这是 C30 服务端 docstring 里
        # **明确约定**的（「503 | 本机没装 whisper 或模型加载失败 | AsrFailure → 5003
        # （但日志里原因明确）」），**不要看着像 bug 就顺手改成 5002**。
        # ⚠️ 注意：它和 `routers/voice.py` 的错误码表对**同一场景**的归类不一致
        # （那张表把"依赖缺失"归到 5002）。这是两份契约之间的口径分歧，
        # 属**产品/契约决策**，已登记到 `docs/技术方向待处理问题.md`，此处保持现状。
        if status >= 500:
            raise AsrFailure(f"ASR 服务返回 HTTP {status}：{body[:200]}")
        if status >= 400:
            raise AsrFailure(f"ASR 服务拒绝了本次请求（HTTP {status}）：{body[:200]}")

        try:
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise AsrFailure(f"ASR 服务返回的不是 JSON：{body[:200]}") from exc

        if not isinstance(payload, dict):
            raise AsrFailure("ASR 服务返回的 JSON 顶层不是对象")
        if "text" not in payload:
            raise AsrFailure("ASR 服务返回的 JSON 缺少 text 字段（契约要求至少含 text）")

        return AsrResult(
            text=str(payload.get("text") or "").strip(),
            language=str(payload.get("language") or language),
            duration_ms=int(payload.get("duration_ms") or 0),
            backend=self.name,
            raw=payload,
        )
