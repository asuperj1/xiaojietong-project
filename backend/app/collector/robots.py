"""robots.txt 合规闸门（B26）。

职责：**在发请求之前**回答一个问题 —— "这个 URL 我们允许抓吗？"

设计要点
--------
1. **按 origin 缓存**：`robots.txt` 是站点级的，同站点多个 URL 只拉一次；
   带 TTL（默认 1 小时），过期后重新拉取。
2. **区分「明确禁止」与「取不到」**：
   - HTTP 404 / 410：按 RFC 9309 视为**无限制**，允许抓取；
   - 网络错误 / 5xx：属于"规则未知"，默认 **保守拒绝**（`on_error="block"`），
     绝不因为"读不到规则"就默认可以抓。需要放宽时显式传 `on_error="allow"`。
3. **遵守 Crawl-delay**：解析出的 `crawl_delay` 交给限速器，与
   `rate_limit_qps` 取更严的那一个（见 `limiter.py`）。
4. **可注入**：`fetcher` / `clock` 都可替换，单测全程离线、不碰网络。

零业务依赖：不 import `app.db` / `app.core.config`，与 C21 适配器同一约定，
方便 CLI、CI 与独立采集脚本直接使用。
"""
from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

#: 默认 UA：**必须能表明身份**（不伪装浏览器），并给出可联系的地址。
#:
#: ⚠️ 这里**只能放 ASCII**：HTTP 头字段在 `http.client` 里按 latin-1 编码，
#: 混进任何中文都会让**每一次**请求在发出去之前就抛 `UnicodeEncodeError`
#: —— B26 曾因此 100% 抓不到任何页面（robots.txt 与业务页面一起失效，
#: 而且报错被包成"抓取失败"，看起来像对方站点的问题）。见 PR #132 复检。
#: 中文说明写在注释里即可；robots.txt 的 UA 匹配也用不上中文。
DEFAULT_USER_AGENT = (
    "XJTCampusBot/1.0 (+https://github.com/asuperj1/xiaojietong-project; "
    "contact: admin@xiaojietong.example)"
)

#: 视为"不存在 robots.txt、因而无限制"的状态码（RFC 9309 §2.3.1.3）。
_NO_ROBOTS_CODES = (404, 410)

#: robots.txt 正文上限，避免异常站点拖垮采集进程。
_MAX_ROBOTS_BYTES = 512 * 1024


class RobotsUnavailable(RuntimeError):
    """robots.txt 既拿不到、也不是明确的 404 —— 规则处于未知状态。"""


@dataclass(frozen=True)
class RobotsDecision:
    """一次 robots 判定的结果。"""

    allowed: bool
    reason: str
    robots_url: str
    crawl_delay: Optional[float] = None
    from_cache: bool = False

    def __bool__(self) -> bool:  # 允许写 `if gate.check(url):`
        return self.allowed


@dataclass
class _RobotsFile:
    """一个 origin 的 robots.txt 解析结果（缓存单元）。"""

    robots_url: str
    note: str
    parser: Optional[RobotFileParser] = None
    crawl_delay: Optional[float] = None
    blocked_by_policy: bool = False
    expires_at: float = 0.0


def _default_fetcher(url: str, timeout: float, user_agent: str) -> tuple[int, str]:
    """默认取 robots.txt：返回 (状态码, 正文)。网络故障抛 RobotsUnavailable。"""
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 目标由配置白名单决定
            raw = resp.read(_MAX_ROBOTS_BYTES)
            return int(getattr(resp, "status", 200)), raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), ""
    except Exception as exc:  # noqa: BLE001 - 统一转成"规则未知"
        raise RobotsUnavailable(f"{type(exc).__name__}: {exc}") from exc


