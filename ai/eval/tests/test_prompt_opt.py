#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C35 提示工程实验的测试。

与 C34 测试同一套原则：**不需要 Ollama 也能验证关键逻辑**，并且每条关键断言都配
**反向对照**（证明断言不是恒真的）。

这里重点守四件事：
  1. `V0-naive` 必须与 C34 的 `SYSTEM_PROMPT` **逐字节相同**，且各档之间是**只追加**关系
     —— 否则"优化前/优化后"的对照不成立，整个实验就是自证；
  2. 示例池 **不得与评测集有 id 或文本重合**（重合就是泄题，F1 会虚高）；
  3. 配对自助法要真的会动（相同两组 → Δ=0 且区间跨 0；不同两组 → 方向对得上）；
  4. 门禁退出码要真的会拦（达标 0 / 未达标 1）—— 否则 CI 门禁形同虚设。

⚠️ 与 C34 测试一样用 importlib 按路径加载，不写 `from ai.eval import ...`
（`ai/` 是否有 `__init__.py` 取决于 C31 是否已合并）。

作者：成员3 · C35
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[1]
DATASET_PATH = EVAL_DIR / "extract_cases.json"
BASE_POOL = EVAL_DIR / "fixtures" / "few_shot_examples.json"
EXT_POOL = EVAL_DIR / "fixtures" / "prompt_opt_examples_ext.json"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_t", EVAL_DIR / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


eb = _load("extract_bench")
pv = _load("prompt_variants")
po = _load("prompt_opt")


def _dataset_cases() -> list[dict]:
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))["cases"]


def _norm(text: str) -> str:
    """去掉空白后比较 —— 评测集里的文本带换行/多空格，直接比会漏判重合。"""
    return re.sub(r"\s+", "", text or "")


# ======================================================== prompt 阶梯 ====


def test_v0_is_byte_identical_to_c34_prompt():
    """**对照的根基**：优化前的 prompt 必须就是 C34 那个已发布的 prompt 本身。

    如果这里改成"重写一份等价的"，那 C34 的 0.3043 基线就不再可比，
    本实验也就没有"优化前"可言了。
    """
    assert pv.get_system_prompt("V0-naive") == eb.SYSTEM_PROMPT


def test_ladder_only_appends():
    """各档必须是"在上一档之上追加"。

    反向对照：若某档偷偷改写了前文（例如把 C34 的格式说明删了），
    对照就不再是"只多了一条标准"，增益无法归因 —— 这条断言就是防这个的。
    """
    for prev, cur in zip(pv.VARIANTS, pv.VARIANTS[1:]):
        a, b = pv.get_system_prompt(prev), pv.get_system_prompt(cur)
        assert len(b) > len(a)
        assert b.startswith(a), f"{cur} 不是 {prev} 的追加，而是改写了原文"


@pytest.mark.parametrize("variant,needle", [
    ("V1-date", "2026-09-16"),          # 基准日
    ("V1-date", "早于今天"),             # 跨年规则
    ("V2-rubric", "importance"),        # 评分细则
    ("V3-optimized", "奖学金"),          # 受控词表
    ("V3-optimized", "只能选一个"),       # 单值约束
    ("V3-optimized", "不要为了填满而编造"),  # null 规则
    ("V3-optimized", "原文片段"),         # entities 口径
])
def test_ladder_contains_expected_standard(variant, needle):
    assert needle in pv.get_system_prompt(variant)


@pytest.mark.parametrize("variant,needle", [
    # 注意不能用 importance / 奖学金 这类词做判据：C34 的**输出格式说明**里本来就有它们，
    # 用它们会导致"低档没有"的断言假失败（自己踩过）。
    ("V0-naive", "错过就失去资格"),      # V0 不该有重要度判定标准
    ("V1-date", "不要输出组合值"),        # V1 不该有受控词表约束
    ("V2-rubric", "只能选一个"),         # V2 不该有单值约束
])
def test_reverse_control_lower_tiers_lack_later_standards(variant, needle):
    """**反向对照**：断言上面那组"含有某条标准"不是恒真的 —— 低档确实没有它。"""
    assert needle not in pv.get_system_prompt(variant)


def test_unknown_variant_raises():
    with pytest.raises(KeyError):
        pv.get_system_prompt("V9-nonexistent")


