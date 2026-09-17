"""本地 Whisper 的 **HTTP 服务端**（C30）—— B32 `http` 后端的另一半。

## 为什么需要它

`XJT_ASR_BACKEND=whisper` 是在**主应用进程内**加载模型：装 `faster-whisper` 会拉
torch/ctranslate2（几百 MB），模型本身还要占内存，而且它**阻塞工作线程**（见 `CON-09`）。
把转写拆成独立进程后：

- 主应用侧只留一个 `http` 客户端（`http_remote.py`），**不必装 whisper**；
- 识别服务可以单独起在一台带 GPU / 内网的机器上，崩了也不影响主应用；
- **网络受限时的兜底路线**：云端识别用不了时，本机起这个服务即可（完全离线）。

## 与客户端的契约（由 `HttpRemoteBackend` 定义，本模块必须与之逐字对齐）

```
POST  <XJT_ASR_HTTP_URL>
      multipart/form-data
        file      : 音频字节（**字段名固定为 `file`**，客户端未暴露改名入口）
        language  : 语言，如 "zh"
        prompt    : 可选，中文热词/上下文（whisper 的 initial_prompt）
      Authorization: Bearer <XJT_ASR_HTTP_API_KEY>   ← 仅当主应用配了 key
→ 200 {"text": "...", "language": "zh", "duration_ms": 4200}
```

只有 `text` 是**必需**字段；其余可选（客户端会安全地取默认值）。

失败语义（客户端据此映射成 5002 / 5003）：

| 本服务返回 | 含义 | 客户端归类 |
|---|---|---|
| **连不上** | 服务没起 / 端口不通 | `AsrUnavailable` → **5002** |
| 401 / 400 / 413 | 本次请求有问题 | `AsrFailure` → **5003** |
| 503 | 本机没装 whisper 或模型加载失败 | `AsrFailure` → **5003**（但日志里原因明确） |
| 500 | 转写本身失败（音频损坏等） | `AsrFailure` → **5003** |

> ⚠️ **不要**为了"看起来成功"而返回 200 + 空 `text`：客户端与路由层都约定
> 「**转写失败必须给明确错误码，不许静默降级**」。空 `text` 只应表示
> 「后端真的没听出内容」。

## 启动

```bash
# 在 backend/ 目录下（需要 python-multipart，已在 requirements.txt）
python -m app.services.asr.server --host 0.0.0.0 --port 9001

# 主应用侧配置
#   XJT_ASR_BACKEND=http
#   XJT_ASR_HTTP_URL=http://127.0.0.1:9001/transcribe
#   XJT_ASR_HTTP_API_KEY=<与服务端 --token 一致，可不设>
```

**零业务依赖**：本模块不 import `app.db` / `app.routers` / `app.core.config`
（`main()` 里才惰性读配置），因此可以脱离数据库单独跑，也便于离线单测。
"""
from __future__ import annotations

import argparse
import os
from typing import Optional

from fastapi import FastAPI, File, Form, Header, UploadFile
from fastapi.responses import JSONResponse

from .base import AsrBackend, AsrFailure, AsrUnavailable
from .probe import SUPPORTED_HINT, sniff_audio, wav_duration_seconds

#: 默认上传上限。**刻意大于主应用的 `asr_max_bytes`（2MB）** ——
#: 主应用那一层已经按产品策略卡到 10 秒语音；本服务作为通用识别端，只挡住
#: 会把内存/算力打爆的请求，不重复实施产品策略。
DEFAULT_MAX_BYTES = 10 * 1024 * 1024

#: 单次读取的分片大小。**必须分片读**：`await upload.read()` 会先把整包读进内存，
#: 之后再用 `len()` 判断大小已经晚了（PR #60 审查踩过的坑）。
_CHUNK = 64 * 1024

#: 无 WAV 精确时长时的兜底上限（秒）。whisper 对长音频会显著变慢，
#: 挡住"传一个 1 小时录音"这类请求。
DEFAULT_MAX_SECONDS = 120.0


class _TooLarge(Exception):
    """超过本服务允许的上传上限。"""


async def _read_limited(upload: UploadFile, limit: int) -> bytes:
    """**带上限**地读完上传内容；超限抛 `_TooLarge`（不把整包读进内存）。"""
    declared = getattr(upload, "size", None)
    if isinstance(declared, int) and declared > limit:
        raise _TooLarge(declared)

    parts: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise _TooLarge(total)
        parts.append(chunk)
    return b"".join(parts)


