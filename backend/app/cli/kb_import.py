"""B17 知识库批量导入管道（命令行）。

把「一堆校园文档（Markdown / HTML / PDF / txt）」批量灌进 ``knowledge_doc``，
支持进度反馈、失败续传、内容变化检测、基准回滚，并可一键补建向量索引。

**用法**（在 ``backend/`` 目录下执行）::

    # 1) 预演：只解析不写库（无需数据库，可先验证解析质量）
    python -m app.cli.kb_import ..\\docs\\kb_samples --dry-run

    # 2) 正式导入（默认不向量化，先灌库更快更稳）
    python -m app.cli.kb_import ..\\docs\\kb_samples --category 校园知识 --source-prefix samples/

    # 3) 统一补建向量索引（分片调用 rag.build_index，带进度）
    python -m app.cli.kb_import --index-only

    # 4) 失败续传：直接重跑同一条命令即可（已入库且内容未变的文件会 skip）
    python -m app.cli.kb_import ..\\docs\\kb_samples --category 校园知识 --source-prefix samples/

    # 5) 规模基准演练（120 篇合成语料）+ 达标闸门 + 清理
    python -m app.cli.kb_import ..\\..\\tmp_kb_bench --source-prefix bench/ --require-new 120
    python -m app.cli.kb_import --purge-source-prefix bench/

> 说明：``--require-new`` 断言的是**本轮新建+覆盖**篇数，因此**续传轮不要再带它**
> （续传轮全部 skip(unchanged) 属正常，带 `--require-new` 会如实报未达标并退出码 1）；
> 续传轮若想校验总量，用 ``--require-min <库内篇数>``。
> 目录扫描会自动跳过 ``README*``、``_``/``.`` 开头的文件与 ``__pycache__`` 等目录。

**退出码**（供 CI / 验收脚本判读，避免"永远返回 0"）::

    0  全部成功（或跳过）
    1  存在失败/冲突，或 --require-min 未达标
    2  参数或环境错误（路径不存在、扩展缺失、数据库不可用）
    130 用户中断（Ctrl+C，状态已落盘，可重跑续传）

**断点续传原理**：状态文件（默认 ``data/kb_import_state.json``）记录
``相对路径 -> {sha256, doc_id, status, ts}``；重跑时若文件内容哈希未变且库内文档仍在，
直接跳过；内容变化则按 ``--force`` 决定覆盖或报冲突。写文件采用"临时文件 + 替换"，
中断也不会写坏状态。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# 允许 `python -m app.cli.kb_import` 在任意工作目录下运行
_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.core.config import settings                       # noqa: E402
from app.services.parser import (                          # noqa: E402
    SUPPORTED_EXT,
    ParseError,
    parse_bytes,
)
from app.services.parser.base import EXT_KINDS             # noqa: E402

STATE_VERSION = 1
DEFAULT_STATE = "data/kb_import_state.json"
SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".idea", ".vscode", "build", "dist"}
# 语料目录里的"说明文件"不入库（它们描述语料本身，不是校园知识）；
# 以 _ 或 . 开头的文件同样跳过（临时/隐藏文件）。
SKIP_FILES = {"readme.md", "readme.txt", "readme.html", "readme.markdown", "readme", "说明.md"}


# ------------------------------------------------------------------ 环境 ----

def ensure_db() -> None:
    """惰性初始化 jt_db 连接池（CLI 是独立进程，不经过 FastAPI lifespan）。"""
    from app.db import cpp_bridge

    if not cpp_bridge.available():
        raise RuntimeError(
            "jt_db C++ 扩展不可用：请确认 backend/app/db/native/ 下已放置 jt_db(.pyd/.so) "
            "及依赖 DLL（见 db/cpp_driver/README.md）"
        )
    if not cpp_bridge.pool_ready():
        cpp_bridge.init_db(
            settings.db_host,
            settings.db_port,
            settings.db_user,
            settings.db_password,
            settings.db_name,
            settings.db_min_conn,
            settings.db_max_conn,
        )


# ------------------------------------------------------------------ 状态 ----

@dataclass
class State:
    """断点续传状态（JSON：version + root + files）。

    ``path=None`` 表示 ``--no-state``（不落盘，每次全量按库内内容判定）。
    """

    path: Optional[Path]
    files: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Optional[Path], reset: bool = False) -> "State":
        if path is None or reset or not path.is_file():
            return cls(path=path, files={})
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            files = raw.get("files") or {}
            return cls(path=path, files={str(k): dict(v) for k, v in files.items()})
        except Exception:      # noqa: BLE001 - 状态文件损坏时按空处理（重新判定）
            return cls(path=path, files={})

    def get(self, key: str) -> dict:
        return self.files.get(key, {})

    def put(self, key: str, entry: dict) -> None:
        self.files[key] = entry

    def save(self) -> None:
        """原子写盘（临时文件 + 替换），避免中断写坏状态。"""
        if self.path is None:
            return                                  # --no-state：不落盘
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": STATE_VERSION,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "count": len(self.files),
            "files": self.files,
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)


# ------------------------------------------------------------------ 扫描 ----

def scan_files(paths: list[str], globs, limit: Optional[int] = None) -> list[Path]:
    """展开输入路径为文件列表（目录递归；按扩展名过滤；稳定排序）。

    跳过：``.``/``_`` 开头的文件、``README*`` 等说明文件、``SKIP_DIRS`` 目录。
    """
    if isinstance(globs, str):
        globs = [g.strip() for g in globs.replace("，", ",").split(",") if g.strip()]
    allow = {str(g).lower().lstrip("*") for g in globs}
    found: list[Path] = []
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_file():
            found.append(p)
            continue
        if not p.is_dir():
            raise FileNotFoundError(f"路径不存在：{p}")
        for dirpath, dirnames, filenames in os.walk(p):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for name in sorted(filenames):
                if name.startswith(".") or name.startswith("_") or name.lower() in SKIP_FILES:
                    continue
                suffix = Path(name).suffix.lower()
                if suffix in allow and suffix in EXT_KINDS:
                    found.append(Path(dirpath) / name)
    # 去重 + 稳定排序（保证多次运行顺序一致，便于续传日志比对）
    uniq = sorted({str(f.resolve()): f for f in found}.values(), key=lambda x: str(x))
    return uniq[:limit] if limit else uniq


def source_key(root: Path, file: Path, prefix: str) -> str:
    """生成写入 ``source_url`` 的来源键：``<prefix><相对路径>``（统一 / 分隔）。"""
    try:
        rel = file.resolve().relative_to(root.resolve())
    except ValueError:
        rel = Path(file.name)
    return (prefix or "") + str(rel).replace("\\", "/")


def infer_category(root: Path, file: Path, fallback: str) -> str:
    """分类推断：优先命令行指定；否则用二级目录名；再否则用根目录名。"""
    if fallback:
        return fallback
    try:
        rel = file.resolve().relative_to(root.resolve())
        parts = rel.parts[:-1]
        if parts:
            return parts[-1][:64]
    except ValueError:
        pass
    return root.name[:64] or "未分类"


# ---------------------------------------------------------------- 导入器 ----

@dataclass
class Progress:
    """导入过程中的计数与耗时（用于进度条与汇总）。"""

    total: int = 0
    done: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    conflicts: int = 0
    failed: int = 0
    embed_failed: int = 0
    chars: int = 0
    started: float = field(default_factory=time.time)

    def line(self, index: int, status: str, name: str, extra: str = "") -> str:
        """单行进度：``[ 12/120] ok(created)  10.0%  剩余 3s  文件名  …``"""
        self.done = index
        pct = (index / self.total * 100) if self.total else 100.0
        elapsed = max(1e-6, time.time() - self.started)
        rate = index / elapsed
        remain = (self.total - index) / rate if rate > 0 else 0.0
        return (
            f"[{index:>4}/{self.total}] {status:<14} {pct:5.1f}%  "
            f"剩余{remain:5.0f}s  {name}{('  ' + extra) if extra else ''}"
        )


async def _import_one(
    file: Path,
    root: Path,
    args: argparse.Namespace,
    state: State,
    progress: Progress,
    report: dict,
) -> None:
    """处理单个文件（解析 + 入库），异常一律记入报告而不中断整批。"""
    from app.services import knowledge

    key = source_key(root, file, args.source_prefix)
    entry = state.get(key)
    try:
        data = file.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
    except OSError as exc:
        report["failed"].append({"file": key, "error": f"读取失败：{exc}"})
        progress.failed += 1
        return

    # 续传快路径：内容未变 + 库内文档仍在 -> 直接跳过（不再解析，省时）
    # 仅对"已成功落库"的记录生效：conflict / 解析失败 / 空文本 / dry_run 记录必须重跑，
    # 否则会出现「文件已改但库里仍是旧内容」却被判定为 unchanged 的静默错误。
    resumable = entry.get("action") in ("created", "updated", "skipped")
    if resumable and entry.get("sha256") == digest and entry.get("doc_id") and not args.force:
        existing = knowledge.find_by_source(key)
        if existing and int(existing["id"]) == int(entry["doc_id"]):
            progress.skipped += 1
            state.put(key, {**entry, "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "status": "unchanged"})
            report["results"].append({"file": key, "action": "skipped", "status": "unchanged"})
            if not args.quiet:
                print(progress.line(progress.done + 1, "skip(unchanged)", file.name,
                                    f"doc={entry['doc_id']}"))
            return

    try:
        doc = parse_bytes(file.name, data)
    except ParseError as exc:
        report["failed"].append({"file": key, "error": f"解析失败：{exc}"})
        progress.failed += 1
        state.put(key, {"sha256": digest, "doc_id": 0, "status": "parse_failed",
                        "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
        if not args.quiet:
            print(progress.line(progress.done + 1, "FAIL(parse)", file.name, str(exc)[:60]))
        return

    category = args.category or infer_category(root, file, "")
    action = "dry_run"
    status = "parsed"
    doc_id = 0
    chunks = 0
    warnings: list[str] = list(doc.warnings)
    if not args.dry_run:
        result = await knowledge.ingest_document(
            doc,
            category=category,
            source_url=key,
            index=args.index,
            dedup=True,
            overwrite=args.force,
        )
        action = str(result["action"])
        status = str(result["status"])
        doc_id = int(result["doc_id"])
        chunks = int(result["chunks"])
        warnings = list(result.get("warnings") or [])

    if action == "created":
        progress.created += 1
    elif action == "updated":
        progress.updated += 1
    elif action == "skipped":
        progress.skipped += 1
    elif action == "conflict":
        progress.conflicts += 1
        report["conflicts"].append({"file": key, "doc_id": doc_id,
                                    "hint": "内容已变化，确认后加 --force 覆盖"})
    elif action == "empty":
        progress.failed += 1
        report["failed"].append({"file": key, "error": "解析结果为空文本（疑似扫描件）"})
    if status == "embed_failed":
        progress.embed_failed += 1
    if action in ("created", "updated"):
        progress.chars += doc.chars

    state.put(key, {
        "sha256": digest, "doc_id": doc_id, "status": status, "action": action,
        "chars": doc.chars, "chunks": chunks, "fmt": doc.fmt,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    report["results"].append({
        "file": key, "action": action, "status": status, "doc_id": doc_id,
        "fmt": doc.fmt, "chars": doc.chars, "chunks": chunks, "warnings": warnings,
    })
    if not args.quiet:
        label = f"{action}({status})"
        extra = f"fmt={doc.fmt} chars={doc.chars}"
        if doc_id:
            extra += f" doc={doc_id}"
        if chunks:
            extra += f" chunks={chunks}"
        print(progress.line(progress.done + 1, label, file.name, extra))


async def run_import(args: argparse.Namespace, report: dict) -> int:
    """执行导入主流程，返回退出码。"""
    from app.services import knowledge

    roots: list[Path] = []
    for raw in args.paths:
        p = Path(raw).expanduser()
        roots.append(p if p.is_dir() else p.parent)

    files = scan_files(args.paths, args.glob, args.limit)
    if not files:
        print(f"未找到可解析文件（支持的扩展名：{', '.join(SUPPORTED_EXT)}）")
        return 2

    state_path = Path(args.state).expanduser() if args.state else None
    if state_path is not None and not state_path.is_absolute():
        state_path = _BACKEND_DIR / state_path
    state = State.load(state_path, reset=args.reset_state)

    report.update({
        "mode": "import", "dry_run": args.dry_run, "index": args.index,
        "files": len(files), "state_file": str(state_path) if state_path else "(disabled)",
        "source_prefix": args.source_prefix, "category": args.category,
    })
    progress = Progress(total=len(files))
    if not args.quiet:
        print(f"扫描到 {len(files)} 个文件；模式={'预演' if args.dry_run else '入库'}；"
              f"向量化={'开' if args.index else '关'}；状态文件={state_path or '(已禁用)'}")
    interrupted = False
    try:
        for idx, file in enumerate(files):
            root = next((r for r in roots if str(file).startswith(str(r.resolve()))), roots[0])
            await _import_one(file, root, args, state, progress, report)
            progress.done = idx + 1
            if args.batch_size and (idx + 1) % args.batch_size == 0:
                state.save()
                if not args.quiet:
                    print(f"  -- 已落盘状态（{idx + 1}/{len(files)}，batch={args.batch_size}）")
    except KeyboardInterrupt:
        interrupted = True
        print("\n收到中断信号：已保存进度，重跑同一命令即可续传（已完成文件会跳过）")
    finally:
        state.save()

    progress.done = len(files)
    summary = {
        "total": len(files), "created": progress.created, "updated": progress.updated,
        "skipped": progress.skipped, "conflicts": progress.conflicts,
        "failed": progress.failed, "embed_failed": progress.embed_failed,
        "chars": progress.chars, "elapsed_s": round(time.time() - progress.started, 2),
    }
    report["summary"] = summary
    if not args.dry_run:
        try:
            report["db"] = knowledge.stats()
        except Exception as exc:  # noqa: BLE001
            report["db"] = {"error": str(exc)}

    if not args.quiet:
        print("-" * 72)
        print(
            f"完成：新建 {summary['created']} / 覆盖 {summary['updated']} / 跳过 "
            f"{summary['skipped']} / 冲突 {summary['conflicts']} / 失败 {summary['failed']} / "
            f"待向量化重试 {summary['embed_failed']}；耗时 {summary['elapsed_s']}s"
        )
        db = report.get("db") or {}
        if db.get("total") is not None:
            print(f"知识库现状：总 {db.get('total')} 篇（已就绪 {db.get('ready')}，"
                  f"待向量化 {db.get('pending')}，分块 {db.get('chunks')}）")

    if interrupted:
        return 130
    if progress.failed or progress.conflicts:
        return 1
    if args.require_new and not args.dry_run:
        got = progress.created + progress.updated
        if got < args.require_new:
            print(f"[未达标] 本次入库（新建+覆盖）{got} 篇 < --require-new {args.require_new}")
            if got == 0 and progress.skipped:
                print(
                    f"[提示] 本次 {progress.skipped} 篇全部命中续传快路径（内容未变，非失败）；"
                    f"续传轮请去掉 --require-new，或改用 --require-min <库内篇数> 校验总量"
                )
            return 1
        if not args.quiet:
            print(f"[达标] 本次入库 {got} 篇 >= {args.require_new}")
    if args.require_min and not args.dry_run:
        total = int((report.get("db") or {}).get("total") or 0)
        if total < args.require_min:
            print(f"[未达标] 知识库总篇数 {total} < --require-min {args.require_min}")
            return 1
        if not args.quiet:
            print(f"[达标] 知识库总篇数 {total} >= {args.require_min}")
    return 0


# -------------------------------------------------------------- 索引补建 ----

async def run_index_only(args: argparse.Namespace, report: dict) -> int:
    """给所有待向量化文档补建索引（分片 + 进度），返回退出码。"""
    from app.db import cpp_bridge
    from app.services import rag

    batch = max(1, int(args.batch_size or settings.kb_import_batch_size))
    rows = cpp_bridge.query("SELECT id FROM knowledge_doc WHERE status = 0 ORDER BY id")
    ids = [int(r["id"]) for r in rows]
    report.update({"mode": "index_only", "pending": len(ids), "batch_size": batch})
    if not ids:
        print("没有待向量化文档（status=0 为空），无需处理")
        return 0

    print(f"待向量化 {len(ids)} 篇，按每批 {batch} 篇处理…")
    ok = failed = 0
    details: list[dict] = []
    started = time.time()
    for start in range(0, len(ids), batch):
        chunk = ids[start : start + batch]
        result = await rag.build_index(doc_ids=chunk)
        ok += int(result["ok"])
        failed += int(result["failed"])
        details.extend(result["details"])
        print(f"  批次 {start // batch + 1}: {len(chunk)} 篇 -> ok={result['ok']} "
              f"failed={result['failed']}（累计 ok={ok}）")
    report.update({
        "ok": ok, "failed": failed, "details": details,
        "elapsed_s": round(time.time() - started, 2),
    })
    print("-" * 72)
    print(f"索引补建完成：ok={ok} failed={failed} 耗时 {report['elapsed_s']}s")
    if failed:
        print("失败项（前 10）：")
        for d in [x for x in details if x["status"] != "ok"][:10]:
            print(f"  doc={d['doc_id']} status={d['status']} chunks={d['chunks']}")
    return 1 if failed else 0


# ------------------------------------------------------------------ 清理 ----

def run_purge(args: argparse.Namespace, report: dict) -> int:
    """按 source_url 前缀清理（基准数据回滚）。"""
    from app.services import knowledge

    result = knowledge.purge_by_source_prefix(args.purge_source_prefix)
    report.update({"mode": "purge", "prefix": args.purge_source_prefix, **result})
    print(f"清理完成：匹配 {result['matched']} 篇，已删除 {result['deleted']} 篇")

    # 同步清掉状态文件里同前缀的记录，避免下次误判为"已导入"
    if args.state:
        state_path = Path(args.state).expanduser()
        if not state_path.is_absolute():
            state_path = _BACKEND_DIR / state_path
        state = State.load(state_path)
        removed = [k for k in list(state.files) if k.startswith(args.purge_source_prefix)]
        for k in removed:
            state.files.pop(k, None)
        if removed:
            state.save()
            print(f"状态文件已同步移除 {len(removed)} 条记录")
    return 0


# ------------------------------------------------------------------ 入口 ----

def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。"""
    p = argparse.ArgumentParser(
        prog="python -m app.cli.kb_import",
        description="B17 知识库批量导入管道（Markdown / HTML / PDF / txt）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("**用法**")[-1][:1200],
    )
    p.add_argument("paths", nargs="*", help="文件或目录（目录递归扫描）")
    p.add_argument("--glob", default=",".join(SUPPORTED_EXT),
                   help=f"目录扫描的扩展名（逗号分隔，默认 {','.join(SUPPORTED_EXT)}）")
    p.add_argument("--category", default="", help="统一分类；留空则按子目录名推断")
    p.add_argument("--source-prefix", default="", help="source_url 前缀（如 samples/、bench/）")
    p.add_argument("--index", action="store_true", help="入库后立即向量化（默认关闭，先灌库更快）")
    p.add_argument("--no-index", dest="index", action="store_false", help="只入库不向量化（默认）")
    p.add_argument("--batch-size", type=int, default=0,
                   help=f"每批落盘/索引篇数（默认 {settings.kb_import_batch_size}）")
    p.add_argument("--force", action="store_true", help="内容变化时覆盖同来源文档")
    p.add_argument("--dry-run", action="store_true", help="只解析不入库（无需数据库）")
    p.add_argument("--limit", type=int, default=None, help="最多处理 N 个文件")
    p.add_argument("--state", default=DEFAULT_STATE, help=f"状态文件（默认 {DEFAULT_STATE}）")
    p.add_argument("--no-state", action="store_true", help="不使用状态文件（每次全量重新判定）")
    p.add_argument("--reset-state", action="store_true", help="先清空状态文件再开始")
    p.add_argument("--report", default="", help="导出 JSON 报告到该路径")
    p.add_argument("--require-min", type=int, default=0,
                   help="结束时知识库总篇数低于该值则退出码 1（B17 硬指标闸门）")
    p.add_argument("--require-new", type=int, default=0,
                   help="本次新建+覆盖篇数低于该值则退出码 1（批量导入能力闸门，如 120）")
    p.add_argument("--index-only", action="store_true", help="只给待向量化文档补建索引")
    p.add_argument("--purge-source-prefix", default="", help="按来源前缀批量删除已入库文档")
    p.add_argument("--json", action="store_true", help="以 JSON 输出（CI 用，隐含 --quiet）")
    p.add_argument("--quiet", action="store_true", help="静默：只输出错误与汇总")
    p.set_defaults(index=False)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    """CLI 主函数（返回退出码）。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.json:
        args.quiet = True
    if args.no_state:
        args.state = ""
    if not args.batch_size:
        args.batch_size = max(1, int(settings.kb_import_batch_size))

    report: dict[str, Any] = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "host_db": f"{settings.db_host}:{settings.db_port}/{settings.db_name}",
        "results": [],
        "failed": [],
        "conflicts": [],
    }
    try:
        if args.purge_source_prefix:
            ensure_db()
            code = run_purge(args, report)
        elif args.index_only:
            ensure_db()
            code = asyncio.run(run_index_only(args, report))
        else:
            if not args.paths:
                parser.error("请给出至少一个文件/目录，或使用 --index-only / --purge-source-prefix")
            if not args.dry_run:
                ensure_db()
            code = asyncio.run(run_import(args, report))
    except KeyboardInterrupt:
        print("\n已中断")
        code = 130
    except Exception as exc:  # noqa: BLE001 - 兜底：环境/数据库错误
        print(f"[错误] {type(exc).__name__}: {exc}")
        report["error"] = f"{type(exc).__name__}: {exc}"
        code = 2

    report["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    report["exit_code"] = code
    if args.report:
        out = Path(args.report).expanduser()
        if not out.is_absolute():
            out = _BACKEND_DIR / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告已写入 {out}")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