def test_reference_date_matches_dataset():
    """prompt 里的基准日必须与评测集的 `reference_date` 一致。

    不一致的话，"本周五"这类相对时间的期望值就变了，而模型仍会按 prompt 里的日期算 ——
    F1 会莫名其妙地掉，且很容易被误读成"prompt 变差了"。
    """
    dataset_date = json.loads(DATASET_PATH.read_text(encoding="utf-8"))["reference_date"]
    assert pv.REFERENCE_DATE == dataset_date


# ======================================================== 示例池 ====


def test_pool_k_zero_is_empty():
    assert po.build_pool(0) == []
    assert po.build_pool(-3) == []


def test_pool_within_c34_is_identical_to_c34_pool():
    """k ≤ 4 时必须**就是** C34 的池子 —— 这是能复现 C34 数字的前提。"""
    c34 = json.loads(BASE_POOL.read_text(encoding="utf-8"))["examples"]
    for k in (1, 2, 3, 4):
        assert po.build_pool(k) == c34[:k]


def test_pool_beyond_c34_appends_extension():
    ext = json.loads(EXT_POOL.read_text(encoding="utf-8"))["examples"]
    five = po.build_pool(5)
    assert [e["id"] for e in five] == ["FS01", "FS02", "FS03", "FS04", "FS05"]
    assert five[4] == ext[0]
    # 要满时按池子上限截断，而不是报错或重复
    assert len(po.build_pool(po.pool_size_cap())) == po.pool_size_cap()
    assert len(po.build_pool(po.pool_size_cap() + 10)) == po.pool_size_cap()


def test_pool_ids_are_unique():
    pool = po.build_pool(po.pool_size_cap())
    ids = [e["id"] for e in pool]
    assert len(ids) == len(set(ids))


def test_pool_never_overlaps_eval_set_ids():
    """示例与评测集 id 重合 = 把答案喂进 prompt。"""
    cases = _dataset_cases()
    overlap = {e["id"] for e in po.build_pool(po.pool_size_cap())} & {c["id"] for c in cases}
    assert not overlap, f"示例与评测集 id 重合：{sorted(overlap)}"


def test_pool_never_overlaps_eval_set_texts():
    """**文本**也不能重合 —— 评测集里同一句话换个 id 就等于泄题（C34 的有效约束，这里加倍）。"""
    eval_texts = {_norm(c["text"]) for c in _dataset_cases()}
    dup = [e["id"] for e in po.build_pool(po.pool_size_cap()) if _norm(e["text"]) in eval_texts]
    assert not dup, f"示例文本与评测集重合：{dup}"


def test_reverse_control_text_overlap_detector_works():
    """**反向对照**：把评测集的第一条文本塞进池子，上面那个检测器必须抓得到。"""
    eval_texts = {_norm(c["text"]) for c in _dataset_cases()}
    fake = {"id": "XX01", "text": _dataset_cases()[0]["text"]}
    assert _norm(fake["text"]) in eval_texts


def test_extension_examples_are_wellformed():
    """扩充示例自己也要合格：类目在受控词表内、deadline 格式对、实体字段齐。"""
    ext = json.loads(EXT_POOL.read_text(encoding="utf-8"))["examples"]
    assert len(ext) == 12
    for ex in ext:
        exp = ex["expected"]
        assert exp["category"] in pv.CATEGORIES, ex["id"]
        assert isinstance(exp["importance"], int) and 1 <= exp["importance"] <= 5, ex["id"]
        dl = exp["deadline"]
        assert dl is None or re.fullmatch(r"\d{4}-\d{2}-\d{2}", dl), f"{ex['id']} deadline={dl}"
        for ent in exp["entities"]:
            assert ent["type"] == "time" and ent["text"] and ent["norm"], ex["id"]
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", ent["norm"]), ex["id"]
        # 没有时间要求就必须是 null，否则自相矛盾
        if not exp["entities"]:
            assert dl is None, f"{ex['id']} 没有时间实体却给了 deadline"


def test_extension_covers_the_label_space():
    """示例池要覆盖评测集会用到的取值，否则"k 增大"只是同质堆叠。"""
    ext = json.loads(EXT_POOL.read_text(encoding="utf-8"))["examples"]
    base = json.loads(BASE_POOL.read_text(encoding="utf-8"))["examples"]
    pool = base + ext
    assert {e["expected"]["category"] for e in pool} == set(pv.CATEGORIES)
    assert {e["expected"]["importance"] for e in pool} == {3, 4, 5}
    assert any(e["expected"]["deadline"] is None for e in pool)


# ======================================================== 网格 ====