def create_app(
    backend: Optional[AsrBackend] = None,
    *,
    token: str = "",
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_seconds: float = DEFAULT_MAX_SECONDS,
) -> FastAPI:
    """构建识别服务。

    参数可注入是**为了单测**：`backend` 传假后端就不必装 whisper；
    `token` 用来验证鉴权分支。
    """
    app = FastAPI(
        title="校捷通 · 本地语音识别服务（C30）",
        description="B32 `http` 后端的服务端实现：把音频转文字，失败给明确 HTTP 状态码。",
        version="1.0.0",
    )

    def _resolve() -> AsrBackend:
        """取后端：显式注入优先，否则按 `XJT_ASR_BACKEND` 规则构建 whisper 本地后端。"""
        if backend is not None:
            return backend
        from .whisper_local import WhisperLocalBackend  # noqa: PLC0415 - 惰性

        return WhisperLocalBackend(
            os.environ.get("XJT_ASR_WHISPER_MODEL", "small"),
            os.environ.get("XJT_ASR_WHISPER_DEVICE", "cpu"),
            os.environ.get("XJT_ASR_WHISPER_COMPUTE", "int8"),
        )

    @app.get("/health")
    def health() -> JSONResponse:
        """轻量预检：**不加载模型**（`availability()` 契约要求无副作用）。"""
        b = _resolve()
        ok, why = b.availability()
        return JSONResponse(
            status_code=200 if ok else 503,
            content={
                "ok": bool(ok),
                "backend": getattr(b, "name", "unknown"),
                "detail": why,
                "supported": SUPPORTED_HINT,
                "max_bytes": max_bytes,
            },
        )

    @app.post("/transcribe")
    async def transcribe(
        file: UploadFile = File(..., description="音频文件（wav/mp3/m4a/aac/ogg/webm/amr）"),
        language: str = Form("zh"),
        prompt: str = Form(""),
        authorization: str = Header(""),
    ) -> JSONResponse:
        # ---- 鉴权（仅当服务端配了 token 才校验；主应用若也配了 key 会带上）----
        if token:
            got = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
            if got != token:
                return JSONResponse(status_code=401, content={"detail": "鉴权失败：缺少或错误的 Authorization: Bearer"})

        # ---- 大小（**分片读**，超限不落内存）----
        try:
            audio = await _read_limited(file, max_bytes)
        except _TooLarge as exc:
            return JSONResponse(
                status_code=413,
                content={"detail": f"音频超过本服务上限 {max_bytes} 字节（收到约 {exc} 字节）"},
            )
        if not audio:
            return JSONResponse(status_code=400, content={"detail": "音频为空"})

        # ---- 格式：按**文件头魔数**判定，不信 Content-Type（与 SEC-11 同理）----
        sniffed = sniff_audio(audio[:64])
        if sniffed is None:
            return JSONResponse(
                status_code=400,
                content={"detail": f"不支持的音频格式（按文件头判定）。支持：{SUPPORTED_HINT}"},
            )
        ext, _mime = sniffed

        # ---- 时长兜底（只对 WAV 精确；算不出来就靠大小上限兜底，与原设计一致）----
        seconds = wav_duration_seconds(audio)
        if seconds is not None and seconds > max_seconds:
            return JSONResponse(
                status_code=413,
                content={"detail": f"音频时长 {seconds:.1f}s 超过本服务上限 {max_seconds:.0f}s"},
            )

        # ---- 转写：异常按「可用性」与「单次失败」分开，别混成一个码 ----
        b = _resolve()
        try:
            result = b.transcribe(audio, filename=f"audio{ext}", language=language, prompt=prompt)
        except AsrUnavailable as exc:
            return JSONResponse(status_code=503, content={"detail": f"识别后端不可用：{exc}"})
        except AsrFailure as exc:
            return JSONResponse(status_code=500, content={"detail": f"转写失败：{exc}"})

        # 真·空 text 是合法的（后端确实没听出内容）：仍返回 200 + 空串，
        # 由调用方决定怎么展示；**绝不用空串冒充"成功识别"**，也绝不因此报错。
        return JSONResponse(
            status_code=200,
            content={
                "text": result.text,
                "language": result.language or language,
                "duration_ms": result.duration_ms or int((seconds or 0) * 1000),
                # 额外字段：客户端只取 text/language/duration_ms，多给的会被放进 raw 便于排查
                "engine": result.backend,
            },
        )

    return app


def main(argv: Optional[list[str]] = None) -> int:
    """命令行入口：`python -m app.services.asr.server --port 9001`。"""
    ap = argparse.ArgumentParser(
        prog="python -m app.services.asr.server",
        description="本地 Whisper HTTP 服务（C30）—— B32 的 http 后端服务端",
    )
    ap.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1，局域网共用需 0.0.0.0）")
    ap.add_argument("--port", type=int, default=9001, help="监听端口（默认 9001）")
    ap.add_argument("--token", default="", help="可选；设置了则要求 Authorization: Bearer <token>")
    ap.add_argument("--model", default="", help="whisper 模型名（默认读 XJT_ASR_WHISPER_MODEL，再默认 small）")
    ap.add_argument("--device", default="", help="cpu / cuda（默认读 XJT_ASR_WHISPER_DEVICE）")
    ap.add_argument("--compute", default="", help="int8 / float16 等（默认读 XJT_ASR_WHISPER_COMPUTE）")
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    ap.add_argument("--max-seconds", type=float, default=DEFAULT_MAX_SECONDS)
    args = ap.parse_args(argv)

    if args.model:
        os.environ["XJT_ASR_WHISPER_MODEL"] = args.model
    if args.device:
        os.environ["XJT_ASR_WHISPER_DEVICE"] = args.device
    if args.compute:
        os.environ["XJT_ASR_WHISPER_COMPUTE"] = args.compute

    app = create_app(token=args.token, max_bytes=args.max_bytes, max_seconds=args.max_seconds)

    # 起服务前先如实报告能不能用 —— 否则第一个请求才暴露"没装 whisper"
    from .whisper_local import WhisperLocalBackend  # noqa: PLC0415

    probe = WhisperLocalBackend(
        os.environ.get("XJT_ASR_WHISPER_MODEL", "small"),
        os.environ.get("XJT_ASR_WHISPER_DEVICE", "cpu"),
        os.environ.get("XJT_ASR_WHISPER_COMPUTE", "int8"),
    )
    ok, why = probe.availability()
    kind = f"{probe.model_name} / {probe.device} / {probe.compute_type}"
    if not ok:
        print(f"[!] 识别后端当前不可用：{why}")
        print("[!] 服务仍会启动，但 /transcribe 会返回 503（而不是假装成功）。")
    else:
        print(f"[OK] 识别后端可用（{kind}）。模型在第一次转写时加载。")

    import uvicorn  # noqa: PLC0415 - 仅命令行路径需要

    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":  # pragma: no cover - 命令行入口
    raise SystemExit(main())
