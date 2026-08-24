"""统一响应体与业务异常（契约见 docs/api.md §0）。

响应：{ "code": 0, "message": "ok", "data": ... }
错误码：100x 参数 / 200x 认证 / 300x 业务冲突 / 500x 服务端
"""

from __future__ import annotations

from typing import Any


class BizError(Exception):
    """业务异常：携带 code 与给用户的 message。"""

    def __init__(self, code: int, message: str, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


# 常用错误码快捷构造
def err_param(message: str = "参数错误") -> BizError:
    return BizError(1001, message)


def err_auth(message: str = "未登录") -> BizError:
    return BizError(2001, message, http_status=401)


def err_token(message: str = "token 无效或过期") -> BizError:
    return BizError(2002, message, http_status=401)


def err_forbidden(message: str = "无权限") -> BizError:
    return BizError(2003, message, http_status=403)


def err_biz(message: str = "业务冲突") -> BizError:
    return BizError(3001, message)


def err_server(message: str = "服务端错误") -> BizError:
    return BizError(5001, message, http_status=500)


def ok(data: Any = None) -> dict:
    """成功响应体。"""
    return {"code": 0, "message": "ok", "data": data if data is not None else {}}


def paged(items: list, total: int, page: int, size: int) -> dict:
    """分页 data 结构。"""
    return {"items": items, "total": total, "page": page, "size": size}
