"""`C20` 实证报告：引用真实性闸门可用吗？「文本覆盖率拒答」可用吗？

背景
----
`C14` 基线实测：**负样本误命中率 100%** —— 知识库里根本没有的问题，系统仍返回 top-3 文档。
`C20` 原本想用一道**不依赖 embedding 的文本相关性闸门**（问题的字符 n-gram 在检索结果
里的覆盖率）来拒答。

**实测结论（本脚本产出）：该闸门不可用。**
用 `C14` 基线的 27 题（正 25 / 负 2）＋ 真实 `knowledge_doc` 正文逐题算覆盖率：
**正/负样本区间完全重叠，不存在可分阈值**（`n=2`：正 0.000~0.600 / 负 0.067~0.273；
`n=1` 更糟，负样本反而更高）。根因：**提问口语化、文档书面化，字面重合度不是相关性的
可靠代理**——这恰恰是系统要用 embedding 的原因。

→ 因此 `should_refuse` 改为**只按相似度分数**判定；覆盖率/句子依据降为**默认关闭的诊断信号**。

用法
----
    cd <repo>
    $env:XJT_DB_PASSWORD='<your-local-password>'; $env:XJT_DB_PORT='3307'
    python tools/verify_citation_check.py

本脚本产出两份证据：

1. **标定块**：复查“覆盖率闸门不可分”这个结论（含决定性反例）
2. **验证块**：验证真正可用的闸门 —— ①引用真实性（纯结构校验）②拒答只按分数

退出码：0 = 引用闸门全部通过且拒答闸门三态正常；1 = 有断言失败；2 = 环境不可用。
"""

from __future__ import annotations

import json
import os
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
from app.services.citation_check import (  # noqa: E402
    check_citations,
    clean_answer,
    ngram_coverage,
)

BASELINE = _ROOT / "ai" / "eval" / "baselines" / "rag_baseline_20260912.json"


def _load_docs() -> dict[str, str]:
    """title -> content（只取未下架文档）。"""
    rows = cpp_bridge.query(
        "SELECT title, content FROM knowledge_doc WHERE status != 2", []
    )
    return {str(r["title"]): str(r.get("content") or "") for r in rows}


def _means(rows: list[dict], docs: dict[str, str], n: int) -> list[dict]:
    out: list[dict] = []
    for r in rows:
        got = [t for t in (r.get("got_titles") or []) if t]
        text = "\n".join(docs.get(t, "") for t in got)
        out.append(
            {
                "id": r.get("id"),
                "question": r.get("question"),
                "got": got,
                "n_hits": r.get("n_hits"),
                "cov": ngram_coverage(str(r.get("question") or ""), text, n=n),
            }
        )
    return out


def _best_threshold(pos: list[float], neg: list[float]) -> tuple[float, float]:
    """在候选阈值里挑使「正样本全通过、负样本全拒绝」且间隔最大的那个。"""
    best = (0.0, -1.0)
    for i in range(5, 96):
        t = i / 100.0
        if not pos or not neg:
            break
        if min(pos) >= t > max(neg):
            margin = min(pos) - max(neg)
            if margin > best[1]:
                best = (t, margin)
    return best


