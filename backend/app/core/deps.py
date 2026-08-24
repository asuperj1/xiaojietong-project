"""FastAPI 依赖注入：当前用户 / 管理员校验。"""

from __future__ import annotations

from typing import Optional

import jwt
from fastapi import Depends, Header

from app.core.response import err_auth, err_forbidden, err_token
from app.core.security import decode_token
from app.db import cpp_bridge


def get_current_user(authorization: str = Header(default="")) -> Optional[dict]:
    """解析 Bearer token，返回当前用户 dict（user 表行）。"""
    if not authorization.startswith("Bearer "):
        raise err_auth()
    token = authorization[7:]
    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        raise err_token()
    if payload.get("type") != "access":
        raise err_token()

    user = cpp_bridge.user_dao().find_by_id(payload.get("uid", 0))
    if user is None:
        raise err_forbidden("用户不存在")
    if user.get("status") == "1":
        raise err_forbidden("账号已禁用")
    return user


def get_current_admin(user: dict = Depends(get_current_user)) -> dict:
    """管理员校验：role >= 1。"""
    if int(user.get("role", 0)) < 1:
        raise err_forbidden("需要管理员权限")
    return user
