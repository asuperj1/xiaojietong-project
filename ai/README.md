# ai —— AI 能力目录（成员2/3 联合负责）

| 子目录 | 内容 | 状态 |
|---|---|---|
| `finetune/` | 微调训练：数据构建、LoRA/QLoRA 训练脚本 | 规划 |
| `rag/` | RAG：知识库分块、embedding、检索 | 规划 |
| `edge/` | 端侧 llama.cpp 离线 demo（GGUF） | 规划 |
| `eval/` | 模型评估与接口压测 | 规划 |

技术基线：基座 `Qwen2.5-7B-Instruct`（备选 MiniCPM3-4B）；`PEFT + BitsAndBytes` QLoRA；`bge-m3` embedding；`ChromaDB/FAISS` 向量库；`Ollama/llama.cpp` 推理。详见 `docs/architecture.md` 第 7 章。
