"""音频体检（B32）：魔数白名单 + 时长探测。

**为什么按魔数而不是 Content-Type**：与 `routers/upload.py` 的 SEC-11 同理 ——
客户端提交的 `Content-Type` 完全不可信，只看它会放过伪装成音频的任意文件。

**为什么时长只对 WAV 精确**：WAV 是简单容器，标准库 `wave` 就能算出精确时长；
mp3/m4a/ogg 需要解帧或解容器（mp3 还有 VBR 问题），不引入 ffprobe 很难算准。
因此策略是：**能算就算并据此校验，算不出来就放行**（靠大小上限兜底），
并把后端返回的真实时长透传给前端。
"""
from __future__ import annotations

import io
import wave
from typing import Optional

#: (判定函数, 扩展名, MIME)。顺序无所谓，互斥。
_MAGIC: tuple[tuple, ...] = (
    (lambda h: len(h) >= 12 and h[:4] == b"RIFF" and h[8:12] == b"WAVE", ".wav", "audio/wav"),
    (lambda h: h.startswith(b"ID3"), ".mp3", "audio/mpeg"),
    (lambda h: len(h) >= 2 and h[0] == 0xFF and h[1] in (0xF2, 0xF3, 0xFB, 0xFA, 0xF1),
     ".mp3", "audio/mpeg"),
    (lambda h: len(h) >= 12 and h[4:8] == b"ftyp", ".m4a", "audio/mp4"),
    (lambda h: h.startswith(b"OggS"), ".ogg", "audio/ogg"),
    (lambda h: h.startswith(b"#!AMR"), ".amr", "audio/amr"),
    (lambda h: h.startswith(b"\x1a\x45\xdf\xa3"), ".webm", "audio/webm"),
)

#: 给用户看的可读格式清单（与 _MAGIC 保持一致）
SUPPORTED_HINT = "wav / mp3 / m4a / aac / ogg / webm / amr"


def sniff_audio(head: bytes) -> Optional[tuple[str, str]]:
    """按文件头判定真实格式，返回 `(扩展名, mime)`；不在白名单则返回 None。"""
    if not head:
        return None
    for matches, ext, mime in _MAGIC:
        try:
            if matches(head):
                return ext, mime
        except Exception:  # noqa: BLE001 - 判定函数不该拖垮请求
            continue
    return None


def wav_duration_seconds(data: bytes) -> Optional[float]:
    """精确解析 WAV 时长（秒）；非 WAV 或解析失败返回 None。"""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    try:
        with wave.open(io.BytesIO(data), "rb") as wf:
            rate = wf.getframerate()
            if rate <= 0:
                return None
            return wf.getnframes() / float(rate)
    except Exception:  # noqa: BLE001 - 截断/损坏的 WAV
        return None


def make_wav(seconds: float, *, rate: int = 16000) -> bytes:
    """生成一段静音 WAV（**仅供单测构造样本**，不参与运行时逻辑）。"""
    frames = int(seconds * rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * frames)
    return buf.getvalue()
