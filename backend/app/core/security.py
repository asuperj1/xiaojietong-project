"""JWT 工具：签发 / 校验访问令牌。

契约约定见 docs/api.md §0：token 2h，refresh 7d。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import settings


def create_access_token(user_id: int, role: int = 0) -> str:
    """签发访问令牌。"""
    payload = {
        "uid": user_id,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(seconds=settings.jwt_expire_seconds),
        "type": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: int) -> str:
    payload = {
        "uid": user_id,
        "exp": datetime.now(timezone.utc)
        + timedelta(seconds=settings.jwt_refresh_expire_seconds),
        "type": "refresh",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    """校验并解码 token；失败抛 jwt.PyJWTError。"""
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
