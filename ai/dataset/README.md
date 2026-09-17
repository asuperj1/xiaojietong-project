# 数据采集与标注工具链（`C31`）

校园公告**信息抽取**数据集的生产线：采集 → 去重 → 预标注 → 导出。
面向二阶段科研方向「轻量信息抽取」（`C31`~`C36`），产物直接喂给 `C33` 的微调流水线。

> 数据集本身即成果（可申软著）：**工具链入库，数据集不入库**。
> 产物默认落在 `ai/dataset/out/`，该目录被仓库 `.gitignore` 的 `out/` 规则忽略。

## 快速开始（在**仓库根**执行）

```bash
# 一键：采集 → 去重 → 预标注 → 导出三件套
python -m ai.dataset.cli pipeline --from db --kind notice --limit 200 --out-dir ai/dataset/out

# 分步执行（便于检查每一步的中间产物）
python -m ai.dataset.cli collect --from db  --kind notice --limit 200 --out ai/dataset/out/raw.jsonl
python -m ai.dataset.cli collect --from dir --root docs/kb_samples      --out ai/dataset/out/raw.jsonl
python -m ai.dataset.cli prelabel --in ai/dataset/out/raw.jsonl   --out ai/dataset/out/prelabeled.jsonl
python -m ai.dataset.cli export   --in ai/dataset/out/prelabeled.jsonl --out-dir ai/dataset/out
python -m ai.dataset.cli stats    --in ai/dataset/out/prelabeled.jsonl
```

数据库来源需要 `XJT_DB_*` 环境变量（与后端一致）：

```powershell
$env:XJT_DB_PASSWORD='<your-local-password>'; $env:XJT_DB_PORT='3307'
```

> 采集用的是 `backend/app/db/cpp_bridge`（C++ 层），因此需要
> `backend/app/db/native/jt_db.pyd` 存在 —— 该文件被 gitignore，**每人本地编译**
> （见 `db/cpp_driver/README.md`）。工具链会自行初始化连接池（幂等）。

## 产物三件套

| 文件 | 用途 | 谁消费 |
|---|---|---|
| `train_extract.jsonl` | Qwen 对话格式（`messages`），**只含至少一项标注的样本** | `C33` 微调（与 `ai/finetune/train.py` 的输入格式一致） |
| `labeling.csv` | 人工标注/复核表（UTF-8-**SIG**，Excel 直接打开不乱码） | 标注同学 |
| `stats.md` | 统计报告（状态分布 / 来源 / 实体候选 / 待办） | 论文与软著素材、`C32` 的输入体检 |

## 目录结构

```
ai/dataset/
├── schema.py      # 样本标准格式与校验（唯一真相来源）
├── collect.py     # 采集：本地目录 / 数据库（含 sha256 去重）
├── prelabel.py    # 预标注：可插拔 labeler（默认 keyword）
├── export.py      # 导出：训练 JSONL / 标注 CSV / 统计报告
├── cli.py         # 命令行入口（python -m ai.dataset.cli）
├── tests/         # 离线测试（15 条，不连库、不调模型）
└── out/           # 产物（gitignore）
```

## 样本格式

见 [`schema.py`](./schema.py) 的模块 docstring。要点：

- `labels` 允许为空 —— 所以**采集步骤可以单独跑通**，不强制先标注；
- `annotation.status` 是状态机：`raw` → `prelabeled` → `human` → `reviewed`
  （`C32` 的双人一致性**只在 `human` / `reviewed` 上统计**）；
- 实体带 `norm` 字段存归一化值：原文「9 月 30 日」与归一化 `2026-09-30` 都保留，
  只存归一化会丢掉"模型该学什么字面"的信息。

## 与相邻任务的边界

| 任务 | 关系 |
|---|---|
| `C27` 时间抽取 / `C28` 重要度打分 | **不重复**。它们是生产链路（写库），本工具链是**数据集链路**（产初稿）。`prelabel` 的 labeler 是**可注入**的：合并后可直接 `--labeler app.services.notice_extract:extract_deadline_detail` |
| `C32` 标注一致性 | 消费 `human` / `reviewed` 子集，用导出的 `labeling.csv` 做双人标注 |
| `C33` 抽取模型微调 | 直接吃 `train_extract.jsonl`（`messages` 格式，零改动复用 `ai/finetune/`） |

## 已知局限（诚实清单）

1. 内置 `KeywordTagger` 是**候选高亮**，不是标注：同一词可能被同时标为 `place` 与
   `matter`（如"宿舍"），且不发散、不做同义归并 —— 这正是需要人工修订的原因。