def test_grid_dedups_and_keeps_ladder_order():
    grid = po.build_grid(["V0-naive", "V1-date"], [0], ["V0-naive"], [0, 1, 2])
    assert grid[:2] == [("V0-naive", 0), ("V1-date", 0)]
    assert len(grid) == len(set(grid))
    assert ("V0-naive", 1) in grid and ("V1-date", 1) not in grid


def test_grid_empty_curve_variants_is_ok():
    assert po.build_grid(["V0-naive"], [0], [], []) == [("V0-naive", 0)]


# ======================================================== 统计 ====


def _rows(perfect: int, wrong: int, *, prefix: str = "") -> list[dict]:
    """造 n 条满分 + m 条全错的 case（只给 micro 需要的字段）。"""
    rows = []
    for i in range(perfect):
        rows.append({"id": f"{prefix}P{i}", "score": {"strict": True, "fields": {
            "category": {"tp": 1, "fp": 0, "fn": 0},
            "importance": {"tp": 1, "fp": 0, "fn": 0},
            "deadline": {"tp": 1, "fp": 0, "fn": 0},
            "entities": {"tp": 1, "fp": 0, "fn": 0},
        }}})
    for i in range(wrong):
        rows.append({"id": f"{prefix}W{i}", "score": {"strict": False, "fields": {
            "category": {"tp": 0, "fp": 1, "fn": 1},
            "importance": {"tp": 0, "fp": 1, "fn": 1},
            "deadline": {"tp": 0, "fp": 1, "fn": 1},
            "entities": {"tp": 0, "fp": 1, "fn": 1},
        }}})
    return rows


def _rows_for_same_ids(ids: list[str], correct: int) -> list[dict]:
    """**同一组 id**、前 `correct` 条对、其余条全错。

    配对自助法要求两侧覆盖同一批 case（同一条 case 的两侧一起进/一起出），
    所以不能用两组 id 不同的行去比 —— 那是调用方的错用，函数会直接报错。
    """
    rows = []
    for i, cid in enumerate(ids):
        ok = i < correct
        rows.append({"id": cid, "score": {"strict": ok, "fields": {
            f: ({"tp": 1, "fp": 0, "fn": 0} if ok else {"tp": 0, "fp": 1, "fn": 1})
            for f in ("category", "importance", "deadline", "entities")
        }}})
    return rows


def test_micro_f1_endpoints():
    assert po.micro_f1_of(_rows(4, 0)) == pytest.approx(1.0)
    # 反向对照：全错必须是 0，不能是 None 也不能是 1
    assert po.micro_f1_of(_rows(0, 4)) == pytest.approx(0.0)


def test_bootstrap_identical_groups_is_zero_and_not_significant():
    rows = _rows(3, 1)
    out = po.paired_bootstrap_delta(rows, [dict(r) for r in rows], iters=200, seed=7)
    assert out["delta_pt"] == 0.0
    assert out["ci_low_pt"] <= 0 <= out["ci_high_pt"]
    assert out["significant"] is False


def test_bootstrap_direction_and_significance():
    ids = [f"E{i:02d}" for i in range(8)]
    a, b = _rows_for_same_ids(ids, 1), _rows_for_same_ids(ids, 8)
    out = po.paired_bootstrap_delta(a, b, iters=200, seed=7)
    assert out["delta_pt"] > 0 and out["significant"] is True
    # 反向对照：交换两侧，符号必须反过来（证明不是恒为正）
    rev = po.paired_bootstrap_delta(b, a, iters=200, seed=7)
    assert rev["delta_pt"] < 0 and rev["significant"] is True


def test_bootstrap_requires_same_case_coverage():
    """**配对**的前提是两侧覆盖同一批 case —— 不一致必须报错，不能变成"独立两组"比较。"""
    with pytest.raises(ValueError):
        po.paired_bootstrap_delta(_rows_for_same_ids(["E01", "E02"], 1),
                                  _rows_for_same_ids(["E01", "E03"], 2), iters=20)


def test_bootstrap_rejects_mismatched_lengths():
    """条数不一致时必须报错，不能悄悄取交集 —— 那会让 Δ 失去可比性。"""
    with pytest.raises(ValueError):
        po.paired_bootstrap_delta(_rows(2, 0), _rows(3, 0), iters=10)


def test_label_budget_reached_case():
    records = [
        {"variant": "V0-naive", "k_effective": 0, "metrics": {"micro": {"f1": 0.30}}},
        {"variant": "V0-naive", "k_effective": 4, "metrics": {"micro": {"f1": 0.60}}},
        {"variant": "V3-optimized", "k_effective": 0, "metrics": {"micro": {"f1": 0.50}}},
    ]
    out = po.label_budget(records, naive="V0-naive", optimized="V3-optimized")
    assert out["reached"] is True and out["matched_k"] == 4
    assert out["bracket_below_k"] == 0 and out["bracket_above_k"] == 4


