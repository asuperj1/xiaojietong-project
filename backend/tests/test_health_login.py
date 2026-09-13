"""健康检查与登录链路（B12 用例）。

覆盖：/health 基础探针、微信登录、用户信息读取。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_health_basic(client):
    data = client.get("/api/v1/health").json()
    assert data["status"] == "ok"
    assert data["db"] == "ok"
    assert data["cpp_ext"] is True


def test_wechat_login_and_profile(client, user_a, hdr_a):
    assert user_a["token"], "登录应返回 token"
    resp = client.get("/api/v1/user/me", headers=hdr_a).json()
    assert resp["code"] == 0
    assert resp["data"]["id"] == user_a["user"]["id"]
