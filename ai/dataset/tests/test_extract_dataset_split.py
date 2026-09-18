#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C33 train/dev 切分自检的测试（回应 PR #119 审查 P3-2，登记项 `AI-11`）。

审查原话：「训练/验证切分是分层切分，但报告里没写'分层依据' …… 若切分按生成顺序
而非按类目/难度分层，dev 的分布可能与 train 不同，"dev 指标"就不能代表泛化。
建议在 README 里一句话说明切分依据，**或直接断言两边类目分布接近**。」

这里做的是后者（更强的那条），并且给闸门配足反向对照 —— 否则"通过"可能只是恒真：

  1. **分层真的生效**：每个类型两边都有，且两个维度的占比差都在标定容差内（800 条实测 0.004）；
  2. **闸门有区分度**：
     - 人为把某一类**全塞进一边** ⇒ 必须判不一致（反向对照：正常切分必须通过）；
     - 小规模下某类**只进了一边**（生成器真实行为）⇒ 必须报 `missing_in_dev`；
  3. **容差按实测标定**：小样本按 `2/|dev|` 放宽、稳健规模退化为 5 个百分点下限，
     并断言"60 条时实测差 0.099 > 0.05" —— 说明只留固定下限是不够的。

⚠️ 与 `test_synth_notice.py` 一致：用 importlib 按路径加载被测模块（不依赖 `ai/` 包结构）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
DATASET_DIR = TESTS_DIR.parent
REPO_ROOT = DATASET_DIR.parent.parent
FINETUNE_DIR = REPO_ROOT / "ai" / "finetune"


def _load(path: Path, name: str):
    """按路径加载（并注册进 sys.modules —— 被测模块里有 dataclass，不注册会炸）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


synth = _load(DATASET_DIR / "synth_notice.py", "synth_notice_split_test")
builder = _load(FINETUNE_DIR / "build_extract_dataset.py", "build_extract_split_test")

SEED = 20260917
DEV_RATIO = 0.2


@pytest.fixture(scope="module")
def big_split():
    """800 条（= 正式产物的规模）：生成一次，多个用例共用。"""
    samples = synth.generate(800, seed=SEED)
    train, dev = builder.stratified_split(samples, DEV_RATIO, SEED)
    return samples, train, dev


# ==================================================== 分层真的生效 ====


def test_split_report_passes_on_the_real_scale(big_split):
    """正式规模（800 条）下：两边类型齐全，占比差远小于容差。"""
    _samples, train, dev = big_split
    rep = builder.split_report(train, dev)
    assert rep["passed"] is True
    assert rep["missing_in_dev"] == [] and rep["missing_in_train"] == []
    assert rep["dev_share_max_gap"] <= rep["tolerance"]
    # 生产规模下实测只有 0.004 —— 断言一个宽松上限，防止将来悄悄退化
    assert rep["dev_share_max_gap"] < 0.02, rep["dev_share_max_gap"]
    # 覆盖矩阵的 12 个类型（`notice_info_only` 等）两边都得有
    assert len(rep["by_kind"]) >= 8
    assert all(v["train"] > 0 and v["dev"] > 0 for v in rep["by_kind"].values())


def test_split_is_deterministic_and_seed_sensitive(big_split):
    """同 seed 逐条相同（可复现）；换 seed 必须变 —— 否则说明 seed 根本没进切分。"""
    samples, train, dev = big_split
    train2, dev2 = builder.stratified_split(samples, DEV_RATIO, SEED)
    assert [s.id for s in train2] == [s.id for s in train]
    assert [s.id for s in dev2] == [s.id for s in dev]

    train3, dev3 = builder.stratified_split(samples, DEV_RATIO, SEED + 1)
    assert [s.id for s in dev3] != [s.id for s in dev]


# ==================================================== 闸门有区分度 ====


def test_gate_flags_a_kind_missing_in_dev():
    """**反向对照**：小规模下确实会出现"某类只进了一边"（生成器真实行为，非构造）。"""
    samples = synth.generate(40, seed=SEED)
    train, dev = builder.stratified_split(samples, DEV_RATIO, SEED)
    rep = builder.split_report(train, dev)
    assert rep["missing_in_dev"], "40 条时应当有类型没进 dev（否则这条断言是空跑的）"
    assert rep["passed"] is False


def test_gate_flags_a_skewed_split(big_split):
    """**反向对照**：把某一类整体挪进 dev ⇒ 闸门必须拒绝（证明占比差真的在起作用）。"""
    samples, train, dev = big_split
    worst_kind = builder.split_report(train, dev)["worst_key"]
    all_of_one_kind = [s for s in samples if s.tags["kind"] == worst_kind]
    skewed = builder.split_report(
        [s for s in samples if s.tags["kind"] != worst_kind],
        all_of_one_kind,
    )
    assert skewed["passed"] is False
    assert skewed["dev_share_max_gap"] > skewed["tolerance"]
    assert worst_kind in skewed["missing_in_train"] or skewed["dev_share_max_gap"] > 0.2


def test_gate_rejects_an_empty_dev(big_split):
    """空 dev 是"最严重的不一致"：不能因为 `n_dev=0` 时占比全为 0 就判通过。"""
    samples, train, _dev = big_split
    rep = builder.split_report(train, [])
    assert rep["passed"] is False
    assert len(rep["missing_in_dev"]) == len(rep["by_kind"])


# ==================================================== 容差标定 ========


def test_tolerance_is_calibrated_not_guessed():
    """容差 = max(5 个百分点, 2/|dev|)：小样本放宽、稳健规模吃下限。"""
    assert builder.split_share_tolerance(159) == 0.05
    assert builder.split_share_tolerance(240) == 0.05
    assert builder.split_share_tolerance(14) == pytest.approx(2 / 14, abs=1e-4)
    # 空 dev 不能给出"最宽松"的容差（否则配合占比全 0 会放行）
    assert builder.split_share_tolerance(0) >= 1.0


def test_fixed_floor_alone_would_be_wrong():
    """标定依据本身要能被检验：60 条规模的实测占比差已经高于 5 个百分点下限。

    这条断言的用途：如果哪天有人把容差改成"固定 0.05"，小规模会开始假红 ——
    而那个 0.099 不是分布跑偏，是"占比在 14 条 dev 上本来就是离散的"。
    """
    samples = synth.generate(60, seed=SEED)
    train, dev = builder.stratified_split(samples, DEV_RATIO, SEED)
    rep = builder.split_report(train, dev)
    assert rep["dev_share_max_gap"] > builder.SPLIT_SHARE_TOLERANCE
    assert rep["dev_share_max_gap"] < builder.split_share_tolerance(len(dev))
    assert rep["passed"] is True
