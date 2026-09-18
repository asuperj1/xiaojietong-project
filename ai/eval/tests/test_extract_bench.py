#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C34 基线对比框架的测试。

设计原则：**评分函数必须能在没有模型的情况下被验证**。
否则所有测试都要 Ollama 在线，CI 跑不了，评分逻辑就没人敢改 ——
而评分逻辑一旦错了，"F1 提升 15 个百分点"这个验收结论就是假的。

因此这里：
  · `scripted` 后端提供"已知答案"，用来测端到端
  · 每个关键断言都配一个**反向对照**（证明断言不是恒真的）

⚠️ 用 importlib 按路径加载被测模块，而不是 `from ai.eval.extract_bench import ...`：
   本仓库 `ai/` 包是否有 `__init__.py` 取决于 C31 是否已合并，
   按路径加载可以让 C34 独立于 C31（两者都要动 `ai/` 顶层）。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[1]
C34_PATH = EVAL_DIR / "extract_bench.py"
DATASET_PATH = EVAL_DIR / "extract_cases.json"


def _load():
    spec = importlib.util.spec_from_file_location("extract_bench_c34", C34_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


eb = _load()


# ---------------------------------------------------------------- 数据 ----

def test_dataset_is_valid():
    """评测集本身要合格：id 唯一、字段齐全、category 有值。"""
    data = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    cases = data["cases"]
    assert len(cases) >= 20

    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), f"id 重复：{ids}"

    for c in cases:
        assert c["category"], c["id"]
        assert c["text"].strip(), c["id"]
        exp = c["expected"]
        assert "category" in exp and "importance" in exp, c["id"]
        assert "deadline" in exp, c["id"]
        assert isinstance(exp["entities"], list), c["id"]
        if exp["deadline"] is not None:
            assert len(exp["deadline"]) == 10, f"{c['id']} deadline 应为 YYYY-MM-DD"


def test_dataset_contains_hard_cases():
    """评测集必须含难点，否则 F1 会虚高、区分不出模型好坏。

    三条底线，缺一条这个评测集就失去区分度：
      · 有 deadline 为 null 的样本（考察会不会硬编日期）
      · 有必须归一化的时间写法（考察格式鲁棒性）
      · 有实体为空的样本
    """
    cases = json.loads(DATASET_PATH.read_text(encoding="utf-8"))["cases"]
    assert sum(1 for c in cases if c["expected"]["deadline"] is None) >= 3, "缺「无截止时间」样本"
    assert sum(1 for c in cases if not c["expected"]["entities"]) >= 2, "缺「无实体」样本"

    joined = " ".join(c["text"] for c in cases)
    for kw in ("本周", "下周", "即日起"):
        assert kw in joined, f"缺相对时间表述：{kw}"


# ------------------------------------------------------------ 指标计算 ----

def test_prf_hand_computed():
    """手算对照：TP=3 FP=1 FN=2 → P=0.75 R=0.6 F1=0.6667。

    容差 1e-3：`prf` 会把结果 round 到 4 位小数（见其 docstring），
    所以 0.66666... 变成 0.6667。
    """
    m = eb.prf(tp=3, fp=1, fn=2)
    assert m["precision"] == pytest.approx(0.75)
    assert m["recall"] == pytest.approx(0.6)
    assert m["f1"] == pytest.approx(0.6667, abs=1e-3)


def test_prf_no_samples_returns_none_not_zero():
    """无样本时 F1 必须是 None。

    给 0 会让"这个字段没数据"被读成"这个字段全错" —— 报告会误导人。
    """
    m = eb.prf(tp=0, fp=0, fn=0)
    assert m["precision"] is None
    assert m["recall"] is None
    assert m["f1"] is None


def test_prf_all_wrong_gives_zero():
    """反向对照：全错必须真的是 0.0（证明上面那个 None 不是"什么都返回 None"）。"""
    m = eb.prf(tp=0, fp=5, fn=5)
    assert m["precision"] == 0.0
    assert m["recall"] == 0.0
    assert m["f1"] == 0.0


