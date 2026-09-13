"""上传资源 URL 签名（B14）：HMAC + 过期时间，防未授权访问静态目录。

- 生成：``signed_url(path)`` → ``/static/uploads/xxx.jpg?e=<过期时间戳>&s=<签名>``；
- 校验：``verify(path, e, s)``，使用 ``hmac.compare_digest`` 防时序攻击；
- 密钥：优先 ``XJT_UPLOAD_URL_SECRET``，留空则从 ``XJT_JWT_SECRET`` 派生
  （本地开发零配置，生产建议独立配置）。
"""

from __future__ import annotations

import hashlib
import hmac
import time

from app.core.config import settings


def _secret() -> bytes:
    base = settings.upload_url_secret or settings.jwt_secret
    return base.encode("utf-8")


def _signature(path: str, expire: int) -> str:
    return hmac.new(
        _secret(), f"{path}|{expire}".encode("utf-8"), hashlib.sha256
    ).hexdigest()[:32]


def sign(path: str, ttl_seconds: int | None = None) -> tuple[int, str]:
    """生成 (过期时间戳, 签名)。"""
    ttl = int(ttl_seconds if ttl_seconds is not None else settings.upload_url_ttl_seconds)
    expire = int(time.time()) + max(60, ttl)
    return expire, _signature(path, expire)


def signed_url(path: str, ttl_seconds: int | None = None) -> str:
    """带签名的访问 URL。"""
    expire, sig = sign(path, ttl_seconds)
    return f"{path}?e={expire}&s={sig}"


def verify(path: str, expire: str | None, signature: str | None) -> bool:
    """校验签名与有效期；任一不满足返回 False。"""
    if not expire or not signature:
        return False
    try:
        exp = int(expire)
    except (TypeError, ValueError):
        return False
    if exp < int(time.time()):
        return False
    return hmac.compare_digest(_signature(path, exp), signature)
