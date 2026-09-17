#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C33 合成语料生成器的测试。

原则（沿用本仓 C34/C35 的做法）：**没有模型、没有网络也能验证关键逻辑**，
并且每条关键断言都配**反向对照** —— 证明它不是恒真的。

这里守五件事：
  1. **生成可复现**：同 seed 结果逐字节相同，换 seed 必须变（否则 seed 没生效）；
  2. **标签自检真的会拦**：`validate` 必须能抓出"实体片段不在正文里""没有时间却给了 deadline"
     "importance 越界"等每一类坏标签 —— 否则自检通过就等于自我安慰；
  3. **训练集与留出集隔离**：id、正文都不得与 C34 评测集 / C34+C35 的示例池重合；
  4. **口径跨文件一致**：`REFERENCE_DATE` 必须等于评测集的 `reference_date`，
     `CATEGORIES` 必须等于 `ai/eval/prompt_variants.py::CATEGORIES`
     （这两条一旦漂移，"训练教的东西"和"评测量的事情"就不是一回事了）；
  5. **覆盖矩阵与配比**：类别/重要度/时间表达全覆盖，实体数配比贴近 `ENTITY_MIX`
     （陷阱样本训不足，评测上必然在它们身上扣分）。

⚠️ 用 importlib 按路径加载被测模块，不写 `from ai.dataset import ...`：
与 C34/C35 的测试保持一致，也让本文件不依赖 `ai/` 的包结构。

作者：成员3 · C33
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
DATASET_DIR = TESTS_DIR.parent
EVAL_DIR = DATASET_DIR.parent / "eval"
REPO_ROOT = DATASET_DIR.parent.parent

COUNT = 800


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # ⚠️ 必须先注册进 sys.modules 再 exec：被测模块里有 `@dataclass`，
    # 而 dataclasses 在处理字符串注解时会 `sys.modules.get(cls.__module__)` ——
    # 不注册就拿到 None，报 "'NoneType' object has no attribute '__dict__'"。
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sn = _load(DATASET_DIR / "synth_notice.py", "synth_notice_t")
#: ⚠️ system prompt 的来源是 **C34 的 `extract_bench.SYSTEM_PROMPT`**（已在 dev 里）。
#: 不用 `ai/eval/prompt_variants.py`：那是 C35 的文件（尚在 PR 中未合并），
#: 用它会给 C33 添一个**没必要的跨 PR 依赖**；而 C34 的常量本来就是评测口径的权威出处
#: （`finetuned` 与 `zero-shot` 要求与它逐字节相同）。
eb = _load(EVAL_DIR / "extract_bench.py", "extract_bench_c33test")

