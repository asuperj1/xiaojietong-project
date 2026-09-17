"""认证：微信登录 / 刷新 / 登出。

契约：docs/api.md §2
"""

from __future__ import annotations

import logging

import httpx
import jwt
from fastapi import APIRouter, Header
from pydantic import BaseModel

from app.core.config import settings
from app.core.response import BizError, err_param, err_server, err_token, ok
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    token_version_of,
)
from app.db import cpp_bridge

router = APIRouter(prefix="/auth", tags=["auth"])

logger = logging.getLogger(__name__)


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
    """code 换 openid。

    未配置微信凭据时退化为模拟 openid —— 该行为**仅允许开发态**：
    生产环境（XJT_ENV=prod）已在 `Settings._validate_security` 阶段拒绝启动，
    这里再做一次请求级兜底，避免配置被绕过（审计 SEC-02）。
    """
    if settings.wx_login_mock:
        if settings.is_prod:
            raise err_server("微信登录未配置，服务拒绝该请求")
        logger.warning(
            "微信登录降级为 mock（openid=oXJT_DEV_<code>），仅限本地开发；"
            "生产环境请配置 XJT_WX_APPID / XJT_WX_SECRET"
        )
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
    tv = int(user.get("token_version", 0) or 0)   # C22：把当前版本写进 token
    return ok(
        {
            "token": create_access_token(uid, int(user.get("role", 0)), tv),
            "refresh_token": create_refresh_token(uid, tv),
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
    tv = int(user.get("token_version", 0) or 0)
    # C22：刷新也不能绕过登出 —— 否则拿旧 refresh_token 就能换回新 access_token，
    # 让「登出使 token 失效」形同虚设。
    if token_version_of(payload) != tv:
        raise err_token()
    return ok(
        {
            "token": create_access_token(uid, int(user.get("role", 0)), tv),
            "refresh_token": create_refresh_token(uid, tv),
        }
    )


@router.post("/logout")
def logout(authorization: str = Header(default="")):
    """登出。

    C22 起不再是空操作：把 `user.token_version` +1，使该用户**所有已签发的
    access / refresh token 立即失效**（deps 与 /auth/refresh 都会比对 tv）。

    设计取舍：**不强制鉴权**（拿不到/无效 token 也返回成功）——
      · 登出必须是幂等的，客户端丢 token 后再调一次不能报错；
      · 保持与旧版兼容，不因新增鉴权而打断现有前端调用。

    但**自增本身对 tv 做闸门**：只有「token 里的 `tv` 与库内当前值相等」时才 +1，
    即只对**仍然有效**的 token 生效。否则同一个已经失效的 token 可以无限次调用：

      · 「幂等」就只剩 HTTP 状态码幂等，副作用并不幂等（每次多两趟 DB）；
      · 拿到该用户**任意一个历史 token** 的一方可以持续把账号顶下线 ——
        token 泄露后的持久 DoS。

    `token_version` 的语义是「**没有产生新版本号**」：不带头、token 损坏、
    token 已失效、用户不存在，一律回 `null`。
    """
    # 响应形状固定为 `{ok, token_version}`（api.md 契约），前端可无条件读这个键。
    data: dict = {"ok": True, "token_version": None}
    if authorization.startswith("Bearer "):
        try:
            payload = decode_token(authorization[7:])
        except jwt.PyJWTError:
            return ok(data)      # token 坏了也算登出成功
        uid = int(payload.get("uid", 0) or 0)
        if uid > 0:
            user = cpp_bridge.user_dao().find_by_id(uid)
            current = int((user or {}).get("token_version", 0) or 0)
            if user is not None and token_version_of(payload) == current:
                data["token_version"] = int(cpp_bridge.user_dao().bump_token_version(uid))
    return ok(data)
