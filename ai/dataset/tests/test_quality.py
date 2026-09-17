"""`C32` 标注质量校验的单元测试（纯离线，无需数据库/模型）。

覆盖重点
--------
1. `cohen_kappa` 用**手算结果**核对（不是"跑通就行"）
2. 退化与边界：全同值、空配对、长度不等
3. **反向对照**：高一致数据必须显著高于低一致数据 ——
   否则说明断言是"恒真"的，测不出问题
4. 归一化：同一天的不同写法不应被算成分歧
5. 状态过滤：`prelabeled` 不参与一致性统计
6. 实体 P/R/F1 手算核对
7. 划分的**确定性**（同 seed 同结果）与反向（不同 seed 结果不同）
8. 数据卡渲染包含关键小节
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.dataset.datacard import (  # noqa: E402
    build_datacard,
    render_markdown as render_card_md,
    split_samples,
)
from ai.dataset.quality import (  # noqa: E402
    KAPPA_THRESHOLD,
    agreement_report,
    cohen_kappa,
    entity_prf,
    load_labels,
    normalize_deadline,
    pair_by_id,
    quality_summary,
    render_markdown as render_report_md,
)
from ai.dataset.schema import Sample, write_jsonl  # noqa: E402


# ---------------------------------------------------------------- 构造工具


def _sample(sid: str, *, status: str = "human", **labels) -> Sample:
    return Sample(
        id=sid, text="x", source={"type": "notice", "ref": sid},
        labels=labels, annotation={"status": status},
    )


def _write(tmp_path: Path, name: str, samples: list[Sample]) -> Path:
    path = tmp_path / name
    write_jsonl(path, samples)
    return path


# ---------------------------------------------------------------- 1. Kappa 手算


def test_kappa_perfect_agreement_is_one():
    assert cohen_kappa(["a", "b", "a"], ["a", "b", "a"])["kappa"] == pytest.approx(1.0)


def test_kappa_matches_hand_computed_value():
    """手算：(po=0.75, pe=0.5) → kappa = 0.5"""
    stats = cohen_kappa([1, 1, 0, 0], [1, 1, 1, 0])
    assert stats["po"] == pytest.approx(0.75)
    assert stats["pe"] == pytest.approx(0.5)
    assert stats["kappa"] == pytest.approx(0.5)


def test_kappa_hand_computed_second_case():
    """手算：A=[a,a,b,b,b] B=[a,b,b,b,b]

    一致 4/5 ⇒ po = 0.8
    ca: a=2, b=3；cb: a=1, b=4
    pe = (2/5)(1/5) + (3/5)(4/5) = 0.08 + 0.48 = 0.56
    kappa = (0.8 - 0.56) / (1 - 0.56) = 0.24 / 0.44 = 0.5454...
    """
    stats = cohen_kappa(["a", "a", "b", "b", "b"], ["a", "b", "b", "b", "b"])
    assert stats["po"] == pytest.approx(0.8)
    assert stats["pe"] == pytest.approx(0.56)
    assert stats["kappa"] == pytest.approx(0.24 / 0.44, abs=1e-9)


def test_kappa_full_disagreement_is_negative_or_zero():
    """两组完全不重叠 ⇒ Kappa ≤ 0（这是 Kappa 的意义所在：要扣掉随机一致）"""
    stats = cohen_kappa(["a", "a", "b", "b"], ["b", "b", "a", "a"])
    assert stats["kappa"] is not None and stats["kappa"] <= 0.0


# ---------------------------------------------------------------- 2. 边界


def test_kappa_degenerate_all_same_value():
    """双方都只用同一个标签 ⇒ Kappa 无定义，按退化处理"""
    stats = cohen_kappa(["x", "x", "x"], ["x", "x", "x"])
    assert stats["degenerate"] is True
    assert stats["kappa"] == pytest.approx(1.0)


def test_kappa_empty_pairing_returns_none():
    """没有配对时返回 None（不是 0.0）—— 避免「没数据」被读成「一致性极差」。"""
    stats = cohen_kappa([], [])
    assert stats["kappa"] is None
    assert stats["n"] == 0


def test_kappa_length_mismatch_raises():
    with pytest.raises(ValueError):
        cohen_kappa(["a"], ["a", "b"])


# ---------------------------------------------------------------- 3. 反向对照


def test_high_agreement_beats_low_agreement():
    """**反向对照**：高一致数据的 Kappa 必须显著高于低一致数据。

    如果这条不成立，说明前面"通过"的断言可能只是恒真。
    """
    high = cohen_kappa(
        ["奖学金", "活动", "奖学金", "活动", "通知", "通知"],
        ["奖学金", "活动", "奖学金", "活动", "通知", "通知"],
    )["kappa"]
    low = cohen_kappa(
        ["奖学金", "活动", "奖学金", "活动", "通知", "通知"],
        ["活动", "奖学金", "通知", "通知", "活动", "奖学金"],
    )["kappa"]
    assert high is not None and low is not None
    assert high > low
    assert high >= KAPPA_THRESHOLD      # 完全一致必然是 1.0
    assert low < KAPPA_THRESHOLD        # 低一致必须低于阈值 → 检查真的能判失败


def test_agreement_report_flags_failure():
    """低一致数据必须 `passed=False`，并列出分歧样例"""
    pairs = [
        ("s1", {"category": "a"}, {"category": "b"}),
        ("s2", {"category": "b"}, {"category": "a"}),
        ("s3", {"category": "a"}, {"category": "b"}),
        ("s4", {"category": "b"}, {"category": "a"}),
    ]
    st = agreement_report(pairs, "category")
    assert st["passed"] is False
    assert st["disagree"] == 4
    assert len(st["disagreements"]) == 4


# ---------------------------------------------------------------- 4. 归一化


def test_deadline_normalization_ignores_time_part():
    assert normalize_deadline("2026-09-30 23:59:59") == "2026-09-30"
    assert normalize_deadline("2026-09-30T00:00:00") == "2026-09-30"
    assert normalize_deadline("2026-09-30") == "2026-09-30"
    assert normalize_deadline("待定") == "待定"


def test_deadline_same_day_different_format_counts_as_agree():
    pairs = [("s1", {"deadline": "2026-09-30"}, {"deadline": "2026-09-30 23:59:59"})]
    st = agreement_report(pairs, "deadline", normalize=normalize_deadline)
    assert st["disagree"] == 0
    assert st["kappa"] == pytest.approx(1.0)


def test_deadline_different_day_counts_as_disagree():
    pairs = [("s1", {"deadline": "2026-09-30"}, {"deadline": "2026-10-01"})]
    st = agreement_report(pairs, "deadline", normalize=normalize_deadline)
    assert st["disagree"] == 1


# ---------------------------------------------------------------- 5. 状态过滤


def test_load_labels_only_keeps_human_and_reviewed(tmp_path: Path):
    """`prelabeled` 是机器初稿，参与统计会让 Kappa 虚高 ⇒ 必须排除"""
    path = _write(tmp_path, "a.jsonl", [
        _sample("h1", status="human", category="a"),
        _sample("r1", status="reviewed", category="b"),
        _sample("p1", status="prelabeled", category="c"),
        _sample("raw1", status="raw", category="d"),
    ])
    labels = load_labels(path)
    assert set(labels) == {"h1", "r1"}

    labels_all = load_labels(path, only_annotated=False)
    assert set(labels_all) == {"h1", "r1", "p1", "raw1"}


def test_pair_by_id_takes_intersection(tmp_path: Path):
    a = {"s1": {"category": "a"}, "s2": {"category": "b"}}
    b = {"s2": {"category": "b"}, "s3": {"category": "c"}}
    pairs = pair_by_id(a, b)
    assert [p[0] for p in pairs] == ["s2"]


# ---------------------------------------------------------------- 6. 实体 P/R/F1


def test_entity_prf_hand_computed():
    """A 有 {t1,t2}，B 有 {t2,t3} ⇒ TP=1 FP=1 FN=1
    P = 1/2, R = 1/2, F1 = 1/2"""
    pairs = [(
        "s1",
        {"entities": [{"type": "time", "norm": "t1"}, {"type": "time", "norm": "t2"}]},
        {"entities": [{"type": "time", "norm": "t2"}, {"type": "time", "norm": "t3"}]},
    )]
    stats = entity_prf(pairs)
    assert stats["tp"] == 1 and stats["fp"] == 1 and stats["fn"] == 1
    assert stats["precision"] == pytest.approx(0.5)
    assert stats["recall"] == pytest.approx(0.5)
    assert stats["f1"] == pytest.approx(0.5)


def test_entity_prf_perfect_match():
    pairs = [(
        "s1",
        {"entities": [{"type": "time", "norm": "t1"}]},
        {"entities": [{"type": "time", "norm": "t1"}]},
    )]
    assert entity_prf(pairs)["f1"] == pytest.approx(1.0)


def test_entity_prf_type_matters():
    """类型不同 ⇒ 不算命中（只看值会把 time/place 混为一谈）"""
    pairs = [(
        "s1",
        {"entities": [{"type": "time", "norm": "x"}]},
        {"entities": [{"type": "place", "norm": "x"}]},
    )]
    stats = entity_prf(pairs)
    assert stats["tp"] == 0 and stats["fp"] == 1 and stats["fn"] == 1


# ---------------------------------------------------------------- 7. 总报告


def test_quality_summary_end_to_end(tmp_path: Path):
    """完整链路：两份标注文件 → 报告 → 通过判定 + Markdown 渲染"""
    common = [
        _sample(f"s{i}", category="奖学金" if i % 2 else "活动",
                importance=(5 if i % 2 else 3),
                deadline="2026-09-30",
                entities=[{"type": "time", "norm": "2026-09-30"}])
        for i in range(10)
    ]
    path_a = _write(tmp_path, "a.jsonl", common)
    path_b = _write(tmp_path, "b.jsonl", common)      # B 完全照抄 ⇒ 应 100% 一致

    report = quality_summary(path_a, path_b)
    assert report["paired"] == 10
    assert report["passed"] is True
    assert report["min_kappa"] == pytest.approx(1.0)
    assert report["entities"]["f1"] == pytest.approx(1.0)

    md = render_report_md(report)
    assert "Cohen's Kappa" in md
    assert "P/R/F1" in md


def test_quality_summary_fails_on_disagreement(tmp_path: Path):
    """反向对照：给 B 故意改一半的分类 ⇒ 必须判不通过"""
    a = [_sample(f"s{i}", category="奖学金") for i in range(10)]
    b = [_sample(f"s{i}", category=("活动" if i < 5 else "奖学金")) for i in range(10)]
    report = quality_summary(_write(tmp_path, "a.jsonl", a), _write(tmp_path, "b.jsonl", b))
    assert report["passed"] is False
    assert report["fields"]["category"]["disagree"] == 5


# ---------------------------------------------------------------- 8. 划分


def test_split_is_deterministic_and_complete():
    samples = [_sample(f"s{i:03d}", category="a") for i in range(100)]
    first = split_samples(samples, seed=42)
    second = split_samples(samples, seed=42)
    assert {k: [s.id for s in v] for k, v in first.items()} == \
           {k: [s.id for s in v] for k, v in second.items()}

    # 不丢不重
    all_ids = [s.id for v in first.values() for s in v]
    assert sorted(all_ids) == sorted(s.id for s in samples)

    # 比例大致合理
    assert len(first["train"]) == 80
    assert len(first["dev"]) == 10
    assert len(first["test"]) == 10


def test_split_different_seed_gives_different_order():
    samples = [_sample(f"s{i:03d}", category="a") for i in range(100)]
    a = [s.id for s in split_samples(samples, seed=1)["train"]]
    b = [s.id for s in split_samples(samples, seed=2)["train"]]
    assert a != b        # 反向对照：seed 真的起作用了


def test_split_handles_tiny_dataset():
    """样本少于集合数时也不能崩，且不丢样本"""
    samples = [_sample("only-one", category="a")]
    result = split_samples(samples)
    assert sum(len(v) for v in result.values()) == 1


def test_split_empty_dataset():
    result = split_samples([])
    assert all(v == [] for v in result.values())


# ---------------------------------------------------------------- 9. 数据卡


def test_datacard_and_markdown(tmp_path: Path):
    samples = [
        _sample("s1", category="奖学金", importance=5, deadline="2026-09-30",
                entities=[{"type": "time", "norm": "2026-09-30"}]),
        _sample("s2", category="活动", importance=3, status="prelabeled"),
    ]
    split = split_samples(samples, seed=42)
    agreement = {
        "paired": 2, "threshold": 0.8, "min_kappa": 0.85, "passed": True,
        "fields": {"category": {"kappa": 0.85, "agree": 2, "disagree": 0}},
        "entities": {"precision": 1.0, "recall": 1.0, "f1": 1.0},
    }
    card = build_datacard(samples, name="xjt", version="v0", split=split,
                          agreement=agreement)

    assert card["summary"]["total"] == 2
    assert card["summary"]["by_status"] == {"human": 1, "prelabeled": 1}
    assert "splits" in card and "agreement" in card

    md = render_card_md(card)
    for section in ("## 1. 规模", "## 2. 分布", "## 3. 划分", "## 4. 标注质量"):
        assert section in md
    assert "0.85" in md


def test_datacard_json_serializable(tmp_path: Path):
    """数据卡必须能直接 json.dumps（要入库/归档）"""
    card = build_datacard([_sample("s1", category="a")])
    text = json.dumps(card, ensure_ascii=False)
    assert json.loads(text)["name"] == "xiaojietong-extraction"
