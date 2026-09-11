# finetune —— 校园垂直大模型微调（成员3）

> 流程：数据采集 → 清洗脱敏 → 构建指令集（`train.jsonl`）→ QLoRA 训练 → 评估 → 转 GGUF → 发布 Ollama（供后端对话/RAG 使用）。

## 目录与文件

| 文件 | 职责 |
|---|---|
| `build_dataset.py` | **构建指令数据集**：从内置种子问答 + `db/sql/99b_knowledge_faq.sql` 采集，按问法/多轮模板扩增，输出 ≥500 条 `data/train.jsonl`（纯标准库，离线可跑） |
| `data/seed_qa.jsonl` | 内置校园问答种子（图书馆/校医院/校园卡/教务/宿舍/二手/兼职…） |
| `data/train.jsonl` | **构建产物**（默认 810 条，Qwen2.5 对话格式） |
| `train.py` | **QLoRA 训练**（transformers + peft + bitsandbytes，4bit 量化；缺 GPU/依赖时安全退出并提示） |
| `eval.py` | 评估抽样：对测试问句生成回答，供人工打分/记录训练报告 |
| `requirements.txt` | 训练依赖（torch/transformers/peft/bitsandbytes/datasets/accelerate） |

## 快速开始

```bash
# 1) 构建数据集（无需第三方库）
python ai/finetune/build_dataset.py            # → data/train.jsonl（≥500 条）

# 2) 安装训练依赖（GPU 机器 / Colab）
pip install -r ai/finetune/requirements.txt

# 3) QLoRA 训练（8G 显存用 3B/4B；16G 用 7B-4bit）
python ai/finetune/train.py --base_model Qwen/Qwen2.5-3B-Instruct --output ai/finetune/out/qwen3b-lora

# 4) 评估抽样
python ai/finetune/eval.py --base_model Qwen/Qwen2.5-3B-Instruct --model ai/finetune/out/qwen3b-lora --n 10
```

## 数据格式（Qwen2.5 对话格式）

每行一个 JSON：
```json
{"messages":[
  {"role":"system","content":"你是校捷通校园助手…"},
  {"role":"user","content":"图书馆几点关门？"},
  {"role":"assistant","content":"中心图书馆 8:00-22:00 开放…"}
]}
```

## 硬件建议

| 显存 | 建议基座 | 说明 |
|---|---|---|
| 8 GB | Qwen2.5-3B / MiniCPM3-4B | QLoRA 4bit 可训 |
| 16 GB | Qwen2.5-7B-Instruct | QLoRA 4bit 推荐 |
| 无 GPU | — | 仅可跑 `build_dataset.py`；训练用 Colab/租卡 |

## 与后端衔接

1. 训练完成 → 合并 LoRA 权重并导出 GGUF（llama.cpp `convert_hf_to_gguf.py` / `quantize`）；
2. 交后端（成员2）在 **Ollama** 中载入（`ollama create xjt -f Modelfile`）；
3. `backend/app/services/model_client.py` 指向该模型 → `chat/send` 返回真实回答；RAG 检索命中知识库后拼接上下文。

## 备注

- 训练数据含真实用户文本时须**脱敏**（去学号/手机号/姓名）。
- `train.py` 的 loss 目前为整序列（可后续按 assistant 段做 mask 优化）。
- 数据集为演示种子，正式训练前请以官方信息核对知识内容。
