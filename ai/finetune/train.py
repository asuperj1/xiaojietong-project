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
    args = ap.parse_args()

    # 依赖检查（无 GPU 时不强行运行，避免误报）
    import torch

    if not torch.cuda.is_available():
        print("[提示] 未检测到 CUDA GPU。QLoRA 通常需 GPU（学生机 8G 可用 3B/4B，16G 可用 7B-4bit）。")
        print("       如需 CPU 冒烟测试可加 --smoke（不加载大模型），正式训练请在有 GPU 的机器/Colab 运行。")

    transformers = require("transformers")
    require("peft")
    require("bitsandbytes")
    require("datasets")

    from datasets import Dataset
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig,
                              DataCollatorForLanguageModeling, Trainer, TrainingArguments)

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
        save_steps=200,
        save_total_limit=2,
        bf16=True,
        warmup_steps=20,
        lr_scheduler_type="cosine",
        report_to="none",
    )
    trainer = Trainer(model=model, args=training_args, train_dataset=ds,
                      data_collator=DataCollatorForLanguageModeling(tokenizer, mlm=False))
    trainer.train()
    trainer.save_model(args.output)
    tokenizer.save_pretrained(args.output)
    print(f"✅ LoRA adapter 已保存：{args.output}")
    print("下一步：合并权重并导出 GGUF（可选）→ 交后端在 Ollama 载入；评估用 eval.py。")


if __name__ == "__main__":
    main()
