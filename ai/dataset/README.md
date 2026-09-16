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

## 测试

```bash
python -m pytest ai/dataset/tests -q      # 15 passed，全程离线
```