def main() -> int:
    if not BASELINE.is_file():
        print(f"[NG] 缺少 C14 基线文件：{BASELINE}")
        return 2
    if cpp_bridge.jt_db is None:
        print("[NG] jt_db C++ 扩展不可用（未编译/未拷入 backend/app/db/native/）")
        return 2
    try:
        cpp_bridge.init_db(
            os.environ["XJT_DB_HOST"],
            int(os.environ["XJT_DB_PORT"]),
            os.environ.get("XJT_DB_USER", "root"),
            os.environ.get("XJT_DB_PASSWORD", ""),
            os.environ.get("XJT_DB_NAME", "xiaojietong"),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[NG] 数据库连接失败：{exc}")
        return 2

    docs = _load_docs()
    payload = json.loads(BASELINE.read_text(encoding="utf-8"))
    rows = payload.get("rows") or []
    if not rows:
        print("[NG] 基线里没有 rows")
        return 2

    pos_rows = [r for r in rows if r.get("expected_titles")]
    neg_rows = [r for r in rows if not r.get("expected_titles")]

    print("=" * 86)
    print("C20 文本相关性闸门 · 阈值标定（真实基线 + 真实知识库正文）")
    print("=" * 86)
    print(f"知识库文档：{len(docs)} 篇 ｜ 基线题：{len(rows)}（正样本 {len(pos_rows)} / 负样本 {len(neg_rows)}）")

    pos2 = _means(pos_rows, docs, 2)
    neg2 = _means(neg_rows, docs, 2)
    pos1_by_id = {x["id"]: x["cov"] for x in _means(pos_rows, docs, 1)}
    neg1_by_id = {x["id"]: x["cov"] for x in _means(neg_rows, docs, 1)}
    neg2_ids = {x["id"] for x in neg2}

    print("\n---- 逐题覆盖率 ----")
    for x in pos2 + neg2:
        tag = "负" if x["id"] in neg2_ids else "正"
        u = pos1_by_id.get(x["id"], neg1_by_id.get(x["id"], 0.0))
        print(f"  [{tag}] {str(x['id']):>4} bigram={x['cov']:.3f} unigram={u:.3f}  {str(x['question'])[:26]}")

    pv = [x["cov"] for x in pos2]
    nv = [x["cov"] for x in neg2]
    print("\n---- 可分性判定（n=2 bigram）----")
    print(f"  正样本 cov：min={min(pv):.3f}  max={max(pv):.3f}")
    print(f"  负样本 cov：min={min(nv):.3f}  max={max(nv):.3f}")
    t, margin = _best_threshold(pv, nv)
    coverage_rejected = margin < 0
    if coverage_rejected:
        worst = min(pos2, key=lambda x: x["cov"])
        worst_neg = max(neg2, key=lambda x: x["cov"])
        print("  [结论] **不可分** —— 覆盖率不能当拒答闸门")
        print(f"         决定性反例：正样本 {worst['id']}「{str(worst['question'])[:16]}」"
              f" cov={worst['cov']:.3f}")
        print(f"                     低于负样本 {worst_neg['id']} cov={worst_neg['cov']:.3f}")
        pv1 = list(pos1_by_id.values())
        nv1 = list(neg1_by_id.values())
        print(f"         unigram 更糟：负样本 min={min(nv1):.3f} > 正样本 min={min(pv1):.3f}")
        print("         根因：提问口语化 / 文档书面化，字面重合度不是相关性的可靠代理")
        print("         → 拒答必须依赖**相似度分数**；阈值需用**记录了 score 的**评测重测")
    else:
        print(f"  [结论] 可分：T={t:.2f}（间隔 {margin:.3f}）")

    # ---------------- 验证①：引用真实性闸门（结构校验，可靠） ----------------
    print("\n" + "=" * 86)
    print("验证①：引用真实性闸门（纯结构校验，不靠字面猜测）")
    print("=" * 86)
    real_title = next((t for r in pos_rows for t in (r.get("got_titles") or []) if t), "")
    if not real_title:
        real_title = next(iter(docs), "")
    srcs = [
        {"title": real_title, "content": docs.get(real_title, "")},
        {"title": "另一篇文档", "content": "另一篇的内容。"},
    ]
    checks: list[tuple[str, bool]] = []
    good_ans = f"相关内容见[{real_title}]。"
    rep_good = check_citations(good_ans, srcs)
    checks.append(("真实标题引用 → 判定有效", len(rep_good.valid) == 1 and not rep_good.fabricated))
    checks.append(("真实标题引用 → 清洗后不变（反向对照）", clean_answer(good_ans, rep_good) == good_ans))

    bad_ans = f"相关内容见[{real_title}]，另见[不存在的文档]。"
    rep_bad = check_citations(bad_ans, srcs)
    checks.append(("伪造标题引用 → 被标为 fabricated", len(rep_bad.fabricated) == 1))
    cleaned = clean_answer(bad_ans, rep_bad)
    checks.append(("伪造引用被剔除且正文保留", "不存在的文档" not in cleaned and real_title in cleaned))

    rep_idx = check_citations("见[1]与[9]。", srcs)
    checks.append(("序号引用越界被标伪造", [c.value for c in rep_idx.fabricated] == ["9"]))

    for name, ok in checks:
        print(f"  {'[OK]' if ok else '[NG]'} {name}")

    # ---------------- 验证②：拒答闸门（只按分数） ----------------
    print("\n" + "=" * 86)
    print("验证②：拒答闸门（只按相似度分数；无分数时不拒答）")
    print("=" * 86)
    base = [{"title": real_title, "content": docs.get(real_title, "")}]
    r_hi = check_citations("x", [dict(base[0], score=0.90)], question="图书馆几点关门")
    r_lo = check_citations("x", [dict(base[0], score=0.10)], question="图书馆几点关门")
    r_no = check_citations("x", base, question="图书馆几点关门")
    gate_ok = r_hi.refused is False and r_lo.refused is True and r_no.refused is False
    print(f"  score=0.90 → refused={r_hi.refused}")
    print(f"  score=0.10 → refused={r_lo.refused}  ({r_lo.reason})")
    print(f"  无 score   → refused={r_no.refused}  ({r_no.reason})")
    print(f"  {'[OK]' if gate_ok else '[NG]'} 三态行为符合预期")

    cite_fail = sum(1 for _, ok in checks if not ok)
    verdict = 0 if (cite_fail == 0 and gate_ok) else 1
    print("\n" + "=" * 86)
    print(f"[{'PASS' if verdict == 0 else 'FAIL'}] 引用闸门 {len(checks) - cite_fail}/{len(checks)} 通过"
          f" ｜ 拒答闸门 {'OK' if gate_ok else 'NG'}")
    if coverage_rejected:
        print("结论：**覆盖率闸门已被实测否掉**（正/负样本不可分）——已记入"
              "citation_check.should_refuse 的注释与单测；拒答留待用「记录了 score 的评测」重测阈值。")
    print("=" * 86)
    return verdict


if __name__ == "__main__":
    raise SystemExit(main())
