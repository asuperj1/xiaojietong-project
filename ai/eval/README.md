# eval —— 模型评估与压测

本目录放**可复现的实验脚本**。共同约定（新脚本请遵守）：

1. **docstring 写清楚四件事**：为什么要做 / 指标定义 / 用法 / 退出码。
2. **`--out` 落盘 JSON + Markdown**，JSON 供脚本比对，Markdown 供人看/写进报告。
3. **退出码当 CI 门禁**：未达标返回 1，环境或数据错误返回 2，通过返回 0。
   —— 否则"未达标"会被流水线当成通过。
4. **报告里记相对路径**，不写本机绝对路径（别人打开报告要能据此定位文件）。
5. **`--compare <baseline.json>`** 与上一次结果比增量，而不只看绝对值。

## 已有脚本

| 脚本 | 任务 | 评什么 | 门槛 |
|---|---|---|---|
| `rag_bench.py` | C14 | RAG 检索：hit@1/3/5、MRR、空结果率、负样本误命中、延迟 | `hit@3 ≥ 0.80` |
| `extract_bench.py` | C34 | 信息抽取：字段级/实体级 P/R/F1、micro/macro-F1、严格匹配率 | 相对基线 **ΔF1 ≥ 15 个百分点** |

（`ai/finetune/eval_compare.py` 是 C12 的问答质量对比，放在 `ai/finetune/` 下，不在此表。）

---

## `extract_bench.py`（C34 信息抽取基线对比）

### 要回答的问题
微调（C33）之后，"到底提升了多少"必须能在**同一评测集 + 同一评分函数**下纵向可比。
否则数字会被换数据集、换评分口径污染，答辩一追问就散。

### 实验设计：只让 prompt 和模型变

| 模式 | prompt | 模型 | 作用 |
|---|---|---|---|
| `zero-shot` | 任务说明 + 输出格式 | 基座 | 对照下界 |
| `few-shot` | 再给 k 条示例 | 基座 | 检验"给例子能不能替代微调" |
| `finetuned` | **与 zero-shot 完全一致** | 微调后 | 提升可归因到微调 |
| `rule`（C27，可选） | 不适用 | 无 | 完全离线参照，旁证 F1 是否可信 |

> ⚠️ `finetuned` 的 prompt 必须与 `zero-shot` **逐字节相同**。
> 代码里有 `prompt_mismatch` 自检，测试也有对应断言 ——
> 这是本实验成立的前提，不是风格问题。

### 指标口径

| 字段 | 口径 |
|---|---|
| `category` / `importance` | 精确匹配 |
| `deadline` | 归一化后匹配（只取日期，`2026-09-30 23:59:59` ≡ `2026-09-30`） |
| `entities` | 集合比对，键 = `(type, norm or text)` |

汇总时同时给 **micro-F1（主指标，验收看它）** 与 **macro-F1**：
两者差得远说明模型能力不均衡（微调数据分布有问题），而不是"个别 case 没答对"。
另有**严格匹配率**（整条 JSON 全对）和 **JSON 解析失败率**。

> 解析失败**不计入 F1**，单独统计。因为"格式没守住"和"内容抽错"是两类问题，
> 混进 F1 会让分数被非能力因素拉低。但解析失败时只记 FN（漏抽），不记 FP ——
> 模型并没有编造错答案，它是没给出可用答案。

### 用法

```bash
# ① 离线自检：确认评分函数与评测集自洽（不需要 Ollama）
python ai/eval/fixtures/make_upper_bound.py
python ai/eval/extract_bench.py --backend scripted \
    --scripted-answers ai/eval/fixtures/answers_upper_bound.json \
    --out ai/eval/out/extract_upper_bound.json
# 期望 micro-F1 恰好 = 1.0；若不是 1.0，是评分函数坏了，不是模型差

# ② 真实评测（需 Ollama 已启动）
python ai/eval/extract_bench.py --backend ollama --model qwen2.5:3b \
    --modes zero-shot,few-shot --out ai/eval/out/extract_base.json

# ③ 微调后（prompt 与 ② 完全相同，只换模型）
python ai/eval/extract_bench.py --backend ollama --model xjt-3b \
    --modes finetuned --out ai/eval/out/extract_finetuned.json

# ④ 与基线比（未达 15 个百分点返回退出码 1）
python ai/eval/extract_bench.py --backend ollama --model xjt-3b \
    --modes finetuned --compare ai/eval/out/extract_base.json
```

### 评测集

`extract_cases.json`（24 条）。两个必须知道的事实：

1. **这是合成数据**，不是真实通知；等 C31 工具链 + D12 标注协作产出真实标注后替换。
2. **`reference_date` = 2026-09-16（周三）**，所有相对时间（"本周五"/"下周三"/"即日起两周内"）
   的期望值都以此推算。**换基准日就必须同步改 `expected`** —— 这是评测集必须冻结的原因。

刻意混入的三类难点（缺了评测集就没有区分度）：

| 难点 | 考察什么 |
|---|---|
| `deadline: null` 的样本 | 会不会硬编一个日期（过度抽取） |
| 相对时间表述 | 能否结合基准日推算 |
| 一句话多个时间 | 抽的是"截止"还是别的活动时间 |

### 测试

```bash
python -m pytest ai/eval/tests/test_extract_bench.py -q     # 37 passed
```

测试写得很"不信任自己"：**5 处显式反向对照**，即每个关键断言都配一条"证明它不是恒真的"：

1. 全错答案的 F1 必须真的是 0.0（证明 `prf` 不会恒返回高分）
2. 全错时的 `aggregate` 必须是 0.0
3. Δ 不够时必须判未达标（否则 `--compare` 可能永远返回"通过"）
4. few-shot 的 prompt 必须真的与 zero-shot 不同（否则三种模式没区别）
5. 答案错一半时 F1 必须明显下降，且 > 0

另有 4 处起同等作用的断言：`micro ≠ macro`、解析失败只记 FN 不记 FP、
未达标必须退出码 1、评测集必须含 `null`-deadline 难点样本。

并且用**变异测试**验证过这些断言不是空转：故意破坏评分函数 8 处
（F1 恒为 1、FP/FN 对调、不做归一化、few-shot 失效、finetuned prompt 被改、
解析永不失败、实体键退化为 text、strict 恒真），测试**全部报错**
（`killed=8 survived=0 skipped=0`）。

> 为什么要这么较真：如果评分函数本身写错了，"F1 提升 15 个百分点"这个验收结论就是假的，
> 而且**没人会再回头验证它**。

## 接口压测

`locust` 或脚本对典型查询（列表分页、批量插入）对比 C++ 扩展 vs 直连，产出具说服力的性能数据。

