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


@router.post("/image")
async def upload_image(file: UploadFile, user: dict = Depends(get_current_user)):
    ext = ALLOWED.get(file.content_type or "")
    if not ext:
        raise err_param("仅支持 jpg/png/webp 图片")
    data = await file.read()
    if len(data) > MAX_SIZE:
        raise err_param("图片不能超过 5MB")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    md5 = hashlib.md5(data).hexdigest()
    fname = f"{md5[:8]}_{int(time.time())}{ext}"
    path = UPLOAD_DIR / fname
    path.write_bytes(data)

    url = f"/static/uploads/{fname}"
    cpp_bridge.execute(
        "INSERT INTO image_asset (user_id, url, mime, size_bytes, md5) VALUES (?, ?, ?, ?, ?)",
        [int(user["id"]), url, file.content_type, len(data), md5],
    )
    return ok({"url": url, "size": len(data)})
