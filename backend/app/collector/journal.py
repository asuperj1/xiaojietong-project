"""采集日志（B26）—— 「有日志可追溯」的落地。

验收要求是"采集不违规；**有日志可追溯**"。所谓可追溯，至少能回答：

- 什么时候、哪个源、哪个 URL？
- robots 判定结果是什么（允许 / 禁止，依据哪份 robots.txt）？
- 限速等了多久？
- 结果如何（HTTP 状态、字节数、失败原因）？

实现方式是 **JSONL 追加写**（每行一个事件对象）：
- 天然可追加、可 grep、可 `jq`，坏了也只坏一行；
- 与 `logging` 并行输出，进程内看日志、事后查 JSONL 都行。

零业务依赖：不 import `app.db` / `app.core.config`；默认落盘目录由调用方给，
或走 `XJT_COLLECT_LOG` 环境变量。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

#: 环境变量：JSONL 落盘路径（留空 = 只走 logging，不落盘）。
ENV_LOG_PATH = "XJT_COLLECT_LOG"

#: 环境变量：是否把每条事件也打到 stdout（便于本地观察）。
ENV_LOG_ECHO = "XJT_COLLECT_ECHO"

DEFAULT_LOG_RELPATH = Path("data") / "collect" / "collect.jsonl"

logger = logging.getLogger("xjt.collector")


@dataclass
class CollectEvent:
    """一条采集事件。字段刻意保持扁平，便于直接喂给报表或告警。"""

    ts: str
    source: str
    url: str
    phase: str                      # robots / rate_limit / fetch
    outcome: str                    # ok / blocked / skipped / http_error / network_error
    detail: str = ""
    status: Optional[int] = None
    bytes: Optional[int] = None
    waited_ms: int = 0
    elapsed_ms: int = 0
    extra: dict = field(default_factory=dict)

    def line(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


class CollectJournal:
    """采集事件的记录器（线程安全，追加写 JSONL）。"""

    def __init__(
        self,
        path: Optional[str | os.PathLike] = None,
        *,
        echo: Optional[bool] = None,
        clock: Callable[[], float] = time.time,
        keep_memory: int = 200,
    ) -> None:
        resolved = path if path is not None else os.environ.get(ENV_LOG_PATH) or DEFAULT_LOG_RELPATH
        self.path = Path(resolved) if resolved else None
        self.echo = bool(os.environ.get(ENV_LOG_ECHO)) if echo is None else echo
        self._clock = clock
        self._lock = threading.Lock()
        self._recent: list[CollectEvent] = []
        self._keep = max(1, keep_memory)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- 写入 ----

    def record(self, *, source: str, url: str, phase: str, outcome: str, detail: str = "",
               status: Optional[int] = None, bytes: Optional[int] = None,
               waited_ms: int = 0, elapsed_ms: int = 0, **extra) -> CollectEvent:
        event = CollectEvent(
            ts=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self._clock())),
            source=source, url=url, phase=phase, outcome=outcome, detail=detail,
            status=status, bytes=bytes, waited_ms=waited_ms, elapsed_ms=elapsed_ms,
            extra=extra or {},
        )
        with self._lock:
            self._recent.append(event)
            if len(self._recent) > self._keep:
                del self._recent[: len(self._recent) - self._keep]
            if self.path is not None:
                try:
                    with self.path.open("a", encoding="utf-8") as fh:
                        fh.write(event.line() + "\n")
                except OSError as exc:  # 日志写不进去不能拖垮采集
                    logger.warning("采集日志落盘失败（%s）：%s", self.path, exc)
        level = logging.WARNING if outcome in ("blocked", "http_error", "network_error") else logging.INFO
        logger.log(level, "[%s] %s %s → %s %s", source, phase, url, outcome,
                   f"({detail})" if detail else "")
        if self.echo:
            print(event.line())
        return event

    # ---------------------------------------------------------------- 查询 ----

    def recent(self, n: int = 20) -> list[CollectEvent]:
        """最近 n 条（进程内缓存，便于健康检查/CLI 展示）。"""
        with self._lock:
            return list(self._recent[-n:])

    def iterate(self, *, limit: int = 0) -> Iterable[CollectEvent]:
        """从 JSONL 逐行读取历史事件（limit<=0 表示不限）。"""
        if self.path is None or not self.path.exists():
            return iter(())
        return self._read_lines(limit)

    def _read_lines(self, limit: int) -> Iterable[CollectEvent]:
        out: list[CollectEvent] = []
        with self.path.open(encoding="utf-8") as fh:  # type: ignore[union-attr]
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                out.append(CollectEvent(**payload))
                if limit and len(out) >= limit:
                    break
        return out

    def stats(self) -> dict:
        """按 outcome 计数（只统计进程内保留的窗口）。"""
        counter: dict[str, int] = {}
        for ev in self.recent(self._keep):
            counter[ev.outcome] = counter.get(ev.outcome, 0) + 1
        return {"path": str(self.path) if self.path else None, "recent": counter}


def journal_from_env() -> CollectJournal:
    """按环境变量构造日志器（`XJT_COLLECT_LOG` / `XJT_COLLECT_ECHO`）。"""
    return CollectJournal()
