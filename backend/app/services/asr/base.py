"""ASR 后端抽象（B32）。

设计目标：**转写失败必须给明确错误码，不许静默降级**。

因此把两类失败分开：

- `AsrUnavailable` —— "这个后端现在用不了"（没装依赖 / 没配地址 / 服务不可达）
  → 路由层映射成 **5002（模型服务不可用）**，文案直接告诉运维去看哪个配置项；
- `AsrFailure` —— "后端是好的，但这次转写失败了"（音频损坏 / 上游 5xx / 超时）
  → 路由层映射成 **5003（语音转写失败）**。

两条路径都不返回空字符串冒充成功 —— 空结果只可能来自后端**真的**没听出内容，
且响应里始终带 `text` 字段（哪怕是空串）供前端区分。

零业务依赖：不 import `app.db` / `app.routers`，便于离线单测与独立脚本使用。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class AsrUnavailable(RuntimeError):
    """后端不可用（未配置 / 依赖缺失 / 服务不可达）。"""


class AsrFailure(RuntimeError):
    """后端可用，但本次转写失败。"""


@dataclass(frozen=True)
class AsrResult:
    """一次成功的转写结果。"""

    text: str
    language: str = "zh"
    duration_ms: int = 0
    backend: str = ""
    raw: dict = field(default_factory=dict)


@runtime_checkable
class AsrBackend(Protocol):
    """所有 ASR 后端的最小接口。"""

    name: str

    def availability(self) -> tuple[bool, str]:
        """返回 (是否可用, 不可用原因)。**必须无副作用、可反复调用。**"""
        ...

    def transcribe(self, audio: bytes, *, filename: str = "", language: str = "zh",
                   prompt: str = "") -> AsrResult:
        """转写；失败抛 AsrUnavailable / AsrFailure。"""
        ...


class NullBackend:
    """占位后端：明确表示"本机没配语音转写"。"""

    name = "none"

    def availability(self) -> tuple[bool, str]:
        return False, "未配置语音转写后端（XJT_ASR_BACKEND 为空或 none）"

    def transcribe(self, audio: bytes, *, filename: str = "", language: str = "zh",
                   prompt: str = "") -> AsrResult:  # pragma: no cover - 由路由层拦在前面
        raise AsrUnavailable("未配置语音转写后端")
