"""认证：微信登录 / 刷新 / 登出。

契约：docs/api.md §2
"""

from __future__ import annotations

import httpx
import jwt
from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import settings
from app.core.response import BizError, err_param, err_server, err_token, ok
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.db import cpp_bridge

router = APIRouter(prefix="/auth", tags=["auth"])


class WechatLoginIn(BaseModel):
    code: str


class RefreshIn(BaseModel):
    refresh_token: str


def _user_view(u: dict) -> dict:
    """user 表行 → 前端视图（数值字段转 int）。"""
    return {
        "id": int(u.get("id", 0)),
        "openid": u.get("openid", ""),
        "nickname": u.get("nickname", ""),
        "avatar": u.get("avatar", ""),
        "role": int(u.get("role", 0)),
        "student_no": u.get("student_no", ""),
        "major": u.get("major", ""),
        "grade": u.get("grade", ""),
        "campus": u.get("campus", ""),
    }


async def _code_to_openid(code: str) -> str:
    """code 换 openid。未配置微信凭据时使用模拟 openid（开发模式）。"""
    if not settings.wx_appid or not settings.wx_secret:
        return "oXJT_DEV_" + code
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            "https://api.weixin.qq.com/sns/jscode2session",
            params={
                "appid": settings.wx_appid,
                "secret": settings.wx_secret,
                "js_code": code,
                "grant_type": "authorization_code",
            },
        )
        data = resp.json()
    if "openid" not in data:
        raise BizError(2001, f"微信登录失败: {data.get('errmsg', '')}")
    return data["openid"]


@router.post("/wechat-login")
async def wechat_login(body: WechatLoginIn):
    if not body.code:
        raise err_param("缺少 code")
    openid = await _code_to_openid(body.code)

    dao = cpp_bridge.user_dao()
    user = dao.find_by_openid(openid)
    is_new = False
    if user is None:
        uid = dao.create(openid, "微信用户")
        if uid <= 0:
            raise err_server("创建用户失败")
        user = dao.find_by_id(uid)
        is_new = True

    uid = int(user["id"])
    return ok(
        {
            "token": create_access_token(uid, int(user.get("role", 0))),
            "refresh_token": create_refresh_token(uid),
            "user": _user_view(user),
            "is_new": is_new,
        }
    )


@router.post("/refresh")
def refresh(body: RefreshIn):
    try:
        payload = decode_token(body.refresh_token)
    except jwt.PyJWTError:
        raise err_token()
    if payload.get("type") != "refresh":
        raise err_token()

    uid = int(payload.get("uid", 0))
    user = cpp_bridge.user_dao().find_by_id(uid)
    if user is None:
        raise err_token()
    return ok(
        {
            "token": create_access_token(uid, int(user.get("role", 0))),
            "refresh_token": create_refresh_token(uid),
        }
    )


@router.post("/logout")
def logout():
    # 无状态 JWT：前端丢弃 token 即可
    return ok()