2. 它**不做时间归一化**（那是 `C27` 的职责），`entities[].norm` 一律为 `null`。
3. 预标注对 `human` / `reviewed` 样本**整个跳过**（含候选也不补）——
   这是比"只填空位"更强的保证：重跑预标注永远不会碰人工成果。
4. `collect --from db` 目前只取 `campus_notice` / `knowledge_doc`；
   网页采集（`B27` 配置驱动采集器）打通后可作为第三个来源接入。

## 标注质量校验与数据卡（`C32`）

`C31` 解决了"怎么产出数据"，`C32` 回答"**这批数据能不能用**"。
验收口径来自任务单：**Cohen's Kappa ≥ 0.8**。

### 为什么必须是"两份标注文件"

`Sample.labels` 只存**一份**标注，做不了一致性统计。所以 `quality` 子命令的输入是
**标注者 A / B 各一份 JSONL**，按 `id` 配对后逐字段比对：

```bash
# 双人一致性（Kappa + 实体 P/R/F1）
python -m ai.dataset.cli quality \
    --a ai/dataset/out/annotator_a.jsonl \
    --b ai/dataset/out/annotator_b.jsonl \
    --fields category,importance,deadline \
    --json ai/dataset/out/quality.json \
    --out  ai/dataset/out/quality.md
```

- 退出码：**0 = 全部字段达标；1 = 有字段未达标**（便于 CI 判失败）
- 只统计 `human` / `reviewed` 状态的样本 —— `prelabeled` 是机器初稿，
  算进去只会得到虚高的 Kappa
- 三类字段用三种口径：
  | 字段 | 口径 | 说明 |
  |---|---|---|
  | `category` | Cohen's Kappa | 离散分类 |
  | `importance` | Cohen's Kappa | 1~5 离散 |
  | `deadline` | Kappa（先归一化） | `2026-09-30 23:59:59` 与 `2026-09-30` 算一致 |
  | `entities` | **P/R/F1** | 集合不适用 Kappa；键为 `(type, norm or text)` |

### 数据卡

```bash
python -m ai.dataset.cli datacard \
    --in ai/dataset/out/annotator_a.jsonl \
    --name xjt-extraction --version v0.1 \
    --split "train=0.8,dev=0.1,test=0.1" --split-dir ai/dataset/out/splits \
    --quality-a ai/dataset/out/annotator_a.jsonl \
    --quality-b ai/dataset/out/annotator_b.jsonl \
    --json ai/dataset/out/datacard.json \
    --out  ai/dataset/out/datacard.md
```

数据卡含四节：**规模**（条数/状态/文本长度分位）· **分布**（来源/分类/重要度/实体类型）·
**划分**（train/dev/test 条数与占比）· **标注质量**（Kappa 表 + 实体 F1）。

> 划分用固定 `seed` + 按 `id` 排序后切分 ⇒ **同输入必然同输出**。
> 否则 `C34` 的基线对比不可复现："F1 提升 15 个百分点"可能是换了测试集的产物。

### 边界约定（容易写错的地方）

- `pe == 1`（双方都只用了同一个标签）→ Kappa 数学上无定义：
  完全一致给 `1.0`，否则给 `0.0`，并在报告里标 `degenerate=True`
- 配对为空（没有共同 `id`）→ 返回 `None` 而不是 `0.0`，
  避免"没数据"被误读成"一致性极差"
- 分歧样例最多列 50 条（避免报告刷屏），JSON 报告里保留同量

## 测试

```bash
python -m pytest ai/dataset/tests -q      # 40 passed（C31: 15 + C32: 25），全程离线
```

`C32` 的测试里有两条**反向对照**，用来证明断言不是恒真的：

1. 高一致数据的 Kappa 必须**显著高于**低一致数据，且低一致必须**低于阈值**
2. 换 `seed` 后划分结果必须**不同**（证明 seed 真的起作用）

---

## `synth_notice.py`（C33 合成语料生成器）

### 为什么要“造”数据，而不是去标
真正实测过的家底（2026-09-17）：`campus_notice` 表**只有 10 行**，正文**全部 < 60 字**；
`deadline`/`importance` 列虽已建但**全为 NULL**。⇒ **真实原料不足以训练**，
连"模型预标注（`prelabel`）"那条路也只能标出 10 条；而且预标注会把模型当前的错误学进去
（本任务恰恰是要修它在**年份/importance/类目**上的错），还得人工复核 —— 质量说不清。
⇒ 改为**程序构造**：**标签由构造过程保证正确**，不需要人工，且能按需覆盖整个标签空间。

