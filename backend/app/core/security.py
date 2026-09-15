"""JWT 工具：签发 / 校验访问令牌。

契约约定见 docs/api.md §0：token 2h，refresh 7d。

**C22 · token 版本号（`tv`）**：access / refresh 都带 `tv` = 签发时用户的
`user.token_version`。`core/deps.get_current_user` 与 `POST /auth/refresh`
都会把它和库里的值比对，不一致即拒绝。
❗**向后兼容**：老 token 里根本没有 `tv`，一律按 `0` 处理；
而 `token_version` 列的默认值也是 `0` ⇒ 上线不会把已登录用户踢下线。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import settings


def create_access_token(user_id: int, role: int = 0, token_version: int = 0) -> str:
    """签发访问令牌（`tv` 为该用户当前 token 版本号）。"""
    payload = {
        "uid": user_id,
        "role": role,
        "tv": int(token_version),
        "exp": datetime.now(timezone.utc) + timedelta(seconds=settings.jwt_expire_seconds),
        "type": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: int, token_version: int = 0) -> str:
    payload = {
        "uid": user_id,
        "tv": int(token_version),
        "exp": datetime.now(timezone.utc)
        + timedelta(seconds=settings.jwt_refresh_expire_seconds),
        "type": "refresh",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def token_version_of(payload: dict) -> int:
    """从 token payload 取 `tv`：缺失/非法一律当 `0`（向后兼容老 token）。"""
    try:
        return int(payload.get("tv", 0))
    except (TypeError, ValueError):
        return 0


def decode_token(token: str) -> dict:
    """校验并解码 token；失败抛 jwt.PyJWTError。"""
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