def test_label_budget_reverse_control_never_reached():
    """**反向对照**：朴素 prompt 一直追不上时必须如实说"没追上"，不能返回一个假 k。"""
    records = [
        {"variant": "V0-naive", "k_effective": 0, "metrics": {"micro": {"f1": 0.10}}},
        {"variant": "V0-naive", "k_effective": 4, "metrics": {"micro": {"f1": 0.20}}},
        {"variant": "V3-optimized", "k_effective": 0, "metrics": {"micro": {"f1": 0.90}}},
    ]
    out = po.label_budget(records, naive="V0-naive", optimized="V3-optimized")
    assert out["reached"] is False and out["matched_k"] is None
    assert out["max_k_tested"] == 4


def test_label_budget_unavailable_without_optimized_k0():
    out = po.label_budget([{"variant": "V0-naive", "k_effective": 0,
                            "metrics": {"micro": {"f1": 0.3}}}],
                          naive="V0-naive", optimized="V3-optimized")
    assert out["available"] is False


# ================================================ 截断检测 ====


def _rec(variant: str, k: int, tokens: float) -> dict:
    return {"variant": variant, "k_effective": k, "prompt_tokens_avg": tokens}


def test_detect_truncation_flags_flat_tokens():
    """**这条是本次实验最容易踩的坑**：k 涨了 token 不涨 ⇒ 示例被静默截断。"""
    warn = po.detect_truncation([_rec("V0-naive", 0, 150), _rec("V0-naive", 16, 150)])
    assert len(warn) == 1 and "截断" in warn[0]


def test_detect_truncation_silent_when_tokens_grow():
    """反向对照：token 正常增长时不得误报。"""
    assert po.detect_truncation([_rec("V0-naive", 0, 150), _rec("V0-naive", 16, 1800)]) == []


def test_detect_truncation_ignores_scripted_backend():
    """scripted 后端没有 token 统计（全是 0）—— 不该因此报警。"""
    assert po.detect_truncation([_rec("V0-naive", 0, 0), _rec("V0-naive", 4, 0)]) == []


# ================================================ 端到端（离线）====


def _run_scripted(tmp_path, extra: list[str]) -> int:
    args = [
        "--backend", "scripted",
        "--scripted-answers", str(EVAL_DIR / "fixtures" / "answers_upper_bound.json"),
        "--variants", "V0-naive,V3-optimized",
        "--ks", "0",
        "--curve-variants", "V0-naive",
        "--curve-ks", "0,1",
        "--out", str(tmp_path / "r.json"),
    ] + extra
    return po.main(args)


def test_scripted_end_to_end_writes_reports(tmp_path, capsys):
    """离线跑通全链路（不依赖 Ollama），并落盘 JSON + Markdown。"""
    rc = _run_scripted(tmp_path, ["--min-gain-pt", "0"])
    assert rc == 0
    payload = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert payload["meta"]["backend"] == "scripted"
    assert [c["config"] for c in payload["configs"]]
    md = (tmp_path / "r.md").read_text(encoding="utf-8")
    for section in ("## 一、prompt 阶梯", "## 二、prompt × 示例条数", "## 七、诚实边界"):
        assert section in md


def test_scripted_end_to_end_gate_blocks_when_not_met(tmp_path):
    """**反向对照**：门禁必须真的会拦 —— scripted 下 Δ=0，把阈值抬到 1 就该退出 1。

    如果没有这条，"达标"可能只是退出码永远为 0 的假象。
    """
    assert _run_scripted(tmp_path, ["--min-gain-pt", "1"]) == 1


def test_unknown_variant_is_rejected(tmp_path):
    rc = po.main(["--backend", "scripted",
                  "--scripted-answers", str(EVAL_DIR / "fixtures" / "answers_upper_bound.json"),
                  "--variants", "V9-nope"])
    assert rc == 2


def test_reference_date_mismatch_is_rejected(tmp_path):
    """评测集基准日与 prompt 里的不一致时必须拒绝开跑（而不是照跑出个怪数字）。"""
    bad = tmp_path / "bad.json"
    data = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    data["reference_date"] = "2020-01-01"
    bad.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    rc = po.main(["--backend", "scripted",
                  "--scripted-answers", str(EVAL_DIR / "fixtures" / "answers_upper_bound.json"),
                  "--dataset", str(bad), "--variants", "V0-naive", "--ks", "0",
                  "--curve-variants", "V0-naive", "--curve-ks", "0"])
    assert rc == 2