# -------------------------------------------------------------- 解析容错 ----

def test_parse_plain_json():
    got, err = eb.parse_output('{"category": "活动", "importance": 3}')
    assert err == ""
    assert got["category"] == "活动"


def test_parse_fenced_json():
    """模型爱包 ```json —— 这是最常见的输出形态，必须能解析。"""
    got, err = eb.parse_output('```json\n{"deadline": "2026-09-30"}\n```')
    assert err == ""
    assert got["deadline"] == "2026-09-30"


def test_parse_json_with_surrounding_prose():
    """前后有解释文字也要能抠出来。

    否则模型只是多写一句"好的，结果如下"，就被判全错 ——
    F1 会被**非能力因素**拉低，测出来的不是抽取能力。
    """
    got, err = eb.parse_output('好的，抽取结果如下：\n{"importance": 5}\n希望有帮助。')
    assert err == ""
    assert got["importance"] == 5


def test_parse_garbage_returns_none_with_reason():
    got, err = eb.parse_output("我无法完成这个任务。")
    assert got is None
    assert err == "未解析出 JSON"


def test_parse_empty_returns_none():
    got, err = eb.parse_output("")
    assert got is None
    assert err == "空输出"


# ---------------------------------------------------------------- 归一化 ----

def test_normalize_deadline_accepts_datetime():
    """`2026-09-30 23:59:59` 与 `2026-09-30` 必须视为同一个答案。

    否则测的是"会不会写零点"，而不是"有没有抽对时间"。
    """
    assert eb.normalize_deadline("2026-09-30 23:59:59") == "2026-09-30"
    assert eb.normalize_deadline("2026-09-30") == "2026-09-30"
    assert eb.normalize_deadline("2026/9/30") == "2026-09-30"
    assert eb.normalize_deadline("2026年9月30日") == "2026-09-30"


def test_normalize_deadline_null_variants():
    for v in (None, "", "null", "None", "无"):
        assert eb.normalize_deadline(v) is None, v


def test_entity_set_keys_on_norm_then_text():
    got = eb.entity_set([
        {"type": "time", "text": "9月30日", "norm": "2026-09-30"},
        {"type": "time", "text": "下周三", "norm": None},
        {"type": "", "text": "噪声", "norm": None},
        "not a dict",
    ])
    assert got == {("time", "2026-09-30"), ("time", "下周三")}


# ---------------------------------------------------------------- 评分 ----

EXPECTED = {
    "category": "奖学金",
    "importance": 5,
    "deadline": "2026-09-30",
    "entities": [{"type": "time", "text": "9月30日前", "norm": "2026-09-30"}],
}


def test_score_perfect():
    s = eb.score_case(dict(EXPECTED), EXPECTED)
    assert s["strict"] is True
    assert all(c["tp"] == c["fn"] == 0 or c["tp"] >= 1 for c in s["fields"].values())
    assert s["fields"]["entities"] == {"tp": 1, "fp": 0, "fn": 0}


def test_score_over_extraction_counts_fp_not_fn():
    """真值没有 deadline，模型硬编了一个 → 记 FP（过度抽取），不能记 FN。

    两者都错，但方向不同：FP 说明模型爱编，FN 说明模型漏抽。
    混在一起就分不清该往哪个方向改。
    """
    exp = {"category": "通知", "importance": 3, "deadline": None, "entities": []}
    pred = {"category": "通知", "importance": 3, "deadline": "2026-10-01", "entities": []}
    s = eb.score_case(pred, exp)
    assert s["fields"]["deadline"] == {"tp": 0, "fp": 1, "fn": 0}
    assert s["strict"] is False


def test_score_missing_field_counts_fn():
    pred = {"category": "奖学金", "importance": 5, "entities": []}
    s = eb.score_case(pred, EXPECTED)
    assert s["fields"]["deadline"] == {"tp": 0, "fp": 0, "fn": 1}
    assert s["strict"] is False


def test_score_wrong_value_counts_both():
    pred = {"category": "活动", "importance": 5, "deadline": "2026-09-30", "entities": []}
    s = eb.score_case(pred, EXPECTED)
    assert s["fields"]["category"] == {"tp": 0, "fp": 1, "fn": 1}


