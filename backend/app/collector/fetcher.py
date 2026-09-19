"""HTTP 抓取（B27）—— stdlib `urllib`，客户端可注入。

刻意不用 httpx/requests：采集器的抓取是"取一次字符串"这种最简单的事，
而 `urllib` 足够；更关键的是**可注入** —— 单测拿一个假 fetcher 就能把整条
流水线跑完，不需要起 http 服务、也不需要网络。

合规不在这里做：robots 与限速统一由 `FetchGuard`（B26）在**调用前**处理，
本模块只管"发出去、拿回来"，出网失败一律抛 `FetchError` 让上层记进日志。
"""
from __future__ import annotations

import gzip
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

from .robots import DEFAULT_USER_AGENT

#: 列表页/详情页的读取上限，防止异常站点把内存吃满
MAX_BYTES = 2 * 1024 * 1024


class FetchError(RuntimeError):
    """网络层失败（连不上 / 超时 / HTTP 错误码）。"""


@dataclass
class FetchResponse:
    url: str
    status: int
    text: str
    final_url: str = ""

    def __len__(self) -> int:      # 方便 guard.after(size=len(resp))
        return len(self.text or "")


def _default_opener(url: str, timeout: float, user_agent: str) -> tuple[int, str, str]:
    """返回 (状态码, 正文, 最终 URL)。非 2xx 抛 FetchError。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 目标来自 C21 配置
            raw = resp.read(MAX_BYTES)
            if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                try:
                    raw = gzip.decompress(raw)
                except OSError:
                    pass                      # 声明了 gzip 但内容不是，按原文处理
            charset = resp.headers.get_content_charset() or "utf-8"
            try:
                text = raw.decode(charset, "replace")
            except LookupError:               # 站点写了不认识的 charset
                text = raw.decode("utf-8", "replace")
            return int(getattr(resp, "status", 200)), text, str(resp.geturl())
    except urllib.error.HTTPError as exc:
        raise FetchError(f"HTTP {exc.code}") from exc
    except Exception as exc:  # noqa: BLE001 - 超时/DNS/TLS 都归为抓取失败
        raise FetchError(f"{type(exc).__name__}: {exc}") from exc


class HttpFetcher:
    """极简 HTTP 客户端（同步）。`opener` 可注入，单测无需网络。"""

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        user_agent: str = DEFAULT_USER_AGENT,
        opener: Optional[Callable[[str, float, str], tuple[int, str, str]]] = None,
    ) -> None:
        self.timeout = float(timeout)
        self.user_agent = user_agent
        self._opener = opener or _default_opener

    def get(self, url: str) -> FetchResponse:
        status, text, final_url = self._opener(url, self.timeout, self.user_agent)
        return FetchResponse(url=url, status=status, text=text, final_url=final_url or url)
