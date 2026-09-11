"""接口限流：单进程内存滑动窗口。

对应审计 **SEC-10**：原全站无任何限流，`/auth/wechat-login` 可被脚本无限爆破
（配合 SEC-02 的 mock 降级即可批量造号），写接口也可被恶意刷量；同时这也是
缓存/DB 压力放大的一条路径。

设计要点：
- **滑动窗口**（`deque` 时间戳）而非固定窗口，避免窗口边界双倍放量；
- 计数键 = `客户端 IP + 规则桶`，规则按「方法 + 路径前缀」匹配，**命中首条即生效**；
  未命中任何规则则计入全局兜底桶；
- 超限返回 **HTTP 429** + 统一业务码 `1010`，与 `app.core.response` 的报文结构一致；
- 惰性 GC：每次请求顺带检查，超过 60s 才真正扫描一次，避免内存无界增长。

局限（部署前必读）：
- **单进程内存**：多 worker / 多机部署时各进程独立计数，不是全局精确限流。
  需要全局限流请接 Redis（`docs/architecture.md` 的后续规划）。
- 位于反向代理之后时依赖 `X-Forwarded-For` 取真实客户端 IP；若代理未正确透传，
  所有请求会被计入代理 IP 的同一个桶，可能误伤。

作者：成员3 · 审计修复
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# 业务码：与 response.py 的 1xxx（通用）区间保持一致
CODE_RATE_LIMITED = 1010


@dataclass(frozen=True)
class Rule:
    """一条限流规则。`method="*"` 表示不限方法；`window` 单位为秒。"""

    method: str
    path_prefix: str
    limit: int
    window: float = 60.0


class SlidingWindowLimiter:
    """按 key 维护滑动窗口内的命中时间戳。"""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._last_gc = time.monotonic()

    def allow(self, key: str, limit: int, window: float, now: float) -> bool:
        """记一次命中；未超限返回 True。"""
        dq = self._hits[key]
        cutoff = now - window
        while dq and dq[0] <= cutoff:
            dq.popleft()
        if len(dq) >= limit:
            return False
        dq.append(now)
        return True

    def gc(self, now: float, idle: float = 3600.0) -> None:
        """清理久未活动的 key，防止内存膨胀（最多每 60s 扫描一次）。"""
        if now - self._last_gc < 60.0:
            return
        self._last_gc = now
        cutoff = now - idle
        for key in [k for k, dq in self._hits.items() if not dq or dq[-1] <= cutoff]:
            self._hits.pop(key, None)

    @property
    def tracked_keys(self) -> int:
        """当前跟踪的计数器数量（供 /health 观测）。"""
        return len(self._hits)


def client_ip(request: Request) -> str:
    """取客户端 IP，优先信任反向代理透传的 X-Forwarded-For 首段。"""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def default_rules(login_per_minute: int) -> list[Rule]:
    """内置规则集：越靠前越优先，登录类接口单独收紧。"""
    return [
        Rule("POST", "/api/v1/auth/wechat-login", login_per_minute),
        Rule("POST", "/api/v1/auth/refresh", login_per_minute * 3),
        Rule("POST", "/api/v1/upload", 30),
        Rule("POST", "/api/v1/chat/send", 30),
        Rule("POST", "/api/v1/agent/tasks", 20),
    ]


class RateLimitMiddleware(BaseHTTPMiddleware):
    """ASGI 限流中间件。"""

    def __init__(
        self,
        app,
        rules: list[Rule],
        global_limit: int = 300,
        global_window: float = 60.0,
        enabled: bool = True,
    ) -> None:
        super().__init__(app)
        self.rules = rules
        self.global_limit = global_limit
        self.global_window = global_window
        self.enabled = enabled
        self.limiter = SlidingWindowLimiter()

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # 预检请求不计入限流，否则浏览器跨域会被误伤
        if not self.enabled or request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        ip = client_ip(request)
        now = time.monotonic()

        for rule in self.rules:
            if not path.startswith(rule.path_prefix):
                continue
            if rule.method != "*" and rule.method != request.method:
                continue
            key = f"{ip}|{rule.method}:{rule.path_prefix}"
            if not self.limiter.allow(key, rule.limit, rule.window, now):
                return _reject(rule)
            break
        else:
            key = f"{ip}|__global__"
            if not self.limiter.allow(key, self.global_limit, self.global_window, now):
                return _reject(None)

        self.limiter.gc(now)
        return await call_next(request)


def _reject(rule: Rule | None) -> JSONResponse:
    """构造 429 响应（沿用统一报文结构）。"""
    if rule is not None:
        retry_after = int(rule.window)
        message = f"操作过于频繁，请 {retry_after} 秒后重试"
    else:
        retry_after = 60
        message = "请求过于频繁，请稍后重试"
    return JSONResponse(
        status_code=429,
        content={"code": CODE_RATE_LIMITED, "message": message, "data": {}},
        headers={"Retry-After": str(retry_after)},
    )
