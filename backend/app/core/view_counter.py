"""浏览量延迟聚合写入。

对应审计 **CAC-03**。原实现 `GET /forum/topics/{id}` 每读一次就执行一次
`UPDATE topic SET view_count = view_count + 1`，带来三重代价：

1. **行锁热点**：热门帖被并发浏览时，所有请求串行争用同一行的排他锁，
   帖子越热越慢（典型 write hotspot，压测里表现为吞吐随热度下降）；
2. **写放大**：读 QPS = 写 QPS，InnoDB 要刷新等量的 redo log / binlog；
3. **额外占用池连接**：该端点是同步 `def`（跑在 AnyIO 线程池），这条写会再占一个连接。

方案：读路径只做**内存自增**（无 SQL、无锁等待），后台协程按固定周期把累积
增量合并成 `UPDATE topic SET view_count = view_count + ?` 批量落库，把 N 次读
放大压缩为 1 条写；同一帖子在一个周期内的多次浏览天然合并。

取舍：进程被强杀（kill -9）会丢失最后一个周期内的计数。浏览量属近似展示指标，
该误差可接受；`flush_interval` 越小误差越小但写次数越多，默认 5 秒。
"""
from __future__ import annotations

import asyncio
import logging
import threading

logger = logging.getLogger(__name__)


class ViewCounter:
    """线程安全的浏览量聚合器。"""

    def __init__(self, flush_interval: float = 5.0) -> None:
        self.flush_interval = flush_interval
        self._pending: dict[int, int] = {}
        # 读路径在同步端点（AnyIO 线程池）里调用，必须用 threading.Lock 而非 asyncio.Lock
        self._lock = threading.Lock()
        self._task: asyncio.Task | None = None
        self._total_bumped = 0
        self._total_flushed = 0

    # ---------- 读路径 ----------

    def bump(self, topic_id: int) -> None:
        """记一次浏览（纯内存，无 IO，可在同步端点直接调用）。"""
        with self._lock:
            self._pending[topic_id] = self._pending.get(topic_id, 0) + 1
            self._total_bumped += 1

    # ---------- 落库 ----------

    def _drain(self) -> dict[int, int]:
        with self._lock:
            if not self._pending:
                return {}
            pending, self._pending = self._pending, {}
            return pending

    def _flush_once(self) -> int:
        """把累积增量落库，返回成功写入的帖子数（阻塞式，供 to_thread 调用）。"""
        pending = self._drain()
        if not pending:
            return 0
        from app.db import cpp_bridge  # 延迟导入，避免模块级循环依赖

        written = 0
        for topic_id, delta in pending.items():
            try:
                cpp_bridge.execute(
                    "UPDATE topic SET view_count = view_count + ? WHERE id = ?",
                    [delta, topic_id],
                )
                written += 1
                self._total_flushed += delta
            except Exception as exc:  # noqa: BLE001  单条失败不应中断整批
                logger.warning("浏览量落库失败 topic=%s delta=%s: %s", topic_id, delta, exc)
        if written:
            logger.debug("浏览量批量落库: %d 个帖子", written)
        return written

    # ---------- 生命周期 ----------

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.flush_interval)
            try:
                await asyncio.to_thread(self._flush_once)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("浏览量聚合任务异常: %s", exc)

    def start(self) -> None:
        """启动后台聚合任务（需在事件循环内调用，见 main.py lifespan）。"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
            logger.info("📊 浏览量聚合已启动（每 %.0fs 落库一次）", self.flush_interval)

    async def stop(self) -> None:
        """停止聚合并做最后一次落库，保证已累积计数不丢。"""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await asyncio.to_thread(self._flush_once)

    # ---------- 观测 ----------

    @property
    def stats(self) -> dict[str, int]:
        with self._lock:
            pending = sum(self._pending.values())
        return {
            "bumped": self._total_bumped,
            "flushed": self._total_flushed,
            "pending": pending,
        }


# 全局单例：读接口与 lifespan 共用
view_counter = ViewCounter()