def test_score_parse_failure_only_fn_never_fp():
    """解析失败时只应记漏抽（FN）。

    记 FP 是不对的：模型并没有"编造一个错答案"，它是"没给出可用答案"。
    这个区分让"格式失败"和"内容抽错"在报告里能分开看。
    """
    s = eb.score_case(None, EXPECTED)
    for f, c in s["fields"].items():
        assert c["fp"] == 0, f
    assert s["strict"] is False


def test_score_datetime_vs_date_is_correct():
    """模型输出带时分秒，只要日期对就算对（归一化生效）。"""
    pred = dict(EXPECTED, deadline="2026-09-30 23:59:59")
    s = eb.score_case(pred, EXPECTED)
    assert s["fields"]["deadline"]["tp"] == 1


# ------------------------------------------------------------ 汇总/对照 ----

def _rows_for(preds: list[dict | None], expected: dict = EXPECTED) -> list[dict]:
    return [{"id": f"X{i}", "score": eb.score_case(p, expected)}
            for i, p in enumerate(preds)]


def test_aggregate_perfect_is_one():
    rows = _rows_for([dict(EXPECTED)] * 4)
    agg = eb.aggregate(rows)
    assert agg["micro"]["f1"] == pytest.approx(1.0)
    assert agg["strict_accuracy"] == 1.0


def test_aggregate_all_wrong_is_zero():
    """反向对照：证明 aggregate 不是恒返回高分。"""
    wrong = {"category": "X", "importance": 1, "deadline": "1999-01-01", "entities": []}
    rows = _rows_for([wrong] * 4)
    agg = eb.aggregate(rows)
    assert agg["micro"]["f1"] == 0.0
    assert agg["strict_accuracy"] == 0.0


def test_aggregate_micro_differs_from_macro():
    """micro 按样本数加权，macro 等权 —— 两者只在**各字段样本量不同**时才分得开。

    ⚠️ 这里有个我第一版就写错的点：若所有样本都带全部字段、且只有**一个字段**出错，
    micro 和 macro 会**恰好相等**（都是 0.9667），断言 `!=` 直接失败。
    所以必须构造成「一个字段样本量大且全对 + 另一个字段样本量小且全错」：
      · category   24 条全对（22 条 big + 2 条 small，small 的 category 也答对）
                   → TP=24 FP=0 FN=0 → F1 = 1.0
      · importance  2 条全错（真值 5 → 预测 1，且只这 2 条带 importance）
                   → TP=0 FP=2 FN=2 → F1 = 0.0
      · macro = (1.0 + 0.0) / 2 = 0.5
      · micro = 24/(24+2) = 0.9231（按样本加权，被 2 条拖不动那么多）
    这也正是读报告时要看两个口径的原因：差距大 = 模型能力不均衡。
    """
    big = {"category": "奖学金", "importance": None, "deadline": None, "entities": []}
    small = {"category": "通知", "importance": 5, "deadline": None, "entities": []}

    rows = [{"id": f"B{i}", "score": eb.score_case(dict(big), big)} for i in range(22)]
    # importance 全错（真值 5 → 预测 1），category 仍答对
    wrong_small = dict(small, importance=1)
    rows += [{"id": f"S{i}", "score": eb.score_case(dict(wrong_small), small)} for i in range(2)]

    agg = eb.aggregate(rows)
    assert agg["per_field"]["category"]["f1"] == 1.0
    assert agg["per_field"]["importance"]["f1"] == 0.0
    assert agg["macro_f1"] == pytest.approx(0.5, abs=1e-3)
    assert agg["micro"]["f1"] == pytest.approx(0.9231, abs=1e-3)
    assert agg["micro"]["f1"] != agg["macro_f1"], "两口径必须分得开，否则其中一个写错了"


# ---------------------------------------------------------------- 对比 ----

