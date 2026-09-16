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
"""
from __future__ import annotations

from typing import Callable, Optional

import httpx

from .base import AsrFailure, AsrResult, AsrUnavailable


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

        try:
            resp = self._post(self.url, files=files, data=data, headers=headers,
                              timeout=self.timeout)
        except AsrUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - 连不上属于"服务不可用"
            raise AsrUnavailable(
                f"无法连接 ASR 服务 {self.url}：{type(exc).__name__}: {exc}"
            ) from exc

        status = int(getattr(resp, "status_code", 0))
        body = getattr(resp, "text", "") or ""
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