# ==================== 已落盘基线的离线自洽守卫（评审 P2）====================
#
# 为什么需要它：`--reproduce-f1 0.4693` 那条复现校验**要跑真 Ollama + 24 条推理**才会执行。
# 也就是说，如果有人改坏了 `extract_bench` 的度量口径（`prf` / `aggregate` / 归一化），
# **CI 不会提前红**，得等某人手动跑一次全量实验才发现 —— 而"口径变了但数字看着正常"
# 正是最难查的一类回归。
#
# 下面这条不需要模型：拿已落盘的基线 JSON，用**当前代码**从它自己存的 tp/fp/fn
# 反算 P/R/F1 与 micro/macro，逐位比对。任何动了度量公式的改动都会立刻在这里红。

BASELINE_JSON = EVAL_DIR / "baselines" / "c35_prompt_opt_20260917.json"


def _baseline_configs():
    if not BASELINE_JSON.exists():
        pytest.skip(f"基线不存在：{BASELINE_JSON}")
    return json.loads(BASELINE_JSON.read_text(encoding="utf-8")).get("configs") or []


def _recompute_all(eb) -> int:
    """用 `eb`（extract_bench）从基线自身的 tp/fp/fn 反算，逐位比对；返回校验处数。"""
    import statistics

    checked = 0
    for c in _baseline_configs():
        m = c.get("metrics") or {}
        per_field = m.get("per_field") or {}
        tag = f"{c.get('variant')}@k={c.get('k_effective')}"

        # ① 逐字段 P/R/F1（含分母为 0 的 None 语义与 4 位舍入）
        for fname, stored in per_field.items():
            got = eb.prf(tp=stored["tp"], fp=stored["fp"], fn=stored["fn"])
            for key in ("precision", "recall", "f1"):
                assert got[key] == stored[key], (
                    f"[{tag}] 字段 {fname}.{key} 反算不一致："
                    f"基线 {stored[key]} vs 当前代码 {got[key]} —— 度量口径被改过？"
                    f"改了就要同步重跑基线。"
                )
            checked += 1

        # ② micro：各字段 tp/fp/fn 相加后再算
        summed = {k: sum(per_field[f][k] for f in per_field) for k in ("tp", "fp", "fn")}
        got_micro = eb.prf(**summed)
        for key in ("precision", "recall", "f1", "tp", "fp", "fn"):
            assert got_micro[key] == m["micro"][key], (
                f"[{tag}] micro.{key} 反算不一致：基线 {m['micro'][key]} vs {got_micro[key]}"
            )
        checked += 1

        # ③ macro：各字段 F1 的算术平均
        f1s = [v["f1"] for v in per_field.values() if v["f1"] is not None]
        if f1s:
            assert round(statistics.fmean(f1s), 4) == m["macro_f1"], (
                f"[{tag}] macro_f1 反算不一致：基线 {m['macro_f1']}"
                f" vs {round(statistics.fmean(f1s), 4)}"
            )
            checked += 1
    return checked


def test_published_baseline_is_reproducible_offline():
    """落盘基线的每个数字，都必须能用**当前代码**从它自己的 tp/fp/fn 反算出来。

    覆盖三件事：`prf` 的公式与舍入、`aggregate` 的 micro 汇总、`aggregate` 的 macro。
    **不需要 Ollama** —— 所以它能进 CI，而 `--reproduce-f1` 那条不能。
    """
    configs = _baseline_configs()
    assert configs, "基线里没有 configs"
    checked = _recompute_all(eb)
    assert checked >= len(configs) * 2, f"只校验了 {checked} 处，覆盖不足"


def test_offline_guard_detects_a_metric_change(monkeypatch):
    """**反向对照**：把 `prf` 改坏一点点，上面那条守卫必须失败。

    没有这条，上面那条可能只是"恰好全绿"（比如基线没存够数、或断言根本没跑到）。
    """
    real = eb.prf
    # 变异：偷偷把 tp +1（模拟"口径被改坏"）
    monkeypatch.setattr(eb, "prf",
                        lambda **kw: real(tp=kw["tp"] + 1, fp=kw["fp"], fn=kw["fn"]))
    with pytest.raises(AssertionError):
        _recompute_all(eb)
