# eval —— 评估与基准

本目录承载**可离线评估**的度量工具（按 `docs/成员2与3两阶段任务分工.md` 的判据：
"能离线评估的归成员3"）。

## 现有工具

### `rag_bench.py` —— RAG 检索质量评估（任务 C14）

用标注问答集量化检索质量，作为 C15（切片可插拔）/ C16（检索重排）的效果基准。

```powershell
# 前置：MySQL 已起、jt_db 扩展已编译、Ollama 已装 bge-m3
$env:XJT_DB_PASSWORD="<你的MySQL密码>"; $env:XJT_DB_PORT="3307"
E:/miniconda3/python.exe -X utf8 ai/eval/rag_bench.py --out ai/eval/out/rag_baseline.json
# 与基线对比（C15/C16 改完后）
E:/miniconda3/python.exe -X utf8 ai/eval/rag_bench.py --compare ai/eval/out/rag_baseline.json
```

| 指标 | 含义 | 目标 |
|---|---|---|
| hit@1 / hit@3 / hit@5 | 检索结果 top-k 内命中期望文档的比例 | **hit@3 ≥ 80%**（方案验收） |
| MRR@5 | 平均倒数排名（未命中记 0） | 越高越好 |
| 空结果率 | 返回 0 条的比例 | 越低越好 |
| 负样本误命中率 | 未收录问题仍返回结果的比例 | 越低越好（防编造引用） |
| 延迟 | 平均 / p50 检索耗时 | 参考 |

退出码：`hit@3` 低于 `--threshold`（默认 0.80）返回 1，可用于 CI 门禁。

数据集：`rag_questions.json`（25 条可命中 + 2 条负样本，覆盖 23 篇知识库文档）。

**注意**：进程内直连（不依赖后端 HTTP 服务），需要 C++ 连接池与数据库可用；
若 Ollama / `bge-m3` 不可用，脚本会走**关键词降级**并在报告中明确标注，此时指标
**不代表向量检索真实水平**。

**⚠️ 运行目录（重要）**：`rag_vector_dir` 是**相对路径**（默认 `data/rag`）。
建索引（`ai/rag/build_index.py`）与评估（`rag_bench.py`）**必须在同一工作目录**下运行，
否则会读写**不同的向量库目录**，表现为"检索全空"。推荐统一在 `backend/` 下执行：

```powershell
cd backend
$env:XJT_DB_PASSWORD="..."; $env:XJT_DB_PORT="3307"
E:/miniconda3/python.exe -X utf8 ..\ai\rag\build_index.py --force      # ① 建索引
E:/miniconda3/python.exe -X utf8 ..\ai\eval\rag_bench.py --out ..\ai\eval\out\rag_baseline.json  # ② 评估
```

## 基线（2026-09-12）

环境：27 篇知识库文档、`bge-m3`、top-k=3、向量检索全部生效（`vector+fallback`）。

| 指标 | 数值 | 判定 |
|---|---|---|
| hit@1 | 92.0% | — |
| **hit@3** | **100.0%** | ✅ 达标（≥80%） |
| hit@5 | 100.0% | ✅ |
| MRR@5 | 0.953 | 好 |
| 空结果率 | 0.0% | ✅ |
| **负样本误命中率** | **100.0%** | ⚠️ **待改进** |
| 延迟 | avg 692ms / p50 **43.5ms** | avg 含首次模型加载 |

**结论与后续**：
1. ✅ 检索质量已达方案验收标准（hit@3 100%），可作为 C15/C16 的基线。
2. ⚠️ **负样本误命中率 100%**：知识库未收录的问题（如"计算机学院院长办公室电话"）
   仍会返回 top-3 结果 —— 说明 `rag_score_threshold = 0.35` **偏低**，
   且这正是 **C20（引用反向校验）** 要解决的核心问题（防编造引用）。
3. 🎯 优化空间已在基线中定位：`Q08`（饭卡挂失）rank=2、`Q10`（宿舍电器）rank=3
   —— 属 **C16 检索重排** 的明确靶点。

## 规划中

- `extract_bench.py` —— 信息抽取基线对比实验（任务 C25：零样本 / few-shot / 微调三档，F1 提升 ≥ 15pp）
- 接口压测脚本：对比 `jt_db` C++ 扩展 vs pymysql 直连（任务 C6，已有 `docs/perf-benchmark.md` 初版结论）

模型评估结论统一写入 MySQL `model_version.metrics_json`（见 `db/sql/10_ai_train.sql`）。
