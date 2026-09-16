"""语音转文字（B32）。

契约：`docs/api.md` §13

```
POST /api/v1/voice/transcribe        multipart/form-data
  file     音频文件（必填）
  language 语言，默认 zh
  prompt   可选热词，帮助识别专有名词（如"校捷通""前卫南区"）
→ { "code": 0, "message": "ok",
    "data": { "text": "今天图书馆几点关门", "language": "zh",
              "duration_ms": 4200, "backend": "http", "format": "wav" } }
```

**失败一律给明确错误码，绝不静默**（任务卡验收项）：

| 场景 | code | HTTP |
|---|---|---|
| 格式不支持 / 文件太大 / 时长不在区间 | `1001` | 400 |
| 未配置后端、依赖缺失、连不上 ASR 服务 | `5002` | 503 |
| 后端可用但本次转写失败（上游 4xx/5xx、音频损坏） | `5003` | 500 |

与 `routers/upload.py` 一致，类型判定走**文件头魔数**而非 `Content-Type`
（后者完全不可信），读取也是**分块限长**，避免超大请求体撑爆内存。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.core.config import settings
from app.core.deps import get_current_user
from app.core.response import BizError, err_param, ok
from app.services import asr
from app.services.asr import AsrFailure, AsrUnavailable

router = APIRouter(prefix="/voice", tags=["voice"])

#: 5002 沿用 api.md 已有的「模型服务不可用」；5003 为 B32 新增（已登记契约）
CODE_ASR_UNAVAILABLE = 5002
CODE_ASR_FAILED = 5003

_CHUNK = 64 * 1024


async def _read_limited(file: UploadFile, limit: int) -> bytes:
    """分块读取，超限立即中断（同 upload.py：直接 read() 会先把整个请求体吃进内存）。"""
    buf = bytearray()
    while True:
        chunk = await file.read(_CHUNK)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > limit:
            raise err_param(
                f"语音文件不能超过 {limit / 1024 / 1024:.1f}MB"
                f"（10 秒语音通常不到 200KB，请确认没有传错文件）"
            )
    return bytes(buf)


@router.post("/transcribe")
async def transcribe(
    file: UploadFile = File(..., description="音频文件（wav/mp3/m4a/ogg/webm/amr）"),
    language: str = Form("zh", description="语言代码，默认 zh"),
    prompt: str = Form("", description="可选热词提示"),
    user: dict = Depends(get_current_user),
):
    """把 3~10 秒的中文语音转成文字。"""
    data = await _read_limited(file, settings.asr_max_bytes)
    if not data:
        raise err_param("音频内容为空")

    probed = asr.sniff_audio(data[:64])
    if probed is None:
        raise err_param(f"不支持的音频格式，仅支持 {asr.SUPPORTED_HINT}")
    ext, _mime = probed

    # WAV 能精确算时长就据此校验；其它格式算不出（不引入 ffprobe），靠大小上限兜底
    seconds = asr.wav_duration_seconds(data)
    if seconds is not None:
        lo, hi = settings.asr_min_seconds, settings.asr_max_seconds
        if seconds < lo:
            raise err_param(f"语音太短（{seconds:.1f} 秒），请录制 {lo:.0f}~{hi:.0f} 秒")
        if seconds > hi:
            raise err_param(f"语音太长（{seconds:.1f} 秒），请录制 {lo:.0f}~{hi:.0f} 秒")

    backend = asr.get_backend()
    usable, why = backend.availability()
    if not usable:
        # 明确告诉运维"该配什么"，而不是回一个空字符串让前端以为识别失败
        raise BizError(CODE_ASR_UNAVAILABLE, f"语音转写服务不可用：{why}", http_status=503)

    try:
        result = backend.transcribe(
            data, filename=file.filename or f"audio{ext}",
            language=(language or "zh").strip(), prompt=(prompt or "").strip(),
        )
    except AsrUnavailable as exc:
        raise BizError(CODE_ASR_UNAVAILABLE, f"语音转写服务不可用：{exc}", http_status=503) from exc
    except AsrFailure as exc:
        raise BizError(CODE_ASR_FAILED, f"语音转写失败：{exc}", http_status=500) from exc

    return ok({
        "text": result.text,
        "language": result.language,
        "duration_ms": result.duration_ms or int((seconds or 0.0) * 1000),
        "backend": result.backend,
        "format": ext.lstrip("."),
    })
