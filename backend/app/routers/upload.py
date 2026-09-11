"""文件上传：图片（占位保存到本地 uploads/，生产接对象存储）。

契约：docs/api.md §12
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from fastapi import APIRouter, Depends, UploadFile

from app.core.deps import get_current_user
from app.core.response import err_param, ok
from app.db import cpp_bridge

router = APIRouter(prefix="/upload", tags=["upload"])

UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "uploads"
ALLOWED = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_SIZE = 5 * 1024 * 1024  # 5MB
_CHUNK = 64 * 1024

# 审计 SEC-11：客户端提交的 Content-Type **完全不可信**，仅看它会导致
# 上传伪装成图片的 HTML/SVG（配合 /static/uploads 公开访问 → 存储型 XSS）。
# 因此必须按**文件头魔数**判定真实类型。
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", ".jpg"),                 # JPEG
    (b"\x89PNG\r\n\x1a\n", ".png"),            # PNG
)
_EXT_MIME = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def _sniff_ext(head: bytes) -> str | None:
    """按文件头魔数判定真实扩展名；非白名单图片返回 None。"""
    for magic, ext in _MAGIC:
        if head.startswith(magic):
            return ext
    # WebP 是 RIFF 容器：`RIFF____WEBP`
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return None


async def _read_limited(file: UploadFile, limit: int) -> bytes:
    """分块读取，超限立即中断。

    直接 `await file.read()` 会先把整个请求体读进内存再判大小，攻击者可用
    超大文件撑爆内存（审计 SEC-11）。
    """
    buf = bytearray()
    while True:
        chunk = await file.read(_CHUNK)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > limit:
            raise err_param(f"图片不能超过 {limit // 1024 // 1024}MB")
    return bytes(buf)


@router.post("/image")
async def upload_image(file: UploadFile, user: dict = Depends(get_current_user)):
    data = await _read_limited(file, MAX_SIZE)
    if not data:
        raise err_param("文件为空")

    # 以文件内容为准判定类型；客户端声明的 Content-Type 只用于一致性校验
    real_ext = _sniff_ext(data[:16])
    if real_ext is None:
        raise err_param("文件内容不是有效的 jpg/png/webp 图片")
    claimed_ext = ALLOWED.get(file.content_type or "")
    if claimed_ext and claimed_ext != real_ext:
        # 不静默纠正：声明与实际不符是明确的攻击信号，直接拒绝
        raise err_param("文件类型与内容不符")
    ext = real_ext

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    md5 = hashlib.md5(data).hexdigest()
    fname = f"{md5[:8]}_{int(time.time())}{ext}"
    path = UPLOAD_DIR / fname
    path.write_bytes(data)

    url = f"/static/uploads/{fname}"
    cpp_bridge.execute(
        "INSERT INTO image_asset (user_id, url, mime, size_bytes, md5) VALUES (?, ?, ?, ?, ?)",
        [int(user["id"]), url, _EXT_MIME[ext], len(data), md5],
    )
    return ok({"url": url, "size": len(data)})
