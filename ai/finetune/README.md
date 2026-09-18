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

---

## C33 抽取专用微调（`xjt-extract-3b`）

前面的流水线是本仓的 **对话**微调（C12/C5，产物 `xjt-3b`）。C33 复用同一套 QLoRA 流程，
只换**数据**与**输出目录**，训一个**抽取专用**模型 —— 它同时是 C36（抽取服务化）要指向的模型。

### 为什么需要专用模型（有实测依据）
- C34 实测：把**对话微调**的 `xjt-3b` 拿来抽取，micro-F1 只有 0.2418，比基座还低；
- C35 实测：同一个 `xjt-3b` 只要 prompt 写好就能到 **0.4795**（≈ 基座 + 优 prompt 的 0.4767）
  ⇒ "微调后更差"主要来自 prompt，但**对话微调本身不提供抽取能力增益**。
- ⇒ 要真正超过"基座 + 好 prompt"，需要**抽取专用**微调。这就是 C33 的立项理由。

### 数据从哪来：**合成**（实测家底后被迫如此）
`campus_notice` 真实表**只有 10 行且正文 < 60 字** ⇒ 真实原料不足以训练，
预标注也走不通（最多 10 条，且会把模型现有错误学进去）。
⇒ 用 `ai/dataset/synth_notice.py` 程序构造 **800 条**（标签由构造保证正确）。

```bash
# 1) 构建数据集（离线，不需 GPU/网络）
python ai/finetune/build_extract_dataset.py --count 800
#    → data/extract_train.jsonl（641）· data/extract_dev.jsonl（159）· data/extract_dataset.json（元数据）

# 2) 训练（QLoRA 4bit；⚠️ 训练前先卸载 Ollama 模型，否则 16GB 显存会被挤爆）
ollama stop xjt-3b
python ai/finetune/train.py --base_model <本地基座路径> \
    --data ai/finetune/data/extract_train.jsonl \
    --output ai/finetune/out/xjt-extract-3b --epochs 3

# 3) 合并 + 注册进 Ollama
python ai/finetune/merge_lora.py --base_model <基座> --lora ai/finetune/out/xjt-extract-3b \
    --output ai/finetune/out/xjt-extract-3b-merged
#    再用 ai/finetune/register_model.py / Modelfile 注册为 xjt-extract-3b

# 4) 评测（C34 的工具直接复用，只换模型名）
python ai/eval/extract_bench.py --backend ollama --model xjt-extract-3b \
    --modes zero-shot,few-shot,finetuned
```

### 三条硬约束（不遵守，结论就不成立）
1. **训练样本的 `system` 必须是 `ai/eval/extract_bench.py::SYSTEM_PROMPT` 原文**
   （C34 要求 `finetuned` 与 `zero-shot` 的 prompt 逐字节相同，它有 `prompt_mismatch` 自检）。
   构建器把这个常量的 **sha1 写进 `data/extract_dataset.json`**，可与评测报告里的
   `system_prompt_sha1` 直接对比。
2. **留出集一条都不许进训练**：C34 评测集 24 条 + C34/C35 示例池 16 条（构建器有两道闸）。
3. **基准日与评测集一致**（2026-09-16），否则相对时间的期望值就不是一回事。
4. **train/dev 按 `tags.kind` 分层切，并且分布要自检**：构建器算两个维度（`tags.kind` /
   `category`）的占比差，每个类型两边都得有、占比差 ≤ `max(5pp, 2/|dev|)`，超限直接退出码 2。
   实测：800 条规模最大差 **0.004**；40 条规模实测 0.094 且 **2 类没进 dev** ⇒ 被拦。
   明细写进 `data/extract_dataset.json` 的 `split.by_kind` / `split.by_category`。
   为什么不能只看 test loss：若切分按生成顺序而非分层，dev 的分布就可能与 train 不同，
   “dev 指标”就不代表泛化（回归见 `ai/dataset/tests/test_extract_dataset_split.py`）。

### 达标线（比任务书更严，建议按这个验收）
- ① 同 prompt（`zero-shot`）下微调模型 ≥ **0.4767**（C35 的"基座 + 优 prompt"）——
  否则"写好 prompt 就行"，微调没有存在意义；
- ② `zero-shot` 下比基座 0.3043 提升 **≥ 15 个百分点**（对齐 C34 口径，且证明**能力进了权重**）；
- ③ 附消融：同数据只给 few-shot、不训练，证明起作用的是梯度而不是上下文。

达不到 ① 就**如实写"此规模下提示工程比微调更划算"** —— 那也是有价值的结论。

### 已知局限（写进训练报告，不许省略）
- 训练集**合成**、评测集**人工编写**（同域不同源）⇒ 泛化性**未验证**（真实样本仅 10 条，只做定性检查）。
- 固定基准日可能让模型过拟合到 2026 年。
- `importance` 标注边界本身模糊（评漇集里 E02 记 4、E17 记 5）⇒ 该字段上限受标注一致性限制。
- `train.py` 整序列 loss ⇒ 模型也会学"生成正文"，抽取任务的更优做法是只对 assistant 段算 loss。
