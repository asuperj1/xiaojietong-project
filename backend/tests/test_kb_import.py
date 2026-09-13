"""B17 批量导入管道测试。

- 纯函数部分（扫描/来源键/分类推断）与 CLI 预演（dry-run）**不需要数据库**；
- 入库 / 去重 / 冲突 / 回滚的集成用例依赖真实数据库，无环境自动 skip（见 conftest）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.cli import kb_import
from app.services.parser import parse_bytes


def _write_fixtures(root: Path) -> None:
    """构造 3 个可解析文件 + 1 个应被忽略的 .docx。"""
    (root / "图书馆").mkdir(parents=True, exist_ok=True)
    (root / "图书馆" / "借阅规则.md").write_text(
        "# 图书馆借阅规则\n本科生可借 10 册，借期 30 天。\n", encoding="utf-8"
    )
    (root / "办事流程").mkdir(parents=True, exist_ok=True)
    (root / "办事流程" / "校园卡补办.html").write_text(
        "<html><head><title>校园卡补办</title></head><body><p>先挂失再补办。</p></body></html>",
        encoding="utf-8",
    )
    (root / "后勤").mkdir(parents=True, exist_ok=True)
    (root / "后勤" / "报修说明.txt").write_text("宿舍报修说明\n小程序提交报修。\n", encoding="utf-8")
    (root / "忽略我.docx").write_bytes(b"not supported")


def test_scan_files_filters_extensions_and_orders(tmp_path):
    _write_fixtures(tmp_path)
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "脏文件.md").write_text("should be skipped", encoding="utf-8")

    files = kb_import.scan_files([str(tmp_path)], [".md", ".html", ".txt"])
    paths = [str(f) for f in files]
    assert paths == sorted(paths)                       # 稳定排序（按完整路径，续传日志可比对）
    assert len(files) == 3                              # .docx 与 __pycache__ 被排除
    assert all(f.suffix != ".docx" for f in files)
    assert not any("__pycache__" in p for p in paths)


def test_source_key_and_category_inference(tmp_path):
    (tmp_path / "图书馆").mkdir()
    f = tmp_path / "图书馆" / "借阅规则.md"
    f.write_text("# x", encoding="utf-8")

    assert kb_import.source_key(tmp_path, f, "samples/") == "samples/图书馆/借阅规则.md"
    assert kb_import.source_key(tmp_path, f, "") == "图书馆/借阅规则.md"
    assert kb_import.infer_category(tmp_path, f, "") == "图书馆"
    assert kb_import.infer_category(tmp_path, f, "指定分类") == "指定分类"


def test_cli_dry_run_reports_without_database(tmp_path):
    _write_fixtures(tmp_path)
    report_path = tmp_path / "report.json"

    code = kb_import.main([
        str(tmp_path), "--dry-run", "--no-state", "--report", str(report_path),
    ])
    assert code == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["files"] == 3
    assert report["dry_run"] is True
    assert len(report["results"]) == 3
    assert {r["action"] for r in report["results"]} == {"dry_run"}
    assert {r["fmt"] for r in report["results"]} == {"markdown", "html", "text"}
    assert report["summary"]["failed"] == 0
    assert "db" not in report                          # 预演不查库


def test_cli_empty_dir_returns_2(tmp_path, capsys):
    code = kb_import.main([str(tmp_path), "--dry-run", "--no-state"])
    assert code == 2
    assert "未找到可解析文件" in capsys.readouterr().out


def test_cli_state_file_written(tmp_path):
    _write_fixtures(tmp_path)
    state = tmp_path / "state.json"
    code = kb_import.main([
        str(tmp_path), "--dry-run", "--state", str(state),
    ])
    assert code == 0
    data = json.loads(state.read_text(encoding="utf-8"))
    assert data["version"] == 1 and data["count"] == 3
    entry = next(iter(data["files"].values()))
    assert len(entry["sha256"]) == 64 and entry["fmt"] in {"markdown", "html", "text"}


def test_ingest_dedup_conflict_and_purge(client):
    """入库 / 去重 / 冲突 / 覆盖 / 回滚的集成用例。

    依赖真实数据库（`client` 夹具已做可用性判定，不可用则整体 skip）；
    ``index=False`` 刻意不触发 embedding，保证不依赖 Ollama。

    **隔离性**：来源键带随机后缀（每次运行互不干扰），且清理放在 ``finally``——
    否则用例中途失败会把残留文档留给下一次运行，导致"内容已变化 → conflict"的假失败。
    """
    from uuid import uuid4

    from app.services import knowledge

    prefix = f"pytest/{uuid4().hex[:8]}/"
    src = prefix + "规则.md"
    try:
        doc = parse_bytes("规则.md", "# 续传测试\n内容第一版。\n".encode("utf-8"))
        first = asyncio.run(
            knowledge.ingest_document(doc, category="pytest", source_url=src, index=False, dedup=True)
        )
        assert first["action"] == "created" and first["status"] == "pending"

        second = asyncio.run(
            knowledge.ingest_document(doc, category="pytest", source_url=src, index=False, dedup=True)
        )
        assert second["action"] == "skipped" and second["doc_id"] == first["doc_id"]

        changed = parse_bytes("规则.md", "# 续传测试\n内容第二版。\n".encode("utf-8"))
        conflict = asyncio.run(
            knowledge.ingest_document(changed, category="pytest", source_url=src, index=False, dedup=True)
        )
        assert conflict["action"] == "conflict"

        overwritten = asyncio.run(
            knowledge.ingest_document(
                changed, category="pytest", source_url=src, index=False, dedup=True, overwrite=True
            )
        )
        assert overwritten["action"] == "updated"

        listed, total = knowledge.list_docs(keyword="续传测试", size=50)
        assert total >= 1
        mine = [r for r in listed if r["id"] == first["doc_id"]]
        assert mine, f"列表未包含本次入库文档：listed={[(r['id'], r['title']) for r in listed]}"
        assert isinstance(mine[0]["id"], int) and isinstance(mine[0]["status"], int)  # 契约：数字而非字符串
        assert mine[0]["status"] == 0                                                 # index=False 应为待向量化

        purged = knowledge.purge_by_source_prefix(prefix)
        assert purged["deleted"] >= 1
        assert knowledge.find_by_source(src) is None
    finally:
        knowledge.purge_by_source_prefix(prefix)      # 兜底：失败也不留残留
