"""采集合规工具包（B26）—— robots.txt 检查 + 限速 + 可追溯日志。

它解决的是"抓之前"的三件事，是 `B27` 配置驱动采集器的地基：

```
    ┌────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────┐
    │ 源配置(C21) │ → │ RobotsGate   │ → │ RateLimiter  │ → │ 真正的请求 │
    │ sources[]  │   │ 允许抓吗？    │   │ 还要等多久？  │   │ (B27)    │
    └────────────┘   └──────────────┘   └──────────────┘   └──────────┘
                             └──────── CollectJournal 全程留痕 ───────┘
```

三个组件也可单独使用；合起来用就走 `FetchGuard`：

```python
from app.collector import FetchGuard

guard = FetchGuard()                       # 默认读 XJT_COLLECT_LOG 落盘
decision = guard.before(source, url)       # source 即 C21 配置里的 sources[i]
if not decision.allowed:
    continue                                # 已被 robots 拒绝，日志里已记原因

resp = requests.get(url, headers={"User-Agent": guard.user_agent}, timeout=10)
guard.after(source, url, status=resp.status_code, size=len(resp.content))
```

零业务依赖：本包不 import `app.db` / `app.core.config` / `app.services`，
可被 CLI、CI、独立采集脚本直接使用，单测也不需要数据库与 Ollama。
（与 C21 适配器模块同一约定，见 `app/adapters/README.md` §5.4）
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from .journal import CollectEvent, CollectJournal, journal_from_env
from .limiter import RateLimiter, limiter_for, limiter_stats, reset_limiters
from .robots import DEFAULT_USER_AGENT, RobotsDecision, RobotsGate, RobotsUnavailable

__all__ = [
    "CollectEvent",
    "CollectJournal",
    "FetchDecision",
    "FetchGuard",
    "RateLimiter",
    "RobotsDecision",
    "RobotsGate",
    "RobotsUnavailable",
    "DEFAULT_USER_AGENT",
    "journal_from_env",
    "limiter_for",
    "limiter_stats",
    "reset_limiters",
]


def _field(source: Any, name: str, default: Any = None) -> Any:
    """从 C21 的 `SourceConfig`（pydantic 模型）或普通 dict 里取字段。

    刻意用 duck typing 而不是 `from app.adapters import SourceConfig`：
    本包要保持零业务依赖，且 C21 未合并时也能独立跑。
    """
    if source is None:
        return default
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


@dataclass(frozen=True)
class FetchDecision:
    """一次"抓取前检查"的结论。"""

    allowed: bool
    source: str
    url: str
    reason: str
    waited_ms: int = 0
    crawl_delay: Optional[float] = None

    def __bool__(self) -> bool:
        return self.allowed


class FetchGuard:
    """B27 采集器的唯一抓取入口：先过 robots，再等限速，全程留痕。"""

    def __init__(
        self,
        *,
        journal: Optional[CollectJournal] = None,
        robots: Optional[RobotsGate] = None,
        user_agent: str = DEFAULT_USER_AGENT,
        default_qps: float = 0.5,
        respect_robots: bool = True,
        jitter: float = 0.0,
        robots_timeout: float = 5.0,
        robots_ttl: float = 3600.0,
        on_robots_error: str = "block",
        limiter_kwargs: Optional[dict] = None,
    ) -> None:
        self.user_agent = user_agent
        self.journal = journal if journal is not None else journal_from_env()
        self.robots = robots if robots is not None else RobotsGate(
            user_agent=user_agent, timeout=robots_timeout, ttl=robots_ttl,
            on_error=on_robots_error,
        )
        self.default_qps = float(default_qps)
        self.respect_robots = bool(respect_robots)
        self.jitter = jitter
        # 透传给 limiter_for（如注入假时钟做离线测试）
        self.limiter_kwargs = dict(limiter_kwargs or {})

    # ------------------------------------------------------------ 抓取前 ----

    def _source_key(self, source: Any) -> str:
        return str(_field(source, "key", None) or _field(source, "name", None) or "unknown")

    def before(self, source: Any, url: str) -> FetchDecision:
        """robots 判定 + 限速等待 + 记日志。**不发起任何业务请求。**"""
        key = self._source_key(source)
        t0 = time.perf_counter()

        # ---- ① robots 闸门 ----
        respect = bool(_field(source, "respect_robots", self.respect_robots))
        crawl_delay: Optional[float] = None
        robots_reason = "配置关闭了 robots 检查（respect_robots=false）"
        if respect:
            decision = self.robots.check(url)
            if not decision.allowed:
                self.journal.record(
                    source=key, url=url, phase="robots", outcome="blocked",
                    detail=decision.reason,
                    elapsed_ms=int((time.perf_counter() - t0) * 1000),
                )
                return FetchDecision(False, key, url, decision.reason)
            crawl_delay = decision.crawl_delay
            robots_reason = decision.reason
        else:
            self.journal.record(source=key, url=url, phase="robots", outcome="skipped",
                                detail=robots_reason)

        # ---- ② 限速 ----
        qps = _field(source, "rate_limit_qps", None)
        qps = self.default_qps if qps is None else float(qps)
        limiter = limiter_for(key, qps, jitter=self.jitter, **self.limiter_kwargs)
        waited = limiter.acquire(crawl_delay)
        waited_ms = int(round(waited * 1000))
        if waited_ms > 0:
            self.journal.record(
                source=key, url=url, phase="rate_limit", outcome="ok",
                detail=f"等待 {waited:.3f}s（qps={limiter.qps}，crawl_delay={crawl_delay}）",
                waited_ms=waited_ms,
            )

        reason = robots_reason if respect else robots_reason
        return FetchDecision(True, key, url, reason, waited_ms=waited_ms, crawl_delay=crawl_delay)

    # ------------------------------------------------------------ 抓取后 ----

    def after(self, source: Any, url: str, *, status: Optional[int] = None,
              size: Optional[int] = None, elapsed_ms: int = 0,
              error: Optional[str] = None, **extra) -> CollectEvent:
        """记录一次实际请求的结果（成功、HTTP 错误或网络异常）。"""
        key = self._source_key(source)
        if error:
            outcome, detail = "network_error", error
        elif status is None:
            outcome, detail = "ok", ""
        elif 200 <= int(status) < 300:
            outcome, detail = "ok", ""
        else:
            outcome, detail = "http_error", f"HTTP {status}"
        return self.journal.record(
            source=key, url=url, phase="fetch", outcome=outcome, detail=detail,
            status=None if status is None else int(status), bytes=size,
            elapsed_ms=elapsed_ms, **extra,
        )
