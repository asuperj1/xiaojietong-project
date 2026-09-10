#!/usr/bin/env python3
"""极简 HTTP 文件服务（支持 Range 断点续传），用于把 GGUF 模型分发给队友。

为什么不用 `python -m http.server`？
    自带 http.server 不支持 Range 请求，下载一旦中断（大文件很常见）就得从头再来；
    本脚本实现 HTTP 206 Partial Content，可被 IDM / 迅雷多线程下载、支持断点续传。

用法：
    python gguf_server.py E:/models/xjt-3b-f16.gguf --port 8000
    python gguf_server.py E:/models/xjt-3b-f16.gguf --port 8000 --bind 0.0.0.0

特性：
    - 支持 Range（206），支持多线程下载与断点续传
    - 只暴露指定单个文件、不列目录（比 http.server 更安全）
    - 每次传输打印对端 IP、字节数、速度，便于确认队友是否下载完成

作者：成员3（C++ 数据层 / 模型微调 / 数据库）· C5 分发工具
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional, Tuple
from urllib.parse import unquote

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")
CHUNK_SIZE = 256 * 1024


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    """线程化 HTTP 服务：对端断开时不打印冗长 traceback。

    大文件传输中浏览器/下载器主动断开（或校园网抖动）很常见，
    默认实现会向控制台输出整段堆栈，干扰进度查看。
    """

    daemon_threads = True        # Ctrl+C 后不残留工作线程
    allow_reuse_address = True   # 避免端口处于 TIME_WAIT 时无法重启

    def handle_error(self, request, client_address):  # noqa: D102
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError)):
            print(f"  ! 对端断开：{client_address[0]}（{type(exc).__name__}）", flush=True)
            return
        super().handle_error(request, client_address)


class SingleFileHandler(BaseHTTPRequestHandler):
    """只服务单个文件的 HTTP 处理器（支持 Range）。"""

    # 由 main() 注入
    file_path: str = ""
    file_size: int = 0
    file_name: str = ""

    server_version = "XJTGgufServer/1.0"
    protocol_version = "HTTP/1.1"

    # ---------- 内部工具 ----------

    def _parse_range(self, header: Optional[str]) -> Optional[Tuple[int, int]]:
        """解析 Range 头，返回 (start, end)；不合法或缺失返回 None。"""
        if not header:
            return None
        m = RANGE_RE.match(header.strip())
        if not m:
            return None
        start_s, end_s = m.group(1), m.group(2)
        if start_s == "":
            # 后缀范围 bytes=-N（取最后 N 字节）
            n = int(end_s or 0)
            if n <= 0:
                return None
            start, end = max(0, self.file_size - n), self.file_size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else self.file_size - 1
        if start > end or start >= self.file_size:
            return None
        return start, min(end, self.file_size - 1)

    def _common_headers(self, length: int, status: int,
                        start: int = 0, end: int = 0) -> None:
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Disposition",
                         f'attachment; filename="{self.file_name}"')
        if status == 206:
            self.send_header("Content-Range",
                             f"bytes {start}-{end}/{self.file_size}")

    # ---------- HTTP 方法 ----------

    def do_HEAD(self) -> None:  # noqa: N802
        self.send_response(200)
        self._common_headers(self.file_size, 200)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        req_path = unquote(self.path.split("?")[0])
        if req_path not in ("/", f"/{self.file_name}"):
            self.send_error(404, "Not Found")
            return

        rng = self._parse_range(self.headers.get("Range"))
        if rng is None:
            start, end, status = 0, self.file_size - 1, 200
        else:
            start, end, status = rng[0], rng[1], 206

        length = end - start + 1
        self.send_response(status)
        self._common_headers(length, status, start, end)
        self.end_headers()

        t0, sent = time.time(), 0
        try:
            with open(self.file_path, "rb") as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = fh.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
                    sent += len(chunk)
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            print(f"  ! 对端中断（已传 {sent / 1048576:.1f} MiB）", flush=True)

        cost = max(time.time() - t0, 1e-6)
        speed = sent / cost / 1048576
        print(
            f"[{time.strftime('%H:%M:%S')}] {self.client_address[0]} "
            f"传入 {sent / 1048576:.1f} MiB  ({start}-{end}) "
            f"用时 {cost:.1f}s  速度 {speed:.1f} MiB/s",
            flush=True,
        )

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        """屏蔽默认访问日志，改用 do_GET 中的自定义输出。"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="单文件 HTTP 服务（支持断点续传），用于把 GGUF 分发给队友")
    parser.add_argument("file", help="要共享的文件路径")
    parser.add_argument("--port", type=int, default=8000, help="监听端口（默认 8000）")
    parser.add_argument("--bind", default="0.0.0.0", help="绑定地址（默认 0.0.0.0）")
    args = parser.parse_args()

    path = os.path.abspath(args.file)
    if not os.path.isfile(path):
        print(f"找不到文件：{path}", file=sys.stderr)
        return 1

    SingleFileHandler.file_path = path
    SingleFileHandler.file_size = os.path.getsize(path)
    SingleFileHandler.file_name = os.path.basename(path)

    size_gib = SingleFileHandler.file_size / 1073741824
    print(f"已共享：{SingleFileHandler.file_name}（{size_gib:.2f} GiB）")
    print(f"监听：{args.bind}:{args.port}（支持 Range 断点续传）")
    print("按 Ctrl+C 停止服务\n")

    httpd = QuietThreadingHTTPServer((args.bind, args.port), SingleFileHandler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止服务")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
