"""采集：把原始文本变成 `Sample` 列表（`C31`）。

两条来源，职责分明
------------------
1. **本地目录**（离线可用，也是单测入口）：`.txt` / `.md` → 一个文件一条样本。
2. **数据库**（真实素材）：
   - `campus_notice`：校园通知（抽取任务的主要素材，含大量时间/地点/机构）
   - `knowledge_doc`：知识库文档（长文本，可作为泛化素材）

   ⚠️ 数据库来源走 `backend/app/db/cpp_bridge`，需要 `backend/` 在 `sys.path`
   且连接池已初始化 —— 因此**懒加载**：只用文件来源时完全不碰 DB。

去重
----
按正文的 `sha256` 去重（保留先出现的）。重复公告在不同来源被采集两次是很常见的
（同一通知既有网页版也有人工录入版），不去重会直接污染 `C32` 的一致性统计。
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Iterable

from .schema import Sample, make_sample

__all__ = [
    "REPO_ROOT",
    "collect_from_dir",
    "collect_from_db",
    "dedupe",
    "summary",
]

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
_BACKEND = REPO_ROOT / "backend"

DEFAULT_EXTS = (".txt", ".md")


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def collect_from_dir(root: str | Path, exts: Iterable[str] = DEFAULT_EXTS) -> list[Sample]:
    """把目录下的文本文件逐个采成样本（**离线**，不依赖数据库）。"""
    base = Path(root)
    if not base.exists():
        return []
    wanted = {e.lower() for e in exts}
    out: list[Sample] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in wanted:
            continue
        try:
            text = path.read_text(encoding="utf-8").strip()
        except UnicodeDecodeError:
            continue                      # 非文本文件直接跳过，不因一条坏数据中断整批
        if not text:
            continue
        rel = path.relative_to(base).as_posix()
        out.append(make_sample(text, source_type="file", ref=rel))
    return out


def _ensure_backend_on_path() -> None:
    """把 `backend/` 加进 sys.path（只为数据库来源服务，文件来源不会触发）。"""
    b = str(_BACKEND)
    if b not in sys.path:
        sys.path.insert(0, b)


def _ensure_pool() -> None:
    """确保连接池可用（**幂等**）。

    为什么必须自己初始化：`cpp_bridge.query()` 在池未初始化时会直接抛
    `RuntimeError: 连接池未初始化`。本工具链是**独立进程**（不像后端由 lifespan
    初始化），所以得自己按 `settings` 建池；若已被外层初始化过则什么都不做。
    """
    from app.core.config import settings       # noqa: PLC0415
    from app.db import cpp_bridge              # noqa: PLC0415

    if cpp_bridge.pool_ready():
        return
    cpp_bridge.init_db(
        settings.db_host,
        settings.db_port,
        settings.db_user,
        settings.db_password,
        settings.db_name,
    )


def collect_from_db(kind: str = "notice", limit: int = 200) -> list[Sample]:
    """从数据库采集（`notice` = 校园通知；`knowledge` = 知识库文档）。

    采集后统一把 `title + 正文` 拼成 `text`：抽取任务要看到完整语境，
    只给正文会丢掉"《XX通知》"里的机构与事项线索。
    """
    _ensure_backend_on_path()
    _ensure_pool()
    from app.db import cpp_bridge          # noqa: PLC0415 - 懒加载，见模块 docstring

    if kind == "notice":
        rows = cpp_bridge.query(
            "SELECT id, title, content FROM campus_notice ORDER BY id DESC LIMIT ?",
            [int(limit)],
        )
        stype = "notice"
    elif kind == "knowledge":
        rows = cpp_bridge.query(
            "SELECT id, title, content FROM knowledge_doc WHERE status != 2 "
            "ORDER BY id DESC LIMIT ?",
            [int(limit)],
        )
        stype = "knowledge"
    else:
        raise ValueError(f"未知采集类型：{kind!r}（支持 notice / knowledge）")

    out: list[Sample] = []
    for r in rows or []:
        title = str(r.get("title") or "").strip()
        content = str(r.get("content") or "").strip()
        text = f"{title}\n{content}".strip() if title else content
        if not text:
            continue
        out.append(
            make_sample(
                text,
                source_type=stype,
                ref=str(r.get("id") or ""),
            )
        )
    return out


def dedupe(samples: Iterable[Sample]) -> tuple[list[Sample], int]:
    """按正文哈希去重，返回 (去重后列表, 被丢弃条数)。"""
    seen: set[str] = set()
    kept: list[Sample] = []
    dropped = 0
    for s in samples:
        h = _text_hash(s.text)
        if h in seen:
            dropped += 1
            continue
        seen.add(h)
        kept.append(s)
    return kept, dropped


def summary(samples: Iterable[Sample]) -> dict:
    """采集结果的粗统计（条数 / 来源分布 / 文本长度），供 CLI 打印与报告使用。"""
    rows = list(samples)
    by_type: dict[str, int] = {}
    for s in rows:
        by_type[s.source.get("type", "?")] = by_type.get(s.source.get("type", "?"), 0) + 1
    lens = [len(s.text) for s in rows] or [0]
    return {
        "total": len(rows),
        "by_source_type": by_type,
        "text_len": {
            "min": min(lens),
            "max": max(lens),
            "avg": round(sum(lens) / len(lens), 1),
        },
    }
