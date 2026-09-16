"""`C31` 工具链测试（**全程离线**，不连数据库、不调模型）。

覆盖四条链路各自的边界，重点在"会静默出错"的地方：
- 采集：非文本文件 / 空文件不得变成脏样本
- 去重：重复公告必须被丢掉（否则污染 `C32` 的一致性统计）
- 预标注：**不得覆盖人工成果**（重跑预标注是常事，覆盖了就丢数据）
- 导出：空 labels 的样本不得进训练集（会教模型输出空字段）

运行：
```bash
python -m pytest ai/dataset/tests -q
```
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]        # tests -> dataset -> ai -> repo
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ai.dataset.collect import collect_from_dir, dedupe, summary  # noqa: E402
from ai.dataset.export import (  # noqa: E402
    build_messages,
    export_labeling_csv,
    export_training_jsonl,
    stats_markdown,
)
from ai.dataset.prelabel import KeywordTagger, NullLabeler, prelabel_samples, resolve_labeler  # noqa: E402
from ai.dataset.schema import (  # noqa: E402
    Sample,
    make_sample,
    read_jsonl,
    validate_sample,
    write_jsonl,
)


# ---------------------------------------------------------------- schema

def test_valid_sample_has_no_problems() -> None:
    s = make_sample("9 月 30 日前提交材料", source_type="notice", ref="1")
    assert validate_sample(s) == []


def test_validate_catches_each_kind_of_problem() -> None:
    assert validate_sample(Sample(id="a", text="", source={"type": "notice"}))   # 空正文
    assert validate_sample(Sample(id="", text="x", source={"type": "notice"}))  # 空 id
    assert validate_sample(Sample(id="a", text="x", source={"type": "weird"}))  # 来源类型非法
    assert validate_sample(Sample(id="a", text="x", source={"type": "notice"},
                                 annotation={"status": "bogus"}))                # 状态非法

    bad_imp = make_sample("x", source_type="notice", ref="1")
    bad_imp.labels = {"importance": 9}
    assert any("importance" in p for p in validate_sample(bad_imp))

    bad_ent = make_sample("x", source_type="notice", ref="1")
    bad_ent.labels = {"entities": [{"type": "unknown", "text": "x"}]}
    assert any("entities" in p for p in validate_sample(bad_ent))


def test_sample_roundtrip_jsonl(tmp_path: Path) -> None:
    s = make_sample("正文内容", source_type="file", ref="a.md")
    s.labels = {"importance": 3}
    out = tmp_path / "s.jsonl"
    assert write_jsonl(out, [s]) == 1
    back = read_jsonl(out)
    assert back[0].id == s.id
    assert back[0].labels["importance"] == 3


# ---------------------------------------------------------------- collect

def test_collect_from_dir_skips_non_text_and_empty(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("图书馆开放时间 8:00-22:00", encoding="utf-8")
    (tmp_path / "b.txt").write_text("宿舍安全用电检查通知", encoding="utf-8")
    (tmp_path / "empty.txt").write_text("   \n  ", encoding="utf-8")
    (tmp_path / "blob.bin").write_bytes(b"\xff\xfe\x00\x01")

    rows = collect_from_dir(tmp_path)
    assert len(rows) == 2
    assert {r.source["ref"] for r in rows} == {"a.md", "b.txt"}
    assert all(validate_sample(r) == [] for r in rows)


def test_dedupe_drops_repeated_text() -> None:
    a = make_sample("同一段文字", source_type="file", ref="1")
    b = make_sample("同一段文字", source_type="notice", ref="2")   # 同文不同源
    c = make_sample("另一段文字", source_type="file", ref="3")
    kept, dropped = dedupe([a, b, c])
    assert [k.id for k in kept] == [a.id, c.id]
    assert dropped == 1


def test_summary_counts_by_source_type() -> None:
    rows = [
        make_sample("甲", source_type="notice", ref="1"),
        make_sample("乙", source_type="file", ref="2"),
        make_sample("丙", source_type="file", ref="3"),
    ]
    info = summary(rows)
    assert info["total"] == 3
    assert info["by_source_type"] == {"notice": 1, "file": 2}


# ---------------------------------------------------------------- prelabel

def test_keyword_tagger_marks_candidates() -> None:
    s = make_sample(
        "请于9月30日前到行政楼办理，教务处负责受理。", source_type="notice", ref="1"
    )
    out = prelabel_samples([s])
    ents = out[0].labels["entities"]
    types = {e["type"] for e in ents}
    assert {"time", "place", "org"} <= types
    assert all(e["norm"] is None for e in ents), "归一化是 C27 的职责，预标注不得代劳"


def test_prelabel_must_not_override_human_work() -> None:
    """重跑预标注是常事 —— 一旦覆盖人工标注就是数据丢失。

    实现口径：`human` / `reviewed` 的样本被**整个跳过**（连候选实体也不补），
    这是比"只填空位"更强的保证：预标注永远不会碰到人工样本的任何字段。
    """
    s = make_sample("9月30日截止", source_type="notice", ref="1")
    s.annotation = {"status": "human"}
    s.labels = {"deadline": "2026-09-30"}
    out = prelabel_samples([s], KeywordTagger())[0]
    assert out.labels == {"deadline": "2026-09-30"}      # 一字未改
    assert out.status == "human"                          # 状态也不被改回 prelabeled


def test_prelabel_fills_only_empty_slots() -> None:
    s = make_sample("9月30日截止", source_type="notice", ref="1")
    s.labels = {"importance": 5}
    out = prelabel_samples([s], NullLabeler())[0]
    assert out.labels["importance"] == 5      # 已有值保留
    assert out.status == "prelabeled"


def test_resolve_labeler_variants() -> None:
    assert getattr(resolve_labeler("keyword"), "name") == "keyword"
    assert getattr(resolve_labeler("null"), "name") == "null"
    assert callable(resolve_labeler("ai.dataset.prelabel:KeywordTagger"))
    try:
        resolve_labeler("no-colon-here")
    except ValueError as exc:
        assert "labeler" in str(exc)
    else:                                     # pragma: no cover
        raise AssertionError("非法 labeler 应当抛 ValueError")


# ---------------------------------------------------------------- export

def test_training_export_skips_samples_without_labels(tmp_path: Path) -> None:
    empty = make_sample("只有正文", source_type="file", ref="1")
    labeled = make_sample("有标注", source_type="file", ref="2")
    labeled.labels = {"importance": 5}
    n = export_training_jsonl([empty, labeled], tmp_path / "train.jsonl")
    assert n == 1, "空 labels 的样本会教模型输出空字段，必须过滤"


def test_build_messages_shape_and_drops_empty_fields() -> None:
    s = make_sample("公告正文", source_type="notice", ref="9")
    s.labels = {"importance": 4, "deadline": None, "entities": []}
    msg = build_messages(s)
    assert [m["role"] for m in msg["messages"]] == ["system", "user", "assistant"]
    payload = json.loads(msg["messages"][2]["content"])
    assert payload == {"importance": 4}, payload


def test_labeling_csv_is_excel_friendly(tmp_path: Path) -> None:
    s = make_sample("9月30日截止", source_type="notice", ref="1")
    prelabel_samples([s])
    out = tmp_path / "labeling.csv"
    assert export_labeling_csv([s], out) == 1
    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "无 BOM 的 CSV 在 Excel 里会中文乱码"
    text = raw.decode("utf-8-sig")
    assert "entities" in text.splitlines()[0]


def test_stats_markdown_has_required_sections() -> None:
    s = make_sample("9月30日截止，逾期不再受理", source_type="notice", ref="1")
    prelabel_samples([s])
    md = stats_markdown([s])
    for token in ("数据集统计报告", "标注状态分布", "来源分布", "C32"):
        assert token in md, f"统计报告缺少 {token}"


def test_stats_handles_empty_input() -> None:
    md = stats_markdown([])
    assert "0" in md        # 空数据集不得抛异常（CLI 会先跑 collect）


# ------------------------------- 回归锁：None 泄漏与 id 稳定性（PR #92 审查）
#
# 下面几条是 PR #92 审查（2026-09-16）指出的缺陷的回归锁。
# 它们当初全部逃过了本文件的 15 个测试，原因不是"测得少"，而是**选材恰好避开触发条件**：
#   · test_prelabel_fills_only_empty_slots 用的是 NullLabeler（返回 {}），
#     而真正会写 None 的是 KeywordTagger ⇒ 「只填空位」逻辑在危险输入下从未被执行；
#   · test_build_messages_shape_and_drops_empty_fields 传的是 entities=[]，
#     而缺陷出现在 entities=[{"norm": None}]。
# 📌 教训：**测试要喂最危险的输入，而不是最干净的输入。**


def test_prelabel_output_passes_own_validation() -> None:
    """端到端闭环：预标注产物必须能通过自己的校验。

    这条断言曾真实失败过 —— `KeywordTagger` 返回
    `{"importance": None, "category": None}`，被"只填空位"逻辑写进了 labels，
    于是 `validate_sample` 报 "importance 必须是 1~5 的整数，当前 None"。
    """
    s = make_sample("请于9月30日前到教务处办理选课，逾期不再受理。",
                    source_type="notice", ref="1")
    out = prelabel_samples([s], KeywordTagger())[0]
    assert validate_sample(out) == [], validate_sample(out)


def test_prelabel_does_not_write_none_keys() -> None:
    """空值不落键：importance/category 由 C28 与人工负责，不该留 None 占位。

    ⚠️ 必须用 `KeywordTagger`（而非 `NullLabeler`）—— 前者才会返回 None，
    后者返回 `{}`，根本压不到这条路径。
    """
    s = make_sample("9月30日截止", source_type="notice", ref="1")
    out = prelabel_samples([s], KeywordTagger())[0]
    assert out.labels.get("entities"), "候选实体仍应被写入"
    # ⚠️ 断言必须是「键不存在」，不能写成 `out.labels.get(key) is None` ——
    # 后者在缺陷版本下**也会通过**（键存在、值恰好是 None），属恒真空断言。
    # 本仓库已多次栽在"断言依赖的事实并不存在"上（'' 学号、$null -like、"39" == 39）。
    for key in ("importance", "category"):
        assert key not in out.labels, \
            f"{key} 不该被写进 labels（当前值 {out.labels.get(key)!r}）"


def test_make_sample_id_is_stable_across_processes() -> None:
    """无 ref 时 id 也必须确定 —— 否则 C32 按 id 配对会失败。

    ⚠️ 必须用**子进程**验证：内置 `hash()` 的随机化只在进程间显现，
    同一进程内两次调用结果相同（这正是该缺陷躲过原有单测的原因）。
    """
    code = (
        "import sys; sys.path.insert(0, r'{repo}');"
        "from ai.dataset.schema import make_sample;"
        "print(make_sample('\\u540c\\u4e00\\u6bb5\\u6587\\u672c').id)"
    ).format(repo=REPO)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    seen = set()
    for _ in range(3):
        cp = subprocess.run([sys.executable, "-c", code], capture_output=True,
                            text=True, encoding="utf-8", errors="replace", env=env)
        assert cp.returncode == 0, cp.stderr
        seen.add(cp.stdout.strip())
    assert len(seen) == 1, f"跨进程 id 不稳定：{seen}"

    # 反向对照：同一进程内也必须稳定
    assert make_sample("同一段文本").id == make_sample("同一段文本").id


def test_make_sample_id_is_stable_with_and_without_ref() -> None:
    """有 ref 时用 ref、无 ref 时用正文哈希，两条路径都必须确定。"""
    assert make_sample("x", source_type="notice", ref="42").id == "notice-42"
    a = make_sample("同样的正文")
    b = make_sample("同样的正文")
    assert a.id == b.id
    assert a.id.startswith("file-"), a.id
    assert make_sample("不同正文").id != a.id, "不同正文必须得到不同 id"


def test_training_target_has_no_nested_none() -> None:
    """训练目标里不得出现 `norm: null`（会教模型输出空字段）。"""
    s = make_sample("到图书馆办理", source_type="notice", ref="1")
    s.labels = {"entities": [{"type": "place", "text": "图书馆", "norm": None}]}
    payload = json.loads(build_messages(s)["messages"][2]["content"])
    assert all("norm" not in e for e in payload["entities"]), payload


def test_entity_norm_kept_when_present() -> None:
    """反向对照：**有**归一化值时必须保留 `norm`，别把该留的一起清掉。"""
    s = make_sample("9月30日截止", source_type="notice", ref="1")
    s.labels = {"entities": [{"type": "time", "text": "9月30日", "norm": "2026-09-30"}]}
    payload = json.loads(build_messages(s)["messages"][2]["content"])
    assert payload["entities"] == [
        {"type": "time", "text": "9月30日", "norm": "2026-09-30"}
    ], payload


def test_entity_cleanup_drops_broken_items_only() -> None:
    """残缺实体（缺 type/text、非对象）不得进训练目标，但不得连累同批其它项。"""
    s = make_sample("混合实体", source_type="notice", ref="1")
    s.labels = {"entities": [
        {"type": "place", "text": "图书馆"},
        {"type": "", "text": "缺类型"},
        {"type": "matter", "text": ""},
        "not-a-dict",
        {"type": "org", "text": "教务处", "norm": None},
    ]}
    payload = json.loads(build_messages(s)["messages"][2]["content"])
    assert payload["entities"] == [
        {"type": "place", "text": "图书馆"},
        {"type": "org", "text": "教务处"},
    ], payload


def test_export_skips_entities_field_when_all_cleaned() -> None:
    """实体全被清空时，该字段应整体不出现。

    否则会训练模型对"没有实体"的样本也吐出 `{"entities": []}`。
    """
    s = make_sample("没有可用实体", source_type="notice", ref="1")
    s.labels = {"category": "通知", "entities": [{"type": "", "text": "残缺"}]}
    payload = json.loads(build_messages(s)["messages"][2]["content"])
    assert payload == {"category": "通知"}, payload
