#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微调效果评估脚本骨架（C1 配套）。

用法：
    # 用内置测试问句批量生成，人工打分/记录
    python eval.py --model out/qwen3b-lora --base_model Qwen/Qwen2.5-3B-Instruct --n 10

说明：无 GPU/依赖时安全退出并给出提示。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

TEST_QUESTIONS = [
    "图书馆几点关门？",
    "本科生能借几本书？",
    "校园卡丢了怎么办？",
    "校医院怎么报销？",
    "什么时候选课？",
    "宿舍几点熄灯？",
    "校车多久一班？",
    "二手交易要注意什么？",
    "兼职岗位可信吗？",
    "怎么预约图书馆座位？",
]


def main() -> None:
    ap = argparse.ArgumentParser(description="校捷通微调模型评估（生成式抽样）")
    ap.add_argument("--base_model", default="Qwen/Qwen2.5-3B-Instruct")
    ap.add_argument("--model", default="", help="LoRA adapter 路径（留空则仅评估 base）")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--max_new_tokens", type=int, default=200)
    args = ap.parse_args()

    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError:
        print("缺少依赖：请先 pip install -r ai/finetune/requirements.txt")
        sys.exit(2)

    if not torch.cuda.is_available():
        print("[提示] 未检测到 CUDA GPU，CPU 推理 3B/7B 会很慢；建议在 GPU 机器运行。")

    tokenizer = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(args.base_model, device_map="auto", trust_remote_code=True)
    if args.model:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, args.model)
    model.eval()

    qs = TEST_QUESTIONS[: max(1, args.n)]
    for i, q in enumerate(qs, 1):
        msgs = [{"role": "system", "content": "你是校捷通校园助手，服务吉林大学学生。"},
                {"role": "user", "content": q}]
        text = tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(text, return_tensors="pt").to(model.device)
        with torch.no_grad():
            out = model.generate(**inputs, max_new_tokens=args.max_new_tokens, do_sample=False)
        ans = tokenizer.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        print(f"\n[{i}] Q: {q}\n    A: {ans.strip()}")
    print("\n完成。请按准确性/完整性/口吻打分并记录到训练报告。")


if __name__ == "__main__":
    main()
