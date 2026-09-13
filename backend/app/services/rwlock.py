"""读写锁：读并发、写独占、写优先。

对应审计 **CAC-06**。向量库原先用单个 `threading.Lock` 保护所有操作，
导致 `search`（高频读）与 `add`（建索引时批量写）**互相阻塞**：
`build_index.py --force` 跑起来后，所有 `/chat` 检索请求都要排队等锁，
表现为"建索引期间 AI 问答整体卡住"。

而向量检索是典型的读多写少场景（查询远多于入库），用互斥锁把并发读
也串行化了，属于不必要的性能损失。

语义：
- 多个读者可同时进入；
- 写者独占（无读者也无其他写者）；
- **写优先**：一旦有写者等待，后续新读者排队，避免写者饥饿
  （纯读优先的实现里，持续的查询流会让建索引永远拿不到锁）。

用法：
    with lock.read():
        ...
    with lock.write():
        ...
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator


class RWLock:
    """可重入性：**不可重入**。持有读锁时再取写锁会死锁，反之亦然。"""

    def __init__(self) -> None:
        self._cond = threading.Condition(threading.Lock())
        self._readers = 0          # 当前持读锁的线程数
        self._writer = False       # 是否有写者持有
        self._waiting_writers = 0  # 正在等待的写者数（用于写优先）

    # ---------- 读 ----------

    @contextmanager
    def read(self) -> Iterator[None]:
        self.acquire_read()
        try:
            yield
        finally:
            self.release_read()

    def acquire_read(self) -> None:
        with self._cond:
            # 写优先：有写者持有或正在等待时，新读者一律排队
            while self._writer or self._waiting_writers > 0:
                self._cond.wait()
            self._readers += 1

    def release_read(self) -> None:
        with self._cond:
            self._readers -= 1
            if self._readers == 0:
                self._cond.notify_all()

    # ---------- 写 ----------

    @contextmanager
    def write(self) -> Iterator[None]:
        self.acquire_write()
        try:
            yield
        finally:
            self.release_write()

    def acquire_write(self) -> None:
        with self._cond:
            self._waiting_writers += 1
            try:
                while self._writer or self._readers > 0:
                    self._cond.wait()
            finally:
                self._waiting_writers -= 1
            self._writer = True

    def release_write(self) -> None:
        with self._cond:
            self._writer = False
            self._cond.notify_all()

    # ---------- 观测 ----------

    @property
    def stats(self) -> dict[str, int | bool]:
        with self._cond:
            return {
                "readers": self._readers,
                "writer": self._writer,
                "waiting_writers": self._waiting_writers,
            }
