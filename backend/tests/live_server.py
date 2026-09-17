"""测试用：把 ASGI app 真起成一个 HTTP 服务（随机空闲端口）。

**为什么必须真起服务、而不是只用 `TestClient`**：契约类断言（字段名 / 表单字段 /
响应键 / 代理绕过）只有在真发一次 HTTP 时才有意义 —— `TestClient` 走 ASGI 直连，
不经过 `httpx` 的传输层，`trust_env` 之类的行为根本不会被触发，
用例会"绿得没有意义"。

（`tests/test_asr_server.py` 里有一份等价的内联实现，随 PR #115 提交；
待其合并后可统一到本模块，此处先避免改动未合并分支上的文件。）
"""
from __future__ import annotations

import contextlib
import socket
import threading
import time


@contextlib.contextmanager
def live_server(app):
    """`with live_server(app) as base_url:` 起服务，退出时干净关掉。"""
    import uvicorn

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]           # 端口 0 = 让内核挑一个空闲端口

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("uvicorn 未能在 20s 内启动")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def free_port() -> int:
    """拿一个刚被释放的端口（用于"服务没起"的用例：连它必然失败）。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
