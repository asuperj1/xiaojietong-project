"""上传目录静态服务：签名校验 + 安全响应头。

- **B14 签名访问**：`XJT_UPLOAD_SIGNED_URL_ENABLED=true`（默认）时，
  `/static/uploads/*` 必须携带 `?e=<过期时间戳>&s=<HMAC 签名>`，
  否则返回 403 —— 防止静态目录被任意枚举/盗链（签名由上传接口自动附带）。
- **SEC-11 响应头**（纵深防御，即使文件被预览也降级为不可执行）：
  - `X-Content-Type-Options: nosniff` 防内容嗅探；
  - `Content-Security-Policy: sandbox` 使伪装类型不可执行、无同源权限；
  - `Referrer-Policy: no-referrer` 防内网路径外泄；
  - `Cross-Origin-Resource-Policy: same-site`。
"""

from __future__ import annotations

from urllib.parse import parse_qs

from starlette.responses import JSONResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from app.core.config import settings
from app.core.url_sign import verify

_URL_PREFIX = "/static/uploads"


class SafeStaticFiles(StaticFiles):
    """在 StaticFiles 基础上补充签名校验与安全响应头（不覆盖已有同名头）。"""

    SECURITY_HEADERS: dict[str, str] = {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; img-src 'self'; sandbox",
        "Referrer-Policy": "no-referrer",
        "Cross-Origin-Resource-Policy": "same-site",
    }

    async def get_response(self, path: str, scope: Scope) -> Response:
        # B14：签名访问校验（默认开启）
        if settings.upload_signed_url_enabled:
            query = parse_qs(scope.get("query_string", b"").decode("utf-8"))
            expire = (query.get("e") or [None])[0]
            signature = (query.get("s") or [None])[0]
            full_path = f"{_URL_PREFIX}/{path}"
            if not verify(full_path, expire, signature):
                return JSONResponse(
                    status_code=403,
                    content={"code": 2003, "message": "资源链接无效或已过期", "data": {}},
                )

        response = await super().get_response(path, scope)
        for key, value in self.SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response
