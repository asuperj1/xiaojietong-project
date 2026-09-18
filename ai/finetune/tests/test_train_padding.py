"""`train.py` 的 batch padding 单测（C33）。

【为什么会有这些测试】
第一次带开发集跑 C33 训练时，**第 50 步的第一次评估直接崩**：

    ValueError: Unable to create tensor ... features (labels) have excessive nesting

根因是 `DataCollatorForLanguageModeling`（transformers 5.17）只 pad `input_ids`/`attention_mask`，
已 tokenize 的 `labels` 原样丢给 `tokenizer.pad` 转 tensor —— batch>1 必崩。
旧脚本 `per_device_train_batch_size=1` 单样本不触发 padding，所以一直没暴露。

因此这里锁住三件事：
1. 长度补齐后三个 key 等宽，补齐位分别是 pad_token_id / 0 / **-100**；
2. `-100` 这一条要能被“改回用 pad_token_id 填 label”的变异杀死（否则等于没测）；
3. 本文件**不需要 torch** —— 没有 GPU 依赖的环境也要能跑（`PadCollator` 里才 import torch）。
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import types
from pathlib import Path

import pytest

TRAIN_PY = Path(__file__).resolve().parents[1] / "train.py"
PAD_TOKEN_ID = 151643  # Qwen2.5 的真实 pad_token_id（= <|endoftext|>）


def _load_train():
    spec = importlib.util.spec_from_file_location("xjt_train_under_test", TRAIN_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


train = _load_train()


def _feat(n: int, base: int) -> dict:
    return {"input_ids": list(range(base, base + n)),
            "attention_mask": [1] * n,
            "labels": list(range(base, base + n))}


def test_pads_all_keys_to_the_longest_in_batch():
    feats = [_feat(3, 100), _feat(7, 200), _feat(5, 300)]
    out = train.pad_batch(feats, PAD_TOKEN_ID)

    assert set(out) == {"input_ids", "attention_mask", "labels"}
    for key in out:
        assert [len(row) for row in out[key]] == [7, 7, 7], f"{key} 未补齐到 batch 最长"


def test_padding_positions_use_pad_mask_and_minus_100():
    """补齐位：input_ids=pad、mask=0、label=-100。"""
    out = train.pad_batch([_feat(2, 100), _feat(4, 200)], PAD_TOKEN_ID)

    # 第 0 条短样本：前 2 位是真实内容，后 2 位是补齐
    assert out["input_ids"][0] == [100, 101, PAD_TOKEN_ID, PAD_TOKEN_ID]
    assert out["attention_mask"][0] == [1, 1, 0, 0]
    assert out["labels"][0] == [100, 101, -100, -100]
    # 第 1 条本来就最长：一个都不补
    assert out["attention_mask"][1] == [1, 1, 1, 1]
    assert -100 not in out["labels"][1]


def test_label_padding_is_not_pad_token_id():
    """变异锁：label 补齐位必须是 -100，不能是 pad_token_id（否则模型会去学 pad token）。"""
    out = train.pad_batch([_feat(1, 10), _feat(3, 20)], PAD_TOKEN_ID)

    tail = out["labels"][0][1:]
    assert tail == [-100, -100]
    assert PAD_TOKEN_ID not in tail
    assert out["labels"][0] != out["input_ids"][0]  # 两个 key 的补齐位语义不同


def test_content_and_order_are_preserved():
    """补齐不能动真实 token：值、顺序、条数都要原样。"""
    feats = [_feat(3, 100), _feat(6, 500)]
    out = train.pad_batch(feats, PAD_TOKEN_ID)

    assert out["input_ids"][0][:3] == [100, 101, 102]
    assert out["labels"][0][:3] == [100, 101, 102]
    assert out["input_ids"][1] == list(range(500, 506))
    assert len(out["input_ids"]) == len(feats)


def test_single_sample_is_untouched():
    """bs=1（训练时就是这么跑的）不产生任何补齐，与旧行为逐字一致。"""
    out = train.pad_batch([_feat(5, 700)], PAD_TOKEN_ID)

    assert out["input_ids"][0] == list(range(700, 705))
    assert out["attention_mask"][0] == [1] * 5
    assert out["labels"][0] == list(range(700, 705))


def test_empty_batch_raises_clear_error():
    with pytest.raises(ValueError, match="空 batch"):
        train.pad_batch([], PAD_TOKEN_ID)


def test_pad_collator_returns_long_tensors(monkeypatch):
    """PadCollator 只负责“补齐 + 转 tensor(long)”，用假 torch 验，避免给测试引入 GPU 依赖。"""
    calls: list[tuple] = []

    def fake_tensor(data, dtype=None):
        calls.append((data, dtype))
        return f"tensor({data}, {dtype})"

    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(tensor=fake_tensor, long="LONG"))
    out = train.PadCollator(PAD_TOKEN_ID)([_feat(1, 1), _feat(3, 2)])

    assert set(out) == {"input_ids", "attention_mask", "labels"}
    assert all(dtype == "LONG" for _, dtype in calls), "必须以 long 建 tensor（token/标签都是整型）"
    assert out["labels"].startswith("tensor([[1, -100, -100]"), "补齐规则没传到 collator"


def test_importing_train_does_not_require_torch():
    """本测试文件不 import torch，也不允许 train.py 在模块级 import torch。

    否则没有 GPU 依赖的 CI 环境根本 collect 不到这些用例（qlora 那套依赖只在训练机上装）。
    """
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('t', r'{TRAIN_PY}')\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n"
        "print('torch' in sys.modules)\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "False", "train.py 模块级 import 了 torch"
