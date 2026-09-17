"""抓取限速（B26）。

一句话：**同一个源，两次请求之间必须等够时间。**

有效间隔取三者的最严值：

    effective_interval = max(1 / rate_limit_qps, robots 的 Crawl-delay)

也就是说，配置里写了 `rate_limit_qps: 0.5`（2 秒一次），而目标站点 robots.txt
又要求 `Crawl-delay: 10`，那实际就是 10 秒一次 —— **永远取更保守的那个**。

设计要点
--------
1. **时钟与睡眠可注入**：单测用假时钟跑完整逻辑，不真的 sleep（`test_limiter.py`）。
2. **同步 + 异步两套接口**：B27 的采集器无论用 requests 还是 httpx/asyncio 都能直接用。
3. **按 key 复用**：`limiter_for("library-notice", 0.5)` 拿到的永远是同一个实例，
   避免"每个函数各建一个限速器"导致限速形同虚设。
4. **线程安全**：注册表加锁；单个限速器的 `acquire` 也加锁（多线程采集场景）。

零业务依赖：不 import `app.db` / `app.core.config`。
"""
from __future__ import annotations

import asyncio
import random
import threading
import time
from typing import Callable, Optional


class RateLimiter:
    """按最小间隔放行的限速器（key 粒度由调用方决定）。"""

    def __init__(
        self,
        key: str,
        qps: float,
        *,
        jitter: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        rng: Callable[[], float] = random.random,
    ) -> None:
        if qps < 0:
            raise ValueError("qps 不能为负")
        self.key = key
        self.qps = float(qps)
        self.jitter = max(0.0, float(jitter))
        self._clock = clock
        self._sleep = sleep
        self._rng = rng
        self._next_at = 0.0
        self._lock = threading.Lock()
        self.stats = {"acquired": 0, "waited": 0, "waited_seconds": 0.0}

    # ---------------------------------------------------------------- 计算 ----

    def interval(self, crawl_delay: Optional[float] = None) -> float:
        """返回该源当前的有效间隔（秒）。"""
        base = (1.0 / self.qps) if self.qps > 0 else 0.0
        if crawl_delay and crawl_delay > 0:
            base = max(base, float(crawl_delay))
        return base

    def peek_wait(self, crawl_delay: Optional[float] = None) -> float:
        """还要等多久（不睡眠、不改状态），便于调用方先做决策。"""
        return max(0.0, self._next_at - self._clock())

    def _reserve(self, interval: float) -> float:
        """在锁内算出本次要等的时间，并预约下一次窗口。返回等待秒数。"""
        now = self._clock()
        wait = max(0.0, self._next_at - now)
        # 下一次窗口从"本次实际放行时刻"起算，避免等待时间被累计放大
        self._next_at = max(now, self._next_at) + interval
        return wait

    # ------------------------------------------------------------ 同步接口 ----

    def acquire(self, crawl_delay: Optional[float] = None) -> float:
        """阻塞到可以发请求为止，返回**实际等待秒数**。"""
        interval = self.interval(crawl_delay)
        with self._lock:
            wait = self._reserve(interval)
            if wait > 0 and self.jitter:
                wait += self._rng() * self.jitter
            self.stats["acquired"] += 1
            if wait > 0:
                self.stats["waited"] += 1
                self.stats["waited_seconds"] = round(self.stats["waited_seconds"] + wait, 3)
        if wait > 0:
            self._sleep(wait)
        return round(wait, 3)

    # ------------------------------------------------------------ 异步接口 ----

    async def acquire_async(self, crawl_delay: Optional[float] = None) -> float:
        """`acquire` 的异步版本（供 asyncio 采集器使用）。"""
        interval = self.interval(crawl_delay)
        with self._lock:
            wait = self._reserve(interval)
            if wait > 0 and self.jitter:
                wait += self._rng() * self.jitter
            self.stats["acquired"] += 1
            if wait > 0:
                self.stats["waited"] += 1
                self.stats["waited_seconds"] = round(self.stats["waited_seconds"] + wait, 3)
        if wait > 0:
            await asyncio.sleep(wait)
        return round(wait, 3)

    def reset(self) -> None:
        """清空窗口状态（换班 / 手动重跑时用）。"""
        with self._lock:
            self._next_at = 0.0


# ------------------------------------------------------------------ 注册表 ----

_REGISTRY: dict[str, RateLimiter] = {}
_REGISTRY_LOCK = threading.Lock()


def limiter_for(key: str, qps: float, **kwargs) -> RateLimiter:
    """按 key 取（或建）限速器；同 key 再次调用会**更新 qps** 而不重建实例。

    这样即便配置热更新或不同调用点传入不同 qps，也不会出现两个独立窗口
    互相"看不见"、把限速架空的情况。
    """
    with _REGISTRY_LOCK:
        limiter = _REGISTRY.get(key)
        if limiter is None:
            limiter = RateLimiter(key, qps, **kwargs)
            _REGISTRY[key] = limiter
        else:
            limiter.qps = float(qps)
        return limiter


def reset_limiters() -> None:
    """清空注册表（单测隔离用）。"""
    with _REGISTRY_LOCK:
        _REGISTRY.clear()


def limiter_stats() -> dict[str, dict]:
    """所有在册限速器的统计快照，便于日志与健康检查。"""
    with _REGISTRY_LOCK:
        return {k: dict(v.stats) for k, v in _REGISTRY.items()}
