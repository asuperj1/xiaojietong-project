#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合并 LoRA adapter 到基座模型（C5 第 1 步）。

把 QLoRA 训练得到的 adapter 合并进基座权重，输出可独立加载的完整模型目录，
供后续导出 GGUF（llama.cpp）使用。

用法：
    python merge_lora.py \
      --base_model E:/models/Qwen2.5-3B-Instruct \
      --adapter ai/finetune/out/qwen3b-lora \
      --output E:/models/xjt-3b-merged
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(description="合并 LoRA 到基座模型")
    ap.add_argument("--base_model", required=True, help="基座模型路径或 HF id")
    ap.add_argument("--adapter", required=True, help="LoRA adapter 路径")
    ap.add_argument("--output", required=True, help="输出合并模型目录")
    ap.add_argument("--dtype", default="bfloat16", choices=["bfloat16", "float16", "float32"])
    args = ap.parse_args()

    try:
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError:
        print("缺少依赖：conda activate xjt-train && pip install -r ai/finetune/requirements.txt")
        sys.exit(2)

    dtype = {"bfloat16": torch.bfloat16, "float16": torch.float16, "float32": torch.float32}[args.dtype]
    print(f"[1/3] 加载基座：{args.base_model}")
    base = AutoModelForCausalLM.from_pretrained(args.base_model, torch_dtype=dtype, device_map="cpu",
                                                trust_remote_code=True)
    tokenizer = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)

    print(f"[2/3] 载入并合并 LoRA adapter：{args.adapter}")
    model = PeftModel.from_pretrained(base, args.adapter)
    model = model.merge_and_unload()

    print(f"[3/3] 保存合并模型 → {args.output}")
    Path(args.output).mkdir(parents=True, exist_ok=True)
    model.save_pretrained(args.output, safe_serialization=True)
    tokenizer.save_pretrained(args.output)
    print("✅ 合并完成。下一步：转 GGUF（llama.cpp convert_hf_to_gguf.py）。")


if __name__ == "__main__":
    main()