class RobotsGate:
    """按 origin 缓存的 robots.txt 判定器。"""

    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 5.0,
        ttl: float = 3600.0,
        on_error: str = "block",
        fetcher: Optional[Callable[[str, float, str], tuple[int, str]]] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if on_error not in ("block", "allow"):
            raise ValueError("on_error 只能是 'block' 或 'allow'")
        self.user_agent = user_agent
        self.timeout = timeout
        self.ttl = ttl
        self.on_error = on_error
        self._fetcher = fetcher or _default_fetcher
        self._clock = clock
        self._cache: dict[str, _RobotsFile] = {}
        self.stats = {"loaded": 0, "cache_hit": 0, "expired": 0, "blocked": 0, "unavailable": 0}

    # ---------------------------------------------------------------- 工具 ----

    @staticmethod
    def _origin(url: str) -> str:
        p = urlparse(url)
        if not p.scheme or not p.netloc:
            raise ValueError(f"URL 不完整，缺少 scheme/netloc：{url!r}")
        return f"{p.scheme}://{p.netloc}"

    def invalidate(self, url_or_origin: str = "") -> None:
        """清缓存：给定 URL/origin 只清它，留空清全部。"""
        if not url_or_origin:
            self._cache.clear()
            return
        key = url_or_origin if "://" in url_or_origin else f"https://{url_or_origin}"
        self._cache.pop(self._origin(key), None)

    # ---------------------------------------------------------------- 加载 ----

    def _load(self, origin: str) -> _RobotsFile:
        robots_url = origin + "/robots.txt"
        try:
            status, text = self._fetcher(robots_url, self.timeout, self.user_agent)
        except RobotsUnavailable as exc:
            self.stats["unavailable"] += 1
            if self.on_error == "allow":
                return _RobotsFile(robots_url, f"robots.txt 不可得（{exc}）→ 放行（on_error=allow）")
            return _RobotsFile(
                robots_url, f"robots.txt 不可得（{exc}）→ 保守拒绝", blocked_by_policy=True
            )

        if status in _NO_ROBOTS_CODES:
            self.stats["loaded"] += 1
            return _RobotsFile(robots_url, f"robots.txt 不存在（HTTP {status}）→ 无限制")

        if status >= 400:
            self.stats["unavailable"] += 1
            if self.on_error == "allow":
                return _RobotsFile(robots_url, f"robots.txt 返回 HTTP {status} → 放行（on_error=allow）")
            return _RobotsFile(
                robots_url, f"robots.txt 返回 HTTP {status} → 保守拒绝", blocked_by_policy=True
            )

        self.stats["loaded"] += 1
        parser = RobotFileParser()
        parser.parse(text.splitlines())
        delay = parser.crawl_delay(self.user_agent)
        return _RobotsFile(
            robots_url, "robots.txt 已加载", parser=parser,
            crawl_delay=float(delay) if delay else None,
        )

    # ---------------------------------------------------------------- 判定 ----

    def check(self, url: str) -> RobotsDecision:
        """判断 `url` 是否允许抓取（带缓存）。"""
        origin = self._origin(url)
        now = self._clock()
        rf = self._cache.get(origin)
        cached = rf is not None and rf.expires_at > now

        if cached:
            self.stats["cache_hit"] += 1
        else:
            if rf is not None:
                self.stats["expired"] += 1
            rf = self._load(origin)
            rf.expires_at = now + self.ttl
            self._cache[origin] = rf

        decision = self._decide(rf, url, from_cache=cached)
        if not decision.allowed:
            self.stats["blocked"] += 1
        return decision

    def _decide(self, rf: _RobotsFile, url: str, *, from_cache: bool) -> RobotsDecision:
        if rf.blocked_by_policy:
            return RobotsDecision(False, rf.note, rf.robots_url, None, from_cache)
        if rf.parser is None:
            return RobotsDecision(True, rf.note, rf.robots_url, None, from_cache)
        allowed = rf.parser.can_fetch(self.user_agent, url)
        reason = "robots.txt 允许" if allowed else f"robots.txt 禁止该路径（{rf.robots_url}）"
        return RobotsDecision(allowed, reason, rf.robots_url, rf.crawl_delay, from_cache)