### 覆盖矩阵（缺一项就是在训练集里留盲区）
| 维度 | 覆盖 |
|---|---|
| `category` | 5 类全覆盖 |
| `importance` | 3 / 4 / 5（与评测集一致；1/2 从未出现过，训出来只会制造 FP） |
| 时间表达 | 绝对(带年/无年/ISO) · 相对(本周五/下周一/下周三/即日起两周内) · **跨年** · 多时间 · **无时间** |
| 实体数 | 0 / 1 / 2 / 3，按 `ENTITY_MIX` 分配名额（陷阱样本占 11%+18%+11%） |

三类**陷阱样本**（C34 已实证模型在这三处出错）：无时间要求（`deadline` 必须 `null`）、
一句话多个时间（要抽全，`deadline` 取约定那个）、只有活动时间没有报名截止。

`deadline` 取法按类约定：`cutoff_last`（报名/申请 ⇒ 截止日）、`start_first`（放假/活动 ⇒ 开始日）、
`none`（纯通报 ⇒ `null`）—— 与评测集口径一致。

### 基准日必须与评测集一致
相对时间全部按 `REFERENCE_DATE = 2026-09-16`（周三）推算，它**必须等于**
`ai/eval/extract_cases.json` 的 `reference_date`，否则"训练教的东西"和"评测量的事情"不是一回事（有测试守着）。
⚠️ 副作用：固定基准日可能让模型**过拟合到 2026 年**（忽略 prompt 里的"今天"），
已混入含显式年份的绝对时间样本缓解，但**不能消除** —— 写进训练报告的局限里。

### 与留出集的隔离（两道闸）
生成器自带 `similarity_report`：与 **C34 评测集 24 条 + C34/C35 示例池 16 条**比字符 3-gram 重合。
实测（800 条）：**正文零重合；最大 3-gram 覆盖 0.309、Jaccard 0.167**（阈值 0.6）。

> ⚠️ **闸门判据是 `coverage`（留出文本被训练样本覆盖的比例 = `∩ / |留出|`），不是 Jaccard。**
> Jaccard 会被**文本长度稀释**：把留出原文原样抄进一条更长的合成文本时，
> 它随训练文本变长单调下降 —— 实测填充 50 字时 Jaccard 已降到 **0.524 < 0.6**（旧闸门**放行**），
> 而 coverage 仍是 **1.0**（确实被完整抄了）。两者都在报告里列出，但**只有 coverage 当闸门**。
> 回归见 `tests/test_synth_notice.py::test_copy_hidden_in_longer_text_is_still_caught`。

第二道闸在 `ai/finetune/build_extract_dataset.py`：写盘前再查一次，覆盖超阈值则退出码 2、不写任何文件。

### train/dev 怎么切（依据 + 自检）
**依据**：`stratified_split()` 按 `tags["kind"]`（生成器的 12 类模板族）分层 ——
同层内用带种子的洗牌，再按 `dev_ratio`（默认 0.2）切，最后两边各自再洗一次。
**不是**按生成顺序切（否则 dev 可能整类缺失，早停选的 checkpoint 就不可信）。

**自检**（`split_report()`，构建器写盘前的闸门）：两个维度（`tags.kind` / `expected.category`）
分别比两边**占比**（条数天然差 `dev_ratio` 倍，不可直接比），两条硬判据：
1. 每个类型**两边都得有**；
2. 占比最大差 ≤ `max(5 个百分点, 2/|dev|)`（阈值按实测标定：800 条规模实测最大差 **0.004**，
   40 条规模实测 0.094 且**有 2 类没进 dev** → 被第 1 条拦下）。

超限 ⇒ 退出码 2、不写文件；通过则把 `split.by_kind` / `split.by_category` / `dev_share_max_gap`
写进 `ai/finetune/data/extract_dataset.json`，**证据可核对**，不靠口头声明。
回归见 `tests/test_extract_dataset_split.py`（7 例，含 3 处反向对照 + 3 组变异全被杀）。

### 用法
```bash
python ai/dataset/synth_notice.py --count 800 --json ai/eval/out/synth_stats.json   # 离线自检 + 统计
python -m pytest ai/dataset/tests/test_synth_notice.py -q                            # 33 例
```

测试里五处**反向对照**：换 seed 必须变、每类坏标签（片段不在正文/越界类目/越界重要度/
无时间却给 deadline/实体数不符/norm 非法）必须被 `validate` 报出、把留出集正文原样塞进语料时
重合度检查必须报警、`validate` 不能恒返回空、同 seed 必须逐字节可复现。