EVAL_CASES = json.loads((EVAL_DIR / "extract_cases.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def samples():
    return sn.generate(COUNT)


# ==================================================== 可复现性 ====


def test_same_seed_is_reproducible():
    a = sn.generate(120, seed=7)
    b = sn.generate(120, seed=7)
    assert [x.to_dict() for x in a] == [x.to_dict() for x in b]


def test_different_seed_changes_data():
    """反向对照：换 seed 必须真的变 —— 否则 seed 没接上（那"可复现"就没意义）。"""
    a = sn.generate(120, seed=7)
    b = sn.generate(120, seed=8)
    assert [x.text for x in a] != [x.text for x in b]


def test_count_is_respected_and_texts_unique(samples):
    assert len(samples) == COUNT
    assert len({s.text for s in samples}) == COUNT


@pytest.mark.parametrize("bad", [0, -1])
def test_rejects_nonpositive_count(bad):
    with pytest.raises(ValueError):
        sn.generate(bad)


# ==================================================== 标签自检 ====


def test_all_generated_samples_pass_validation(samples):
    assert sn.validate_all(samples) == []


def _good_sample():
    """取一条**带实体**的好样本。

    ⚠️ 别用 `generate(...)[0]`：那可能是"零实体"类（`deadline=null`）的样本，
    下面那些针对 `entities[0]` 的破坏就无从施加（我第一版正是这么写错的）。
    """
    for s in sn.generate(60, seed=3):
        if s.expected["entities"]:
            return s
    raise AssertionError("生成器没有产出任何带实体的样本")


@pytest.mark.parametrize("mutate,expect_kw", [
    (lambda e, s: e["entities"][0].update({"text": "这个词不在正文里"}), "不在正文中"),
    (lambda e, s: e.update({"deadline": "2026-01-01"}), "不在实体日期"),
    (lambda e, s: e.update({"importance": 2}), "不在"),
    (lambda e, s: e.update({"category": "其他"}), "受控词表"),
    (lambda e, s: e.update({"entities": []}), "实体数"),
    (lambda e, s: e["entities"][0].update({"norm": "2026/09/30"}), "YYYY-MM-DD"),
])
def test_validate_catches_broken_labels(mutate, expect_kw):
    """**反向对照**：把好样本逐类弄坏，`validate` 必须报出来。

    没有这一条，"自检通过"可能只是因为 `validate` 永远返回空列表。
    """
    s = _good_sample()
    # 先确认原样本是好的（否则下面的"弄坏后报错"说明不了任何事）
    assert sn.validate(s) == []
    mutate(s.expected, s)
    problems = sn.validate(s)
    assert problems, f"弄坏了 {expect_kw} 却没报错：{s.expected}"
    assert any(expect_kw in p for p in problems), problems


def test_validate_catches_deadline_without_entities():
    s = _good_sample()
    s.expected["entities"] = []
    s.expected["deadline"] = "2026-10-15"
    assert any("过度抽取" in p for p in sn.validate(s))


def test_validate_catches_duplicate_ids_and_texts():
    a = _good_sample()
    b = sn.generate(2, seed=3)[1]
    b.id = a.id
    b.text = a.text
    problems = sn.validate_all([a, b])
    assert any("重复 id" in p for p in problems)
    assert any("重复正文" in p for p in problems)


# ================================================ 口径跨文件一致 ====


def test_reference_date_matches_eval_dataset():
    """相对时间（"本周五"）的期望值按基准日推算 —— 两边不一致就全错。"""
    assert sn.REFERENCE_DATE.isoformat() == EVAL_CASES["reference_date"]


def test_categories_match_eval_label_space():
    """类别词表必须等于**评测集实际用到的**那几类。

    直接对评测集比，而不是对某个模块常量比：训练集教出的类目集合，
    与评测量到的类目集合必须是同一个闭集，否则模型会去猜一个评测里根本不存在的类目。
    """
    assert set(sn.CATEGORIES) == {c["expected"]["category"] for c in EVAL_CASES["cases"]}


def test_system_prompt_source_is_c34_constant():
    """训练用的 system prompt 必须就是 C34 那个常量（不是我们另写一份）。"""
    assert eb.SYSTEM_PROMPT.strip()
    rec = sn.to_messages(sn.generate(1)[0], eb.SYSTEM_PROMPT)
    assert rec["messages"][0]["content"] == eb.SYSTEM_PROMPT


@pytest.mark.parametrize("snippet,norm,not_norm", [
    ("本周五", "2026-09-18", "2026-09-16"),          # 基准日是周三，本周五是 9-18
    ("下周三", "2026-09-23", "2026-09-16"),
    ("即日起两周内", "2026-09-30", "2026-09-23"),
    ("1月15日", "2027-01-15", "2026-01-15"),         # 无年份且早于今天 ⇒ 次年
])
def test_relative_dates_resolved_against_reference_date(samples, snippet, norm, not_norm):
    hit = [s for s in samples if any(e["text"] == snippet for e in s.expected["entities"])]
    assert hit, f"语料里没有 {snippet} 这类样本，相对时间就没被覆盖"
    norms = {e["norm"] for s in hit for e in s.expected["entities"] if e["text"] == snippet}
    assert norms == {norm}, f"{snippet} 的归一化应恒为 {norm}，实测 {norms}"
    assert not_norm not in norms


# ================================================ 与留出集隔离 ====


def _holdout() -> dict:
    texts, ids = [], []
    texts += [c["text"] for c in EVAL_CASES["cases"]]
    ids += [c["id"] for c in EVAL_CASES["cases"]]
    for rel, key in (("ai/eval/fixtures/few_shot_examples.json", "examples"),
                     ("ai/eval/fixtures/prompt_opt_examples_ext.json", "examples")):
        p = REPO_ROOT / rel
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            texts += [e["text"] for e in data.get(key, [])]
            ids += [e["id"] for e in data.get(key, [])]
    return {"texts": texts, "ids": ids}


def test_no_id_collision_with_holdout(samples):
    hold = _holdout()
    assert {s.id for s in samples} & set(hold["ids"]) == set()


def test_no_text_collision_with_holdout(samples):
    hold = {t.replace(" ", "") for t in _holdout()["texts"]}
    dup = [s.id for s in samples if s.text.replace(" ", "") in hold]
    assert dup == [], f"与留出集正文重合：{dup}"


def test_similarity_stays_below_threshold(samples):
    """表层重合度要量化 —— "我很小心没抄"是没法服人的，给个数才有。"""
    report = sn.similarity_report(samples, _holdout()["texts"])
    assert report["passed"] is True, report
    assert report["max_similarity"] < 0.6


def test_similarity_report_flags_a_copy():
    """**反向对照**：把留出集的正文原样塞进语料，重合度检查必须报警。"""
    hold = _holdout()["texts"]
    fake = _good_sample()
    fake.text = hold[0]
    report = sn.similarity_report([fake], hold)
    assert report["passed"] is False
    assert report["max_similarity"] == pytest.approx(1.0, abs=0.01)


# ================================================ 覆盖与配比 ====


def test_coverage_matrix_is_complete(samples):
    stats = sn.summarize(samples)
    assert set(stats["by_category"]) == set(sn.CATEGORIES)
    assert set(int(k) for k in stats["by_importance"]) == set(sn.IMPORTANCE_LEVELS)
    assert set(int(k) for k in stats["by_n_entities"]) == {0, 1, 2, 3}
    # 相对时间/跨年/无时间这三类必须都在（陷阱样本的来源）
    kinds = set(stats["by_time_kind"])
    for must in ("none", "cross_year", "this_week_friday", "next_week_wednesday",
                 "since_today_two_weeks", "no_year", "multi2+multi2", "multi3+multi3+multi3"):
        assert any(k.startswith(must.split("+")[0]) and must.split("+")[0] in k for k in kinds), \
            f"缺时间表达 {must}：{sorted(kinds)}"


def test_entity_mix_is_close_to_target(samples):
    """实体数配比要贴近 `ENTITY_MIX`（陷阱样本训不足，评测上必扣分）。"""
    stats = sn.summarize(samples)
    got = {int(k): v / stats["count"] for k, v in stats["by_n_entities"].items()}
    for n, want in sn.ENTITY_MIX.items():
        assert abs(got.get(n, 0.0) - want) <= 0.03, f"n={n}: 目标 {want}，实测 {got.get(n)}"


def test_null_deadline_samples_exist(samples):
    """必须真的存在"无时间要求"的样本：评测集里这类占 8%，硬编日期会被判 FP。"""
    nulls = [s for s in samples if s.expected["deadline"] is None]
    assert len(nulls) >= COUNT * 0.05
    assert all(s.expected["entities"] == [] for s in nulls)


@pytest.mark.parametrize("rule,expect", [("cutoff_last", max), ("start_first", min)])
def test_deadline_rule_conventions(samples, rule, expect):
    """多实体样本的 deadline 取法必须符合该类约定（报名取截止、放假取开始）。"""
    multi = [s for s in samples
             if s.tags["deadline_rule"] == rule and len(s.expected["entities"]) >= 2]
    assert multi, f"没有 {rule} 的多实体样本"
    for s in multi:
        norms = [e["norm"] for e in s.expected["entities"]]
        assert s.expected["deadline"] == expect(norms), s.to_dict()


def test_entity_text_is_exact_substring(samples):
    for s in samples:
        for e in s.expected["entities"]:
            assert e["text"] in s.text


# ================================================ 训练格式 ====


def test_to_messages_shape_and_system_prompt(samples):
    system = eb.SYSTEM_PROMPT
    rec = sn.to_messages(samples[0], system)
    roles = [m["role"] for m in rec["messages"]]
    assert roles == ["system", "user", "assistant"]
    assert rec["messages"][0]["content"] == system
    assert rec["messages"][1]["content"] == samples[0].text
    payload = json.loads(rec["messages"][2]["content"])
    assert set(payload) == set(sn.FIELD_ORDER)
    assert payload == {k: samples[0].expected[k] for k in sn.FIELD_ORDER}


def test_train_jsonl_roundtrip(tmp_path, samples):
    """落盘后能被 train.py 的 `load_jsonl`（逐行 json）读回来。"""
    system = eb.SYSTEM_PROMPT
    path = tmp_path / "x.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for s in samples[:20]:
            fh.write(json.dumps(sn.to_messages(s, system), ensure_ascii=False) + "\n")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 20
    assert all("messages" in r for r in rows)