def test_comparison_passes_when_delta_meets_threshold():
    report = {"metrics": {"micro": {"f1": 0.80}}}
    baseline = Path(__file__).with_name("_baseline_tmp.json")
    baseline.write_text(json.dumps({"metrics": {"micro": {"f1": 0.60}}}), encoding="utf-8")
    try:
        c = eb.compare_to_baseline(report, baseline, 15.0)
        assert c["delta_pt"] == pytest.approx(20.0)
        assert c["passed"] is True
    finally:
        baseline.unlink(missing_ok=True)


def test_comparison_fails_below_threshold():
    """反向对照：Δ 不够时必须判未达标。

    没有这条，`--compare` 可能永远返回"通过"。
    """
    report = {"metrics": {"micro": {"f1": 0.65}}}
    baseline = Path(__file__).with_name("_baseline_tmp2.json")
    baseline.write_text(json.dumps({"metrics": {"micro": {"f1": 0.60}}}), encoding="utf-8")
    try:
        c = eb.compare_to_baseline(report, baseline, 15.0)
        assert c["delta_pt"] == pytest.approx(5.0)
        assert c["passed"] is False
    finally:
        baseline.unlink(missing_ok=True)


def test_comparison_rejects_empty_f1():
    report = {"metrics": {"micro": {"f1": None}}}
    baseline = Path(__file__).with_name("_baseline_tmp3.json")
    baseline.write_text(json.dumps({"metrics": {"micro": {"f1": 0.6}}}), encoding="utf-8")
    try:
        with pytest.raises(SystemExit):
            eb.compare_to_baseline(report, baseline, 15.0)
    finally:
        baseline.unlink(missing_ok=True)


# ------------------------------------------------------------ 实验设计 ----

def test_zero_shot_and_finetuned_prompts_are_identical():
    """**本实验成立的前提**。

    任务书要证明"微调带来的提升"。如果 finetuned 用的 prompt 与 zero-shot 不同，
    那就无法区分提升来自微调还是来自提示词 —— 答辩时会被问倒。
    """
    text = "关于国庆放假的通知：10月1日至10月7日放假。"
    assert eb.build_prompt(text, "zero-shot") == eb.build_prompt(text, "finetuned")


def test_few_shot_prompt_differs():
    """反向对照：few-shot 必须真的与 zero-shot 不同，否则三种模式没区别。"""
    text = "关于国庆放假的通知。"
    ex = [{"text": "示例文本", "expected": {"category": "通知"}}]
    assert eb.build_prompt(text, "few-shot", ex) != eb.build_prompt(text, "zero-shot")


def test_few_shot_prompt_differs_with_real_fixture():
    """用**真实示例文件**验证 prompt 确实变了。

    上一个测试用的是手搓示例；这个用生产文件，防止"示例文件格式改了、
    拼 prompt 时没读到"这类只能靠真实文件才暴露的问题。
    """
    fs_path = EVAL_DIR / "fixtures" / "few_shot_examples.json"
    examples = eb.load_few_shot_examples(fs_path, 3)
    text = "关于选课系统开放的通知：9月26日至9月28日可选课。"
    fs_prompt = eb.build_prompt(text, "few-shot", examples)
    zs_prompt = eb.build_prompt(text, "zero-shot")
    assert fs_prompt != zs_prompt
    # 示例内容必须真的出现在 prompt 里（而不只是加了一句"以下是示例"）
    assert examples[0]["text"] in fs_prompt


def test_few_shot_examples_do_not_overlap_dataset():
    """few-shot 示例与评测集**不能有 id 重合**。

    历史教训：早期版本从评测集里抽示例（排除本次抽样 id），
    在**全量评测**时排除集合等于全部 id ⇒ 示例池为空 ⇒ few-shot 静默退化成 zero-shot，
    两个模式的 micro-F1 一模一样（0.3043）。
    改成独立文件后，用这条测试锁死两者不重合。
    """
    fs_path = EVAL_DIR / "fixtures" / "few_shot_examples.json"
    examples = eb.load_few_shot_examples(fs_path, 10)
    assert examples, "示例文件不能为空"

    cases = json.loads(DATASET_PATH.read_text(encoding="utf-8"))["cases"]
    case_ids = {c["id"] for c in cases}
    assert not ({e["id"] for e in examples} & case_ids), "示例与评测集 id 重合 = 泄题"

    # 文本也不能重复（换个 id 抄同样的话一样是泄题）
    case_texts = {c["text"] for c in cases}
    assert not ({e["text"] for e in examples} & case_texts)


