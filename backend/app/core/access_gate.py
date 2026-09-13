"""内部联调访问闸门（简易接口鉴权）。

为什么需要
----------
本阶段后端以「公网 IP + 8080」直接暴露（不使用域名/HTTPS/反向代理），
运维层面只能靠安全组收紧来源；一旦来源放宽，**外网扫描器**就会打到测试接口上。
本模块在业务 JWT 鉴权**之前**再加一道团队共享口令，把陌生人挡在门外。

定位（重要）
------------
它**不是**权限系统，也不替代 `Authorization: Bearer` 业务鉴权；
它只是一道「整站门禁」，用于「备案未完成、仅 4 人内部联调」这一阶段。
备案通过、正式对外后应移除或替换为正式的接入网关方案。

开关（XJT_ACCESS_TOKEN）
------------------------
- **非空** → 启用闸门（服务器上必须启用）
- **空**   → 关闭闸门（本机开发默认，行为与之前完全一致，零回归风险）

放行方式（二选一）
------------------
1. 请求头 ``X-Access-Token: <token>``   ← 推荐（不进 URL、不进访问日志）
2. 查询串 ``?access_token=<token>``      ← 便于浏览器临时调试（会进日志，仅临时用）

豁免路径（XJT_ACCESS_GATE_EXEMPT）
----------------------------------
逗号分隔，默认 ``/api/v1/health``（**仅基础存活探针**）。
⚠️ 刻意**不**豁免 ``/api/v1/health/detail`` 与 ``/health/selfcheck``：
它们会回显数据库/连接池/Ollama 状态，属信息泄露，陌生人不应看到。
**不要**把业务接口加进豁免名单。

实现说明
--------
使用**纯 ASGI 中间件**而非 ``BaseHTTPMiddleware``：
后者会包一层 anyio 内存对象流，对 SSE（``/chat/send`` 逐字输出）有缓冲风险。
"""

from __future__ import annotations

import hmac
import json
import logging
from typing import Any, Iterable

logger = logging.getLogger("app.access_gate")

_HEADER_NAME = b"x-access-token"
_QUERY_KEY = "access_token"
_DEFAULT_EXEMPT = ("/api/v1/health",)


def parse_exempt(raw: str | None) -> tuple[str, ...]:
    """把逗号分隔的豁免路径串解析为规范化元组。

    支持两种写法：
      ``/api/v1/health``    精确匹配（推荐，默认）
      ``/api/v1/health*``   前缀匹配（尾部星号，**慎用**）
    """
    if not raw:
        return _DEFAULT_EXEMPT
    parts: list[str] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if not item.startswith("/"):
            item = "/" + item
        item = item.rstrip("/") or "/"
        parts.append(item)
    return tuple(parts) or _DEFAULT_EXEMPT


def _extract_token(scope: dict[str, Any]) -> str:
    """从请求头或查询串中取出调用方提供的令牌（取不到返回空串）。"""
    for name, value in scope.get("headers") or ():
        if name.lower() == _HEADER_NAME:
            try:
                return value.decode("latin-1").strip()
            except Exception:  # pragma: no cover - 解码异常按无令牌处理
                return ""

    qs = scope.get("query_string") or b""
    if qs:
        try:
            from urllib.parse import parse_qs

            values = parse_qs(qs.decode("latin-1")).get(_QUERY_KEY)
            if values:
                return values[0].strip()
        except Exception:  # pragma: no cover
            return ""
    return ""


def _is_exempt(path: str, exempt: Iterable[str]) -> bool:
    """判断路径是否豁免。

    ⚠️ **默认精确匹配**（不自动做前缀匹配）：
    否则豁免 ``/api/v1/health`` 会连带放行 ``/api/v1/health/detail``，
    而后者会回显数据库/连接池/Ollama 状态，属信息泄露。
    确实需要前缀时请显式写成 ``/api/v1/health*``。
    """
    norm = path.rstrip("/") or "/"
    for item in exempt:
        if item.endswith("*"):
            if norm.startswith(item[:-1].rstrip("/")):
                return True
        elif norm == item:
            return True
    return False


class AccessGateMiddleware:
    """整站门禁中间件（``XJT_ACCESS_TOKEN`` 为空时完全透明）。"""

    def __init__(self, app, token: str = "", exempt: Iterable[str] = _DEFAULT_EXEMPT):
        self.app = app
        self.token = (token or "").strip()
        self.exempt = tuple(exempt)
        self.enabled = bool(self.token)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not self.enabled:
            return await self.app(scope, receive, send)

        # CORS 预检不带自定义头，必须放行，否则浏览器侧报错难以定位
        if (scope.get("method") or "GET").upper() == "OPTIONS":
            return await self.app(scope, receive, send)

        path = scope.get("path") or "/"
        if _is_exempt(path, self.exempt):
            return await self.app(scope, receive, send)

        provided = _extract_token(scope)
        # 常量时间比较，避免通过响应耗时逐字节猜测令牌
        if provided and hmac.compare_digest(provided, self.token):
            return await self.app(scope, receive, send)

        client = scope.get("client") or ("?", 0)
        logger.warning(
            "访问闸门拒绝请求：%s %s 来自 %s:%s（%s令牌）",
            scope.get("method"), path, client[0], client[1],
            "提供了错误" if provided else "未提供",
        )
        await self._deny(send)

    @staticmethod
    async def _deny(send) -> None:
        """按项目统一契约返回 403：{code, message, data}。"""
        body = json.dumps(
            {
                "code": 2003,
                "message": "访问被拒绝：缺少或错误的访问令牌（请在请求头携带 X-Access-Token）",
                "data": {},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def install(app, access_token: str, exempt_raw: str | None = None) -> bool:
    """按配置决定是否挂载闸门；返回是否启用。

    在 ``app.add_middleware`` 队列中**最后注册**，使其位于最外层（最先执行），
    这样陌生请求在进入限流与业务逻辑之前就被拒绝，不消耗限流配额。
    """
    token = (access_token or "").strip()
    # 用 uvicorn 的 logger 输出开关状态：本项目未另行配置 logging handler，
    # 自定义 logger 的 INFO 会被丢弃，运维就看不到闸门到底开没开。
    boot = logging.getLogger("uvicorn")
    if not token:
        boot.warning(
            "⚠️  访问闸门未启用（XJT_ACCESS_TOKEN 为空）。"
            "公网暴露场景下这会让任何人访问测试接口 —— 服务器部署必须设置该项。"
        )
        return False

    exempt = parse_exempt(exempt_raw)
    app.add_middleware(AccessGateMiddleware, token=token, exempt=exempt)
    boot.info("✅ 访问闸门已启用（豁免路径：%s）", ",".join(exempt))
    return True
