"""上传目录静态服务：附加安全响应头。

对应审计 **SEC-11**。`/static/uploads` 直接对外提供用户上传的文件，
即使上传侧已做魔数白名单（见 `app/routers/upload.py`），仍建议加一层响应头
纵深防御，防止以下场景：

- **内容嗅探**：浏览器忽略声明的 `Content-Type`，把 `image/png` 当 HTML 解析
  → 加 `X-Content-Type-Options: nosniff`；
- **伪装类型执行脚本**：若某个文件被解析为 HTML/SVG，其中内联脚本可窃取
  站点凭据或跳转 → 加 `Content-Security-Policy: sandbox` 将其降级为不可执行、
  无同源权限的沙箱文档；
- **Referer 外泄**：图片被第三方站点引用时泄漏内网路径 → 加 `Referrer-Policy`。

注意：`sandbox` 指令会让 SVG 无法加载外部资源，若将来要支持 SVG 上传，
需重新评估该策略。
"""
from __future__ import annotations

from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from starlette.responses import Response


class SafeStaticFiles(StaticFiles):
    """在 StaticFiles 基础上补充安全响应头（不覆盖已有的同名头）。"""

    SECURITY_HEADERS: dict[str, str] = {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; img-src 'self'; sandbox",
        "Referrer-Policy": "no-referrer",
        "Cross-Origin-Resource-Policy": "same-site",
    }

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        for key, value in self.SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response