def test_few_shot_k_limits_count():
    fs_path = EVAL_DIR / "fixtures" / "few_shot_examples.json"
    assert len(eb.load_few_shot_examples(fs_path, 2)) == 2
    assert eb.load_few_shot_examples(fs_path, 0) == []


def test_few_shot_mode_with_empty_examples_raises():
    """示例为空时必须**硬失败**，不能静默退化成 zero-shot。

    静默退化比报错危险得多：报告会照常打出 "few-shot" 字样和数字，
    读者无从得知这个对照是假的。
    """
    import argparse
    cases = json.loads(DATASET_PATH.read_text(encoding="utf-8"))["cases"][:2]
    args = argparse.Namespace(mode="few-shot", few_shot_k=3, temperature=0.0,
                              dataset=Path("ds.json"), backend="scripted",
                              scripted_answers="", few_shot_from="")
    with pytest.raises(SystemExit) as exc:
        eb.run_mode(args, {"cases": cases}, cases, eb.ScriptedBackend.__new__(eb.ScriptedBackend),
                    few_shot_examples=[])
    assert "few-shot" in str(exc.value)


def test_stratified_sample_covers_multiple_categories():
    cases = json.loads(DATASET_PATH.read_text(encoding="utf-8"))["cases"]
    got = eb.stratified_sample(cases, 8)
    assert len(got) == 8
    assert len({c["category"] for c in got}) >= 3, "分层抽样没覆盖到多类别"


def test_stratified_sample_limit_larger_than_data():
    cases = json.loads(DATASET_PATH.read_text(encoding="utf-8"))["cases"]
    assert len(eb.stratified_sample(cases, 999)) == len(cases)


# ---------------------------------------------------------------- 端到端 ----

def test_end_to_end_scripted_backend(tmp_path):
    """用预置答案跑通完整流程：读数据 → 生成 → 解析 → 评分 → 汇总 → 出报告。

    这是"框架没坏"的整体证明，也让评分逻辑的改动有回归保护。
    """
    cases = json.loads(DATASET_PATH.read_text(encoding="utf-8"))["cases"][:6]
    answers = {c["id"]: json.dumps(c["expected"], ensure_ascii=False) for c in cases}
    ans_file = tmp_path / "answers.json"
    ans_file.write_text(json.dumps({"answers": answers}, ensure_ascii=False), encoding="utf-8")

    dataset = {"cases": cases}
    args = _make_args(tmp_path, mode="zero-shot", scripted=ans_file)
    report = eb.run_mode(args, dataset, cases, eb.ScriptedBackend(ans_file))

    assert report["metrics"]["n"] == 6
    assert report["metrics"]["micro"]["f1"] == pytest.approx(1.0), "答案与期望一致时应满分"
    assert report["parse_errors"] == 0
    assert report["prompt_mismatch"] is False

    md = eb.render_markdown(report)
    assert "micro-F1" in md and "严格匹配率" in md


def test_end_to_end_scripted_backend_partial(tmp_path):
    """反向对照：答案错一半时，F1 必须明显下降且报告列出失败样例。"""
    cases = json.loads(DATASET_PATH.read_text(encoding="utf-8"))["cases"][:6]
    answers = {}
    for i, c in enumerate(cases):
        if i % 2 == 0:
            answers[c["id"]] = json.dumps(c["expected"], ensure_ascii=False)
        else:
            answers[c["id"]] = "我不会"
    ans_file = tmp_path / "answers_bad.json"
    ans_file.write_text(json.dumps({"answers": answers}, ensure_ascii=False), encoding="utf-8")

    dataset = {"cases": cases}
    args = _make_args(tmp_path, mode="zero-shot", scripted=ans_file)
    report = eb.run_mode(args, dataset, cases, eb.ScriptedBackend(ans_file))

    assert report["metrics"]["micro"]["f1"] < 1.0
    assert report["parse_errors"] == 3
    assert report["parse_error_rate"] == pytest.approx(0.5)
    assert report["failures"], "应记录失败样例"
    assert report["metrics"]["micro"]["f1"] > 0.0, "一半答对不该是 0"


