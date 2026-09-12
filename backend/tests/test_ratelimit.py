"""接口限流中间件（B12 用例②：审计 SEC-10）。

采用**隔离实例**测试（自建小 app + 中间件），不干扰全局 app 与其他用例。
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.ratelimit import RateLimitMiddleware, default_rules


def _build_app(global_limit: int) -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    def ping():  # noqa: ANN202
        return {"ok": True}

    app.add_middleware(
        RateLimitMiddleware,
        rules=default_rules(login_per_minute=2),
        global_limit=global_limit,
        enabled=True,
    )
    return app


def test_global_limit_returns_429():
    """全局兜底桶：超过上限返回 429，且放行次数正确。"""
    client = TestClient(_build_app(global_limit=3))
    codes = [client.get("/ping").status_code for _ in range(6)]
    assert codes.count(200) == 3, codes
    assert 429 in codes, codes


def test_route_rule_returns_429():
    """路由规则桶：登录接口单独收紧（2 次/分钟），超限 429。"""
    client = TestClient(_build_app(global_limit=1000))
    codes = [
        client.post("/api/v1/auth/wechat-login", json={"code": "probe"}).status_code
        for _ in range(5)
    ]
    assert 429 in codes, codes
