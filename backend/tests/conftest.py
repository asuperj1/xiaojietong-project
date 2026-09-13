r"""pytest 公共夹具（B12 工程化基线）。

运行前置（本机示例，需真实 MySQL + jt_db 扩展 + 已导入建库脚本）：
    cd backend
    $env:XJT_DB_PORT="3306"; $env:XJT_DB_PASSWORD="***"
    & ".\.venv\Scripts\python.exe" -m pytest tests -q

说明：本套用例为 **真实依赖集成测试**（进程内 TestClient 直连真实数据库），
环境不可用时自动 skip 而非 fail，保证 `pytest -q` 在任何人机器上都能跑出结论。
（docstring 使用 raw 字符串，避免 Windows 路径反斜杠触发 SyntaxWarning）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 让 `import app` 可用（pytest 只把 tests/ 加入 sys.path）
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import pytest  # noqa: E402

# 环境变量必须在 import app 之前就位（settings 为模块级单例）
os.environ.setdefault("XJT_DB_HOST", "127.0.0.1")
os.environ.setdefault("XJT_DB_PORT", "3306")
os.environ.setdefault("XJT_DB_NAME", "xiaojietong")

from fastapi.testclient import TestClient  # noqa: E402

from app.db import cpp_bridge  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client() -> TestClient:
    """TestClient；数据库/扩展/凭据不可用时整体 skip（而非 fail）。"""
    if not cpp_bridge.available():
        pytest.skip("jt_db C++ 扩展不可用：请先按 db/cpp_driver/README.md 构建")
    if not os.environ.get("XJT_DB_PASSWORD"):
        pytest.skip("未设置 XJT_DB_PASSWORD 环境变量，跳过集成用例")
    with TestClient(app) as c:
        data = c.get("/api/v1/health").json()
        if data.get("db") != "ok":
            pytest.skip(f"数据库未就绪：{data}")
        yield c


def _login(client: TestClient, code: str) -> dict:
    resp = client.post("/api/v1/auth/wechat-login", json={"code": code})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


@pytest.fixture(scope="session")
def user_a(client: TestClient) -> dict:
    """账号 A（code=test1）的登录数据（token + user）。"""
    return _login(client, "test1")


@pytest.fixture(scope="session")
def user_b(client: TestClient) -> dict:
    """账号 B（code=test2）的登录数据。"""
    return _login(client, "test2")


@pytest.fixture(scope="session")
def hdr_a(user_a: dict) -> dict:
    return {"Authorization": f"Bearer {user_a['token']}"}


@pytest.fixture(scope="session")
def hdr_b(user_b: dict) -> dict:
    return {"Authorization": f"Bearer {user_b['token']}"}
