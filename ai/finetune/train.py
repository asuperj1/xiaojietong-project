#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QLoRA 微调脚本骨架（C1）。

依赖（见 requirements.txt，需 GPU 环境）：
    pip install -r requirements.txt

用法示例：
    # 8G 显存（3B/4B）
    python train.py --base_model Qwen/Qwen2.5-3B-Instruct --output out/qwen3b-lora
    # 16G 显存（7B 4bit）
    python train.py --base_model Qwen/Qwen2.5-7B-Instruct --output out/qwen7b-lora

    # C33 抽取微调：带开发集评估 + 早停（dev 切分就是给它用的，见 build_extract_dataset.py）
    python train.py --base_model D:/models/Qwen2.5-3B-Instruct \
        --data ai/finetune/data/extract_train.jsonl \
        --eval_data ai/finetune/data/extract_dev.jsonl \
        --output ai/finetune/out/xjt-extract-3b --epochs 3 --early_stopping_patience 3

说明：本脚本在缺少 GPU/依赖时会给出清晰提示并安全退出，不会破坏环境。
训练产物为 LoRA adapter；合并与 GGUF 导出见 train.py 末尾提示 / ai/edge。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

DEP_HINT = "缺少依赖。请在训练环境执行： pip install -r ai/finetune/requirements.txt"


def require(mod: str):
    try:
        return __import__(mod)
    except ImportError:
        print(f"[依赖缺失] {mod}. {DEP_HINT}")
        sys.exit(2)


def load_jsonl(path: Path) -> list[dict]:
    import json
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def pad_batch(features: list[dict], pad_token_id: int,
              label_pad_id: int = -100) -> dict[str, list[list[int]]]:
    """把同一 batch 里长度不一的样本右补齐；**补齐位的 label 用 -100，不参与 loss**。

    为什么不直接用 DataCollatorForLanguageModeling（transformers 5.17 实测）：
    它只 pad `input_ids`/`attention_mask`，把已经 tokenize 好的 `labels` 原样交给
    `tokenizer.pad` 去转 tensor，于是 **batch>1 直接抛**
    `ValueError: Unable to create tensor ... features (labels) have excessive nesting`。
    旧脚本用 `per_device_train_batch_size=1` 跑，单样本不需要 padding，所以这个坑一直没暴露；
    加上评估（默认 eval batch=8）后，就炸在第 50 步的第一次评估上。

    这里顺手把补齐位的 label 写成 -100：旧写法即使不崩，也会让模型去拟合 pad token。
    """
    if not features:
        raise ValueError("pad_batch 收到空 batch")
    width = max(len(f["input_ids"]) for f in features)
    out: dict[str, list[list[int]]] = {"input_ids": [], "attention_mask": [], "labels": []}
    for f in features:
        n = width - len(f["input_ids"])
        out["input_ids"].append(list(f["input_ids"]) + [pad_token_id] * n)
        out["attention_mask"].append(list(f["attention_mask"]) + [0] * n)
        out["labels"].append(list(f["labels"]) + [label_pad_id] * n)
    return out


class PadCollator:
    """把 pad_batch 的结果转成 tensor（torch 推迟到 __call__ 才 import，便于无 GPU 单测）。"""

    def __init__(self, pad_token_id: int, label_pad_id: int = -100) -> None:
        self.pad_token_id = pad_token_id
        self.label_pad_id = label_pad_id

    def __call__(self, features: list[dict]) -> dict:
        import torch

        padded = pad_batch(features, self.pad_token_id, self.label_pad_id)
        return {k: torch.tensor(v, dtype=torch.long) for k, v in padded.items()}


