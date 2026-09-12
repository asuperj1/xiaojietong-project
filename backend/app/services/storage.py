"""文件存储抽象（B14）：本地实现 + 对象存储（S3/OSS）预留接口。

**目标**：把"文件存在哪"从业务代码里解耦 —— ``upload.py`` 只调用
``get_storage().save() / url_for()``；切换到对象存储时新增一个实现类即可，
业务代码零改动（配置 ``XJT_STORAGE_BACKEND=local|s3|oss``）。

- 当前实现：``LocalStorage``（本地 ``backend/uploads/``，URL 前缀 ``/static/uploads``）；
- 预留：``ObjectStoragePlaceholder``（S3/OSS 的接口占位，接入 SDK 后替换，
  未实现前回退本地实现，避免配置切换导致启动失败）。
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Protocol

from app.core.config import settings
from app.core.url_sign import signed_url

_UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "uploads"
_URL_PREFIX = "/static/uploads"


class StorageBackend(Protocol):
    """存储后端接口（本地 / 对象存储统一契约）。"""

    def save(self, data: bytes, ext: str) -> dict: ...

    def url_for(self, key: str, *, signed: bool = True) -> str: ...

    def delete(self, key: str) -> None: ...


class LocalStorage:
    """本地文件系统实现（开发与单机部署）。"""

    def __init__(self, base_dir: Path = _UPLOAD_DIR, url_prefix: str = _URL_PREFIX) -> None:
        self.base_dir = base_dir
        self.url_prefix = url_prefix.rstrip("/")

    def save(self, data: bytes, ext: str) -> dict:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.md5(data).hexdigest()
        key = f"{digest[:8]}_{int(time.time())}{ext}"
        (self.base_dir / key).write_bytes(data)
        return {"key": key, "size": len(data), "md5": digest}

    def url_for(self, key: str, *, signed: bool = True) -> str:
        path = f"{self.url_prefix}/{key}"
        if signed and settings.upload_signed_url_enabled:
            return signed_url(path)
        return path

    def delete(self, key: str) -> None:
        (self.base_dir / key).unlink(missing_ok=True)


class ObjectStoragePlaceholder:
    """对象存储（S3 / OSS）占位实现：接口已定，未接入 SDK 前回退本地。"""

    def __init__(self, backend: str) -> None:
        self.backend = backend
        self._fallback = LocalStorage()

    def save(self, data: bytes, ext: str) -> dict:
        return self._fallback.save(data, ext)

    def url_for(self, key: str, *, signed: bool = True) -> str:
        return self._fallback.url_for(key, signed=signed)

    def delete(self, key: str) -> None:
        self._fallback.delete(key)


_storage: StorageBackend | None = None


def get_storage() -> StorageBackend:
    """按配置返回存储实现（默认 local；s3/oss 暂回退本地）。"""
    global _storage
    if _storage is None:
        backend = (settings.storage_backend or "local").strip().lower()
        _storage = LocalStorage() if backend == "local" else ObjectStoragePlaceholder(backend)
    return _storage


def resign(url: str, ttl_seconds: int | None = None) -> str:
    """把入库的裸路径重新签名为当前有效的访问 URL（B14 P1 修复）。

    背景：入库一律保存**不带签名**的稳定路径（避免过期签名入库），
    因此所有对外返回图片/头像字段的地方都必须动态重新签名，
    否则开启签名校验后前端拿到裸路径会 403（"只写不读"缺陷）。

    规则：
    - 空值 / 非 ``/static/uploads/`` 前缀（外链、第三方 URL）→ 原样返回；
    - 关闭签名校验（``XJT_UPLOAD_SIGNED_URL_ENABLED=false``）→ 原样返回；
    - 先剥离旧 query 再对**完整路径**签名（不可复用 url_for，
      否则会把完整路径再拼一次前缀，得到 /static/uploads//static/uploads/xxx.jpg）。
    """
    if not url or not settings.upload_signed_url_enabled:
        return url
    path = str(url).split("?")[0]
    if not path.startswith(f"{_URL_PREFIX}/"):
        return url
    return signed_url(path, ttl_seconds)
