"""测试用：把 ASGI app 真起成一个 HTTP 服务（随机空闲端口）。

**为什么必须真起服务、而不是只用 `TestClient`**：契约类断言（字段名 / 表单字段 /
响应键 / 代理绕过）只有在真发一次 HTTP 时才有意义 —— `TestClient` 走 ASGI 直连，
不经过 `httpx` 的传输层，`trust_env` 之类的行为根本不会被触发，
用例会"绿得没有意义"。

（`tests/test_asr_server.py`（PR #115，**尚未合并**）里有一份等价的内联实现 `_live_server`；
#115 合并后统一到本模块，此处先避免改动未合并分支上的文件。）
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


@contextlib.contextmanager
def free_port():
    """占用一个**没有任何服务在监听**的端口（用于"服务没起"的用例）。

    用法：`with free_port() as base_url:` —— 退出时才释放。

    为什么不直接返回一个 int：原实现（bind 拿到端口后立刻 close）有 TOCTOU 窗口 ——
    端口被释放到用例真去连之间，别的进程（或并行跑的另一个用例）可能抢走它，
    于是用例连上了别人的服务（拿到 404 → 退化成 `ExtractFailure`）而失败。
    这里让 socket **保持 bind 但不 listen**：连接会得到 RST（等价于"服务没起"），
    而端口在占用期间不会被抢走。
    """
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        yield f"http://127.0.0.1:{s.getsockname()[1]}"
