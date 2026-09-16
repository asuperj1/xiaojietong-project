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
