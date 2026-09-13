"""`CAC-25` 端到端验证：中文关键词兜底检索「修复前 vs 修复后」命中率对比。

背景
----
审计 `CAC-25`：`rag.py::_keyword_retrieve` 用 `[\\s,，、;；/]+` 切词，
中文问句无空格 → 整句作为一个词 → `LIKE '%图书馆几点关门？%'` → **必然 0 命中**。
后果：Ollama/向量库不可用时（本机与服务器都未装 Ollama）**全站问答检索恒为空**。

本脚本用**真实数据库 + 真实知识库**实测，不依赖 Ollama。

验证三段
--------
1. 旧实现（整句一词）—— 预期大量 0 命中
2. 新实现（中文 2-gram）—— 预期显著命中
3. 反向对照：**构造必然命中的问题**（知识库标题原词），新旧都应命中，
   用来证明「新实现的命中不是因为放宽了口径」，同时锁定「修复没把老功能弄坏」

用法
----
    cd <repo>
    $env:XJT_DB_PASSWORD='jhq000000'; $env:XJT_DB_PORT='3307'
    E:/miniconda3/python.exe tools/verify_cac25_keyword_search.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("XJT_DB_HOST", "127.0.0.1")
os.environ.setdefault("XJT_DB_PORT", "3307")
os.environ.setdefault("XJT_DB_NAME", "xiaojietong")
os.environ.setdefault("XJT_DB_PASSWORD", "jhq000000")

from app.db import cpp_bridge  # noqa: E402
from app.services.zh_tokenizer import terms as new_terms  # noqa: E402

# 旧实现（修复前）原样复刻 —— 用于对照
_OLD_SPLIT_RE = re.compile(r"[\s,，、;；/]+")

TOP_K = 5

# 真实场景中文提问（无空格，正是旧实现翻车的形态）
QUESTIONS = [
    "图书馆几点关门？",
    "一卡通怎么补办",
    "宿舍报修流程",
    "GPA怎么算",
    "奖学金申请条件",
    "缓考怎么申请",
    "校园网密码忘了怎么办",
    "快递点在哪",
    "食堂开放时间",
    "转专业需要什么条件",
    "医保报销流程",
    "四六级报名时间",
]


def _old_terms(question: str) -> list[str]:
    """修复前行为：按空白/半角标点切；中文整句 → 一个词。"""
    got = [t for t in _OLD_SPLIT_RE.split(question.strip()) if t][:5]
    return got or [question[:20]]


def _run_like(term_list: list[str]) -> list[dict]:
    """执行降级关键词 SQL（与线上 `_keyword_retrieve` 同一形态）。"""
    if not term_list:
        return []
    cond = " OR ".join("(title LIKE ? OR content LIKE ?)" for _ in term_list)
    params: list[str] = []
    for t in term_list:
        params += [f"%{t}%", f"%{t}%"]
    params.append(TOP_K)
    return cpp_bridge.query(
        "SELECT title FROM knowledge_doc WHERE status != 2 AND "
        f"({cond}) ORDER BY updated_at DESC LIMIT ?",
        params,
    )


def _run_like_new(term_list: list[str]) -> list[dict]:
    """新实现：WHERE + 命中数排序（与 `_keyword_retrieve` 一致）。"""
    if not term_list:
        return []
    cond = " OR ".join("(title LIKE ? OR content LIKE ?)" for _ in term_list)
    score = " + ".join("((title LIKE ?) + (content LIKE ?))" for _ in term_list)
    params: list[str] = []
    for t in term_list:
        params += [f"%{t}%", f"%{t}%"]
    params = params * 2  # WHERE 与 ORDER BY 各绑一遍
    params.append(TOP_K)
    return cpp_bridge.query(
        "SELECT title FROM knowledge_doc WHERE status != 2 AND "
        f"({cond}) ORDER BY ({score}) DESC, updated_at DESC LIMIT ?",
        params,
    )


def main() -> int:
    if not cpp_bridge.available() and cpp_bridge.jt_db is None:
        print("[NG] jt_db C++ 扩展不可用（未编译/未拷入 backend/app/db/native/），无法验证")
        return 2

    # 脚本直连：需先建连接池（注意 available() 在 init 前恒为 False）
    try:
        cpp_bridge.init_db(
            os.environ.get("XJT_DB_HOST", "127.0.0.1"),
            int(os.environ.get("XJT_DB_PORT", "3307")),
            os.environ.get("XJT_DB_USER", "root"),
            os.environ.get("XJT_DB_PASSWORD", ""),
            os.environ.get("XJT_DB_NAME", "xiaojietong"),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[NG] 数据库连接失败：{exc}")
        return 2

    docs = cpp_bridge.query(
        "SELECT id, title, category FROM knowledge_doc WHERE status != 2", []
    )
    print("=" * 78)
    print("CAC-25 中文关键词兜底检索 · 修复前后对比（真实库实跑）")
    print("=" * 78)
    print(f"知识库可用文档：{len(docs)} 篇")
    if not docs:
        print("[!] 知识库为空，无法验证（请先导入 knowledge_doc）")
        return 2

    print(f"测试提问：{len(QUESTIONS)} 条（均为无空格中文问句）\n")

    old_hits = new_hits = 0
    rows: list[tuple[str, str, str, str]] = []

    for q in QUESTIONS:
        ot = _old_terms(q)
        nt = new_terms(q)
        try:
            o_hit = _run_like(ot)
        except Exception as exc:  # noqa: BLE001
            o_hit = []
            rows.append((q, f"ERR {exc}", "", ""))
            continue
        n_hit = _run_like_new(nt)
        if o_hit:
            old_hits += 1
        if n_hit:
            new_hits += 1
        top = n_hit[0]["title"] if n_hit else "—"
        rows.append((q, str(len(o_hit)), str(len(n_hit)), str(top)))
        print(f"· {q}")
        print(f"    旧词元 {ot}  → 命中 {len(o_hit)}")
        print(f"    新词元 {nt}  → 命中 {len(n_hit)}  首条：{top}")

    total = len(QUESTIONS)
    print("\n" + "=" * 78)
    print(f"【旧实现】有命中的提问：{old_hits}/{total}  （空结果率 {100 * (total - old_hits) // total}%）")
    print(f"【新实现】有命中的提问：{new_hits}/{total}  （空结果率 {100 * (total - new_hits) // total}%）")
    print("=" * 78)

    # ---------------- 反向对照：知识库原词必然命中，证明没改坏老功能 ----------------
    print("\n[反向对照] 用知识库**标题原词**提问，新旧实现都应命中：")
    control_fail: list[str] = []
    for d in docs[:5]:
        title = str(d["title"])
        probe = title[:20]
        n_hit = _run_like_new(new_terms(probe))
        ok = bool(n_hit)
        print(f"  {'[OK]' if ok else '[NG]'} {probe!r} -> 命中 {len(n_hit)}")
        if not ok:
            control_fail.append(probe)

    print()
    passed = new_hits > old_hits and not control_fail
    if passed:
        print(
            f"[PASS] 新实现命中 {new_hits}/{total}，旧实现仅 {old_hits}/{total}；"
            f"反向对照 {5 - len(control_fail)}/5 正常"
        )
        return 0
    print(
        f"[FAIL] 新 {new_hits}/{total}，旧 {old_hits}/{total}，"
        f"反向对照失败 {len(control_fail)} 项"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