def test_end_to_end_cli_writes_outputs(tmp_path, monkeypatch):
    """CLI 全链路：--out 应同时产出 JSON 与 Markdown，退出码 0。"""
    cases = json.loads(DATASET_PATH.read_text(encoding="utf-8"))["cases"][:4]
    ans_file = tmp_path / "ans.json"
    ans_file.write_text(json.dumps(
        {"answers": {c["id"]: json.dumps(c["expected"], ensure_ascii=False) for c in cases}},
        ensure_ascii=False), encoding="utf-8")
    mini_ds = tmp_path / "mini.json"
    mini_ds.write_text(json.dumps({"cases": cases}, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "out" / "r.json"

    monkeypatch.setattr(eb, "REPO_ROOT", tmp_path)
    code = eb.main([
        "--dataset", str(mini_ds), "--backend", "scripted",
        "--scripted-answers", str(ans_file), "--modes", "zero-shot",
        "--out", str(out),
    ])
    assert code == 0
    assert out.exists()
    assert out.with_suffix(".md").exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["metrics"]["micro"]["f1"] == pytest.approx(1.0)


def test_cli_compare_returns_exit_1_below_threshold(tmp_path, monkeypatch):
    """CI 门禁语义：未达标必须退出码 1（否则"未达标"会被流水线当成通过）。"""
    cases = json.loads(DATASET_PATH.read_text(encoding="utf-8"))["cases"][:6]
    # 一半错 → F1 明显低于 1.0；基线设 0.99 → Δ 为负 → 必不达标
    answers = {c["id"]: (json.dumps(c["expected"], ensure_ascii=False) if i % 2 == 0 else "我不会")
               for i, c in enumerate(cases)}
    ans_file = tmp_path / "ans.json"
    ans_file.write_text(json.dumps({"answers": answers}, ensure_ascii=False), encoding="utf-8")
    mini_ds = tmp_path / "mini.json"
    mini_ds.write_text(json.dumps({"cases": cases}, ensure_ascii=False), encoding="utf-8")

    baseline = tmp_path / "base.json"
    baseline.write_text(json.dumps({"metrics": {"micro": {"f1": 0.99}}}), encoding="utf-8")

    monkeypatch.setattr(eb, "REPO_ROOT", tmp_path)
    code = eb.main([
        "--dataset", str(mini_ds), "--backend", "scripted",
        "--scripted-answers", str(ans_file), "--modes", "zero-shot",
        "--compare", str(baseline), "--threshold", "15",
        "--out", str(tmp_path / "o.json"),
    ])
    assert code == 1


def test_cli_rejects_unknown_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(eb, "REPO_ROOT", tmp_path)
    assert eb.main(["--dataset", str(DATASET_PATH), "--modes", "magic"]) == 2


def test_cli_rejects_missing_dataset(tmp_path, monkeypatch):
    monkeypatch.setattr(eb, "REPO_ROOT", tmp_path)
    assert eb.main(["--dataset", str(tmp_path / "nope.json")]) == 2


def test_scripted_backend_reports_missing_answer(tmp_path):
    """缺答案要明确报错，而不是静默返回空串 —— 静默会让 F1 莫名偏低。"""
    ans_file = tmp_path / "a.json"
    ans_file.write_text(json.dumps({"answers": {}}), encoding="utf-8")
    backend = eb.ScriptedBackend(ans_file)
    raw, _, err = backend.generate({"id": "E01"}, "p")
    assert raw == ""
    assert "E01" in err


# ==================== 传输失败不能算成"模型很差"（C33 实测踩过）====================
#
# 背景：C33 注册模型时 Ollama 0.33 把纯 safetensors 导入的模型交给 MLX runner，
# 每条请求都 HTTP 500。当时脚本只在控制台 print 一行 ⚠️，报告里只剩
# 「micro-F1 = 0.0 / 解析失败 = 24」—— 16 个配置全被算成 0.0，
# 而那一堆 0.0 看起来和"模型真的不会"一模一样。

class _AlwaysFailingBackend:
    """模拟“服务根本没法推理”：每条都报错、raw 为空。"""

    name = "ollama"
    model = "xjt-extract-3b"
    num_ctx = 8192
    last_prompt_tokens = 0

    def generate(self, case, prompt):  # noqa: ARG002
        return "", 0.01, "HTTPError 500: mlx runner failed: MLX not available"


def test_transport_failure_on_every_case_aborts_instead_of_scoring_zero(tmp_path):
    """全部请求失败时必须中止，并把原始错误带出来（而不是给一个 0.0 就算完）。"""
    cases = json.loads(DATASET_PATH.read_text(encoding="utf-8"))["cases"][:4]
    args = _make_args(tmp_path, mode="zero-shot", scripted=tmp_path / "x.json")

    with pytest.raises(SystemExit) as exc:
        eb.run_mode(args, {"cases": cases}, cases, _AlwaysFailingBackend())

    msg = str(exc.value)
    assert "MLX" in msg, "必须带上原始错误，否则排障还得再复现一次"
    assert "4/4" in msg


def test_transport_failure_minority_is_recorded_in_report(tmp_path):
    """少数失败：不中止，但必须写进报告（以前只 print 一行，报告里毫无痕迹）。"""
    cases = json.loads(DATASET_PATH.read_text(encoding="utf-8"))["cases"][:4]
    answers = {c["id"]: json.dumps(c["expected"], ensure_ascii=False) for c in cases[:3]}
    ans_file = tmp_path / "a.json"
    ans_file.write_text(json.dumps({"answers": answers}, ensure_ascii=False), encoding="utf-8")
    bad_id = cases[3]["id"]

    class _OneFails(eb.ScriptedBackend):
        def generate(self, case, prompt):
            if case["id"] == bad_id:
                return "", 0.01, "URLError: connection refused"
            return super().generate(case, prompt)

    args = _make_args(tmp_path, mode="zero-shot", scripted=ans_file)
    report = eb.run_mode(args, {"cases": cases}, cases, _OneFails(ans_file))

    assert report["transport_errors"] == 1
    assert report["errors"][0]["id"] == bad_id
    assert "connection refused" in report["errors"][0]["error"]
    md = eb.render_markdown(report)
    assert "请求失败：1 条" in md
    assert "F1 含失败样本" in md, "报告本身要提醒数字不可尽信"


def test_no_transport_failure_keeps_report_clean(tmp_path):
    """反向对照：全成功时 transport_errors 必须是 0（否则上面两条测试等于空断言）。"""
    cases = json.loads(DATASET_PATH.read_text(encoding="utf-8"))["cases"][:4]
    answers = {c["id"]: json.dumps(c["expected"], ensure_ascii=False) for c in cases}
    ans_file = tmp_path / "ok.json"
    ans_file.write_text(json.dumps({"answers": answers}, ensure_ascii=False), encoding="utf-8")

    args = _make_args(tmp_path, mode="zero-shot", scripted=ans_file)
    report = eb.run_mode(args, {"cases": cases}, cases, eb.ScriptedBackend(ans_file))

    assert report["transport_errors"] == 0
    assert report["errors"] == []
    assert "F1 含失败样本" not in eb.render_markdown(report)


# ---------------------------------------------------------------- 工具 ----

def _make_args(tmp_path, mode: str, scripted: Path):
    import argparse
    return argparse.Namespace(
        mode=mode, few_shot_k=3, temperature=0.0, dataset=tmp_path / "ds.json",
        backend="scripted", scripted_answers=str(scripted),
        few_shot_from=str(EVAL_DIR / "fixtures" / "few_shot_examples.json"),
    )