def main() -> None:
    ap = argparse.ArgumentParser(description="校捷通 QLoRA 指令微调")
    ap.add_argument("--base_model", default="Qwen/Qwen2.5-7B-Instruct")
    ap.add_argument("--data", default=str(HERE / "data" / "train.jsonl"))
    ap.add_argument("--output", default=str(HERE / "out" / "lora-adapter"))
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--batch_size", type=int, default=1)
    ap.add_argument("--grad_accum", type=int, default=8)
    ap.add_argument("--max_len", type=int, default=1024)
    ap.add_argument("--lora_r", type=int, default=16)
    ap.add_argument("--lora_alpha", type=int, default=32)
    ap.add_argument("--lora_dropout", type=float, default=0.05)
    # ---- 开发集评估 / 早停（C33 的 dev 切分就是为这两件事建的）----
    # 不传 `--eval_data` 时**行为与旧版逐字一致**（`eval_strategy="no"`，无早停）。
    ap.add_argument("--eval_data", default="",
                    help="开发集 JSONL（与 --data 同格式）；给了就按步评估、保存最优 checkpoint")
    ap.add_argument("--eval_steps", type=int, default=50,
                    help="每多少步评估一次（同时作为 save_steps，两者必须整除）")
    ap.add_argument("--early_stopping_patience", type=int, default=0,
                    help=">0 时启用早停（需 --eval_data）：连续 N 次评估无改善就停")
    ap.add_argument("--eval_batch_size", type=int, default=8,
                    help="评估时的 batch 大小（评估不反传，batch 大些更快）")
    args = ap.parse_args()

    if args.early_stopping_patience > 0 and not args.eval_data:
        print("[错误] --early_stopping_patience 需要 --eval_data（没有评估就无从判断“无改善”）")
        sys.exit(1)

    # 依赖检查（无 GPU 时不强行运行，避免误报）
    import torch

    if not torch.cuda.is_available():
        print("[提示] 未检测到 CUDA GPU。QLoRA 通常需 GPU（学生机 8G 可用 3B/4B，16G 可用 7B-4bit）。")
        print("       本脚本没有 CPU 降级开关：qlora/bnb 依赖 CUDA，无 GPU 时请勿硬跑，正式训练请在有 GPU 的机器上执行。")

    transformers = require("transformers")
    require("peft")
    require("bitsandbytes")
    require("datasets")

    from datasets import Dataset
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig,
                              Trainer, TrainingArguments)

    data_path = Path(args.data)
    if not data_path.exists():
        print(f"[错误] 训练数据不存在：{data_path}\n       请先运行： python build_dataset.py")
        sys.exit(1)

    tokenizer = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    def to_ids(example):
        text = tokenizer.apply_chat_template(example["messages"], tokenize=False, add_generation_prompt=False)
        enc = tokenizer(text, truncation=True, max_length=args.max_len, padding=False)
        enc["labels"] = list(enc["input_ids"])  # 简化：整序列 loss（可后续按 assistant 段做 mask）
        return enc

    raw = Dataset.from_list(load_jsonl(data_path))
    ds = raw.map(to_ids, remove_columns=raw.column_names)

    eval_ds = None
    if args.eval_data:
        eval_path = Path(args.eval_data)
        if not eval_path.exists():
            print(f"[错误] 开发集不存在：{eval_path}")
            sys.exit(1)
        eval_raw = Dataset.from_list(load_jsonl(eval_path))
        eval_ds = eval_raw.map(to_ids, remove_columns=eval_raw.column_names)
        print(f"  训练集 {len(ds)} 条 · 开发集 {len(eval_ds)} 条（每 {args.eval_steps} 步评估）")

    bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                             bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True)
    model = AutoModelForCausalLM.from_pretrained(args.base_model, quantization_config=bnb,
                                                 device_map="auto", trust_remote_code=True)
    model = prepare_model_for_kbit_training(model)
    lora = LoraConfig(r=args.lora_r, lora_alpha=args.lora_alpha, lora_dropout=args.lora_dropout,
                      bias="none", task_type="CAUSAL_LM",
                      target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"])
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    training_args = TrainingArguments(
        output_dir=args.output,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        logging_steps=10,
        save_steps=args.eval_steps if eval_ds else 200,
        # load_best_model_at_end=True 时**不能**限制保留数量：save_total_limit 会把
        # 早期的最优 checkpoint 删掉，最后回载时报 “best model checkpoint not found”。
        # adapter 只有几十 MB，宁可多留几个（本任务 243 步 → 最多 4 个）。
        save_total_limit=2 if not eval_ds else None,
        per_device_eval_batch_size=args.eval_batch_size,
        bf16=True,
        warmup_steps=20,
        lr_scheduler_type="cosine",
        report_to="none",
        # 开发集：按 eval_steps 评估，并**保存最优**而不是最后一个（早停选的 checkpoint 才可信）
        eval_strategy="steps" if eval_ds else "no",
        eval_steps=args.eval_steps,
        load_best_model_at_end=bool(eval_ds),
        metric_for_best_model="eval_loss",
        greater_is_better=False,
    )
    callbacks = []
    if eval_ds and args.early_stopping_patience > 0:
        from transformers import EarlyStoppingCallback

        callbacks.append(EarlyStoppingCallback(
            early_stopping_patience=args.early_stopping_patience))
        print(f"  早停：连续 {args.early_stopping_patience} 次评估无改善即停")
    trainer = Trainer(model=model, args=training_args, train_dataset=ds,
                      eval_dataset=eval_ds, callbacks=callbacks,
                      data_collator=PadCollator(tokenizer.pad_token_id))
    trainer.train()
    trainer.save_model(args.output)
    tokenizer.save_pretrained(args.output)
    if eval_ds:
        # 训练报告需要这三个数：最优 checkpoint 的 dev loss、总步数、是否早停
        metrics = trainer.evaluate()
        print(f"  开发集最终 eval_loss = {metrics.get('eval_loss'):.4f}"
              f"（best_metric = {trainer.state.best_metric} @ step {trainer.state.best_model_checkpoint}）")
        print(f"  实际训练步数 = {trainer.state.global_step}"
              f"（{'早停触发' if trainer.state.global_step < args.epochs * len(ds) / max(1, args.batch_size * args.grad_accum) else '跑满'}）")
    print(f"✅ LoRA adapter 已保存：{args.output}")
    print("下一步：合并权重并导出 GGUF（可选）→ 交后端在 Ollama 载入；评估用 eval.py。")


if __name__ == "__main__":
    main()
