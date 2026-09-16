"""本地 Whisper 后端（B32）—— `faster-whisper` 优先，退到 `openai-whisper`。

**惰性导入**是关键：这两个包分别是几百 MB 的依赖（会拉 torch / ctranslate2），
不能塞进 `requirements.txt` 强制所有人安装。因此：

- 装了就自动可用；
- 没装时 `availability()` 返回 `(False, 原因)`，路由层据此回 **5002（服务不可用）**
  并明确告诉运维该装什么或该改哪个配置项 —— **而不是在 import 期把整个应用打挂**。

模型与设备由配置驱动（`XJT_ASR_WHISPER_MODEL / _DEVICE / _COMPUTE`），
默认 `small / cpu / int8`：3~10 秒的中文短语音足够，且不依赖 GPU。
"""
from __future__ import annotations

import os
import tempfile
import threading

from .base import AsrFailure, AsrResult, AsrUnavailable


class WhisperLocalBackend:
    """进程内 Whisper（首次调用时才加载模型，加载后缓存）。"""

    name = "whisper"

    def __init__(self, model: str = "small", device: str = "cpu", compute_type: str = "int8") -> None:
        self.model_name = model or "small"
        self.device = device or "cpu"
        self.compute_type = compute_type or "int8"
        self._model = None
        self._engine = ""
        self._why = ""
        # 惰性加载要防并发：两个请求同时进来会各加载一遍模型（双倍内存与时间）
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 加载 ----

    def _load(self):
        if self._model is not None:          # 快路径：已加载
            return self._model
        with self._lock:
            return self._load_locked()

    def _load_locked(self):
        if self._model is not None:          # 双检：并发下只有第一个真正加载
            return self._model
        try:
            from faster_whisper import WhisperModel  # noqa: PLC0415 - 惰性、可选依赖

            self._model = WhisperModel(self.model_name, device=self.device,
                                       compute_type=self.compute_type)
            self._engine = "faster-whisper"
            return self._model
        except ImportError as exc:
            fast_why = f"faster-whisper 未安装（{exc}）"
        except Exception as exc:  # noqa: BLE001 - 模型下载/加载失败
            raise AsrUnavailable(f"faster-whisper 加载失败：{type(exc).__name__}: {exc}") from exc

        try:
            import whisper  # noqa: PLC0415 - 惰性、可选依赖

            self._model = whisper.load_model(self.model_name, device=self.device)
            self._engine = "openai-whisper"
            return self._model
        except ImportError as exc:
            self._why = f"{fast_why}；openai-whisper 也未安装（{exc}）"
        except Exception as exc:  # noqa: BLE001
            raise AsrUnavailable(f"openai-whisper 加载失败：{type(exc).__name__}: {exc}") from exc

        raise AsrUnavailable(
            f"{self._why}。请 `pip install faster-whisper`，"
            f"或把 XJT_ASR_BACKEND 改成 http 指向外部识别服务"
        )

    def availability(self) -> tuple[bool, str]:
        """**只做静态判断**（依赖能不能 import），**不加载模型**。

        契约要求「无副作用、可反复调用」：加载 small 模型要数秒、首次还可能触发下载，
        那是 `transcribe()` 的代价。把它藏在"轻量预检"里，会让路由的预检本身变成
        一次重阻塞（也是 review 实测事件循环被堵住的一半原因）。
        """
        if self._model is not None:
            return True, ""
        try:
            import faster_whisper  # noqa: F401,PLC0415 - 仅探测依赖是否存在
            return True, ""
        except ImportError:
            pass
        try:
            import whisper  # noqa: F401,PLC0415 - 仅探测依赖是否存在
            return True, ""
        except ImportError:
            return False, (
                "faster-whisper 与 openai-whisper 均未安装。"
                "请 `pip install faster-whisper`，"
                "或把 XJT_ASR_BACKEND 改成 http 指向外部识别服务"
            )

    # ------------------------------------------------------------ 转写 ----

    def transcribe(self, audio: bytes, *, filename: str = "", language: str = "zh",
                   prompt: str = "") -> AsrResult:
        model = self._load()                     # 不可用则在上面抛 AsrUnavailable
        suffix = os.path.splitext(filename or "")[1] or ".wav"

        fd, path = tempfile.mkstemp(prefix="xjt_asr_", suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(audio)
            try:
                if self._engine == "faster-whisper":
                    segments, info = model.transcribe(
                        path, language=language or None, initial_prompt=prompt or None,
                    )
                    text = "".join(getattr(s, "text", "") for s in segments).strip()
                    duration_ms = int(float(getattr(info, "duration", 0) or 0) * 1000)
                    lang = str(getattr(info, "language", language) or language)
                else:
                    out = model.transcribe(path, language=language or None,
                                           initial_prompt=prompt or None)
                    text = str(out.get("text") or "").strip()
                    duration_ms = 0
                    lang = str(out.get("language") or language)
            except AsrUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001 - 音频损坏/解码失败等
                raise AsrFailure(f"whisper 转写失败：{type(exc).__name__}: {exc}") from exc
        finally:
            try:
                os.unlink(path)
            except OSError:  # pragma: no cover - 临时文件清理失败不该影响结果
                pass

        return AsrResult(text=text, language=lang, duration_ms=duration_ms,
                         backend=f"{self.name}({self._engine})")
