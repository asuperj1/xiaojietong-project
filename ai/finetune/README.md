# finetune —— 大模型微调

流程：数据采集 → 清洗脱敏 → 构建指令集(`train.jsonl`) → QLoRA 训练 → 评估 → 转 GGUF → 发布 Ollama。

- 训练数据从 MySQL `train_corpus / train_annotation` 导出（C++ 层提供批量导出接口）。
- 硬件：QLoRA 4bit 单卡 16G 可训 7B；学生本 8G 显存请用 Qwen2.5-3B / MiniCPM3-4B。
- 训练代码由成员2 在此目录开发（`train.py / build_dataset.py / eval.py`）。
