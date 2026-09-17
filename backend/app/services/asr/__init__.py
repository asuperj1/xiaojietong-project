"""语音转写门面（B32）：按配置挑选后端。

```
XJT_ASR_BACKEND = none   → NullBackend（默认；接口明确回 5002，而不是假装成功）
                = http   → HttpRemoteBackend（转发给外部 ASR 服务，见 http_remote.py）
                = whisper→ WhisperLocalBackend（本机 faster-whisper / openai-whisper）
```

后端实例**缓存**在模块级（whisper 加载一次模型要好几秒，不能每个请求都加载）；
单测用 `set_backend()` 注入假后端、`reset_backend()` 还原。

可选依赖不进 `requirements.txt`：whisper 会拉 torch/ctranslate2（几百 MB），
不该让所有人和 CI 都装 —— 装了就自动可用，没装就走 5002 并说清怎么办。
"""
from __future__ import annotations

from typing import Optional

from .base import AsrBackend, AsrFailure, AsrResult, AsrUnavailable, NullBackend
from .probe import SUPPORTED_HINT, sniff_audio, wav_duration_seconds

__all__ = [
    "AsrBackend", "AsrFailure", "AsrResult", "AsrUnavailable", "NullBackend",
    "SUPPORTED_HINT", "get_backend", "reset_backend", "set_backend",
    "sniff_audio", "wav_duration_seconds",
]

_backend: Optional[AsrBackend] = None


def get_backend() -> AsrBackend:
    """取当前配置对应的后端（惰性构建并缓存）。"""
    global _backend
    if _backend is not None:
        return _backend

    from app.core.config import settings  # noqa: PLC0415 - 惰性，避免包级耦合

    kind = (getattr(settings, "asr_backend", "") or "none").strip().lower()
    if kind == "http":
        from .http_remote import HttpRemoteBackend  # noqa: PLC0415

        _backend = HttpRemoteBackend(
            getattr(settings, "asr_http_url", ""),
            timeout=float(getattr(settings, "asr_http_timeout", 15.0)),
            api_key=getattr(settings, "asr_http_api_key", ""),
        )
    elif kind == "whisper":
        from .whisper_local import WhisperLocalBackend  # noqa: PLC0415

        _backend = WhisperLocalBackend(
            getattr(settings, "asr_whisper_model", "small"),
            getattr(settings, "asr_whisper_device", "cpu"),
            getattr(settings, "asr_whisper_compute", "int8"),
        )
    else:
        _backend = NullBackend()
    return _backend


def set_backend(backend: Optional[AsrBackend]) -> None:
    """注入指定后端（主要给单测用）；传 None 等于 reset。"""
    global _backend
    _backend = backend


def reset_backend() -> None:
    """清掉缓存，下次 `get_backend()` 重新按配置构建。"""
    set_backend(None)
