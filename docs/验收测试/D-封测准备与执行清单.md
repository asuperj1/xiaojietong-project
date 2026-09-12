# 第一阶段封测 · 准备与执行清单（D 分册）

> **配套**：`00-第一阶段验收测试方案.md`（判定/豁免）｜`A-使用向测试用例.md`（117 例）｜`B-技术性测试用例.md`（121 例）｜`C-测试记录表模板.md`
> **跟踪议题**：[Issue #43 【封测】第一阶段封测跟踪（v1.0.0-rc1）](https://github.com/asuperj1/xiaojietong-project/issues/43)
> **编制**：2026-09-12 ｜ 编制人：成员3
> **状态**：准备工作**已完成约 80%**（详见 §9），剩余为 2 项代码修复 + 1 次正式封版

---

## 1. 封测版本定义

| 项 | 内容 |
|---|---|
| 版本号 | **`v1.0.0-rc1`**（第一个封测候选版本） |
| 覆盖范围 | 基础功能 12 模块 + 一阶段创新模块「已到货项」 |
| 后端基线 | **`dev@bbb2ca9`**（PR #42 **已合入** `dev`） |
| 前端基线 | `feature/frontend`（已合入 `dev`，含 29 页） |
| **不含** | 二阶段科研内容（C22~C27）、部署上线（Docker/HTTPS/域名）、多模态、`ai/edge/` |
| 封测形式 | **封闭测试**：内部 3~5 人 + 邀请 2~3 位同学，按 A 分册（用户视角）实操 |

> **封测 ≠ 演示**：演示只跑通路径，封测要求**按用例逐条记录**（含失败与阻塞）。

---

## 2. 封测前置条件（Entry）

| # | 条件 | 状态 | 负责 |
|---|---|---|---|
| 1 | ~~PR #42 合并进 `dev`~~ → **已完成**（`dev@bbb2ca9`） | ✅ 已合入 | — |
| 2 | ⚠️ **`auth.py` 头像补 `resign`**（第三轮新发现，1 行） | ❌ 待修 | 成员2 |
| 3 | ⚠️ **二手读路径加 `audit_status` 过滤**（`DATA-01`，实测已确证泄漏） | ❌ 待修 | 成员3 / 成员2 |
| 4 | 环境三件套在线（MySQL / 后端 / Ollama） | ✅ 已验证 | 成员3 |
| 5 | 种子数据齐备 | ✅ **已完成**（§4） | 成员3 |
| 6 | 开机自检通过（阻塞项 = 0） | ⚠️ 当前 1 项（Ollama 502，合并后自动消除） | 成员3 |
| 7 | 测试基线冻结（记录 commit） | ⏳ 合并后记录 | 成员3 |

> **第 2、3 项是"封测前必修"**：第 2 项导致**登录首屏头像 403**；第 3 项导致**待审违规内容对所有人可见**（答辩现场最易被戳破）。

---

## 3. 环境搭建（5 步）

```powershell
# ── 步骤 1：数据库（MySQL 8.0 @ 3307）──────────────────────────
# 确认 45 张表
& "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" --host=127.0.0.1 --port=3307 `
  -uroot -p -N -e "use xiaojietong; SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='xiaojietong';"

# ── 步骤 2：种子数据（★ 见 §4，三条命令）────────────────────────

# ── 步骤 3：启动 Ollama（保持托盘运行；实测直连 200，走代理会 502）
ollama serve        # 或用桌面版；确认 ollama list 有 qwen2.5:3b / xjt-3b / bge-m3

# ── 步骤 4：启动后端（★ 注意 --host 0.0.0.0，真机封测必需）────────
cd backend
$env:XJT_DB_PASSWORD='<your-local-password>'; $env:XJT_DB_PORT='3307'
Remove-Item Env:XJT_ENV -ErrorAction SilentlyContinue      # 清残留，否则 prod 硬校验会拒绝启动
E:\miniconda3\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# ── 步骤 5：前端（xjt-frontend）
# ① services/request.js：BASE_URL 改为 http://<本机局域网IP>:8000/api/v1（见方案 §3.2）
# ② 微信开发者工具 → 详情 → 本地设置 → ☑ 不校验合法域名...
# ③ AppID 用 wx6ccc5c4c02b31455
```

---

## 4. 数据准备（三条命令，均已实测）

| # | 数据 | 命令 | 实测结果 |
|---|---|---|---|
| 1 | **POI 种子**（8 个校园点位，来自 PR #42 的 `99e`） | `mysql ... xiaojietong < db/sql/99e_poi_seed.sql` | ✅ POI **0 → 8** |
| 2 | **静态字典种子**（公司 3 / 岗位 6 / 通知 8） | `mysql ... xiaojietong < db/sql/99f_beta_seed.sql` | ✅ 全部写入 |
| 3 | **动态业务种子**（二手 5 / 帖 5 / 求购 1 / 收藏 2 / 标签） | `python -X utf8 tools/seed_testdata.py` | ✅ **20/20 成功** |

```powershell
# 完整执行（在项目根目录）
$m = 'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe'
cmd /c "`"$m`" --host=127.0.0.1 --port=3307 -uroot -p --default-character-set=utf8mb4 xiaojietong < db\sql\99f_beta_seed.sql"
python -X utf8 tools/seed_testdata.py
```

**种子账号**（`seed_testdata.py` 自动创建，可直接在 A 分册中当「甲 / 乙 / 丙」用）：

| 账号 | 登录 code（mock） | 用途 |
|---|---|---|
| 甲 | `seed-user-alpha` | 主操作者 |
| 乙 | `seed-user-beta` | 交叉可见性验证 |
| 丙 | `seed-user-gamma` | 越权验证 |

> ⚠️ **`job_post.status = 0 才是上架**（1 = 下架），与直觉相反，已在 `99f_beta_seed.sql` 中注明并统一置 0。
> ⚠️ `seed_testdata.py` **可重复执行**（会追加数据）；要干净环境请用独立测试库。

---

## 5. 开机自检（每次封测前必跑）

```powershell
python -X utf8 tools/preflight_check.py
```

**9 组检查**：E1 后端 / E2 组件与版本 / E3 模型清单 / E4 登录 / **E5 种子数据** / **E6 签名闭环** / E7 前端配置 / E8 十二模块冒烟 / E9 已知缺陷复核

**判据**：`阻塞项 = 0` 才可开测。

**当前实测**（`dev@7a3260c`，即 **未合并 PR #42** 的状态）：

| 轮次 | 结果 |
|---|---|
| 首次 | ✅ 23 ｜ ❌ 5 ｜ 阻塞 **5** |
| 补种子数据后 | ✅ **27** ｜ ❌ **1** ｜ 阻塞 **1** |

唯一剩余项：`E2.3 Ollama 可达 = false` —— **原因已定位**：系统代理把本地 11434 拦成 502（直连 200 正常）。该问题由 **`fix/sec23-ollama-proxy-isolation`（PR #48）的 `trust_env=False`** 修复 → **合入 `dev` 后此项自动通过**。

---

## 6. 封测执行清单

### D-1（准备日，半天）
- ☐ 合并 PR #42 → 记录 `dev` commit 到 C 分册
- ☐ 补 `auth.py` 头像 `resign`（1 行）
- ☐ 二手读路径加 `audit_status` 过滤
- ☐ 重跑 `pytest tests -q` + `tools/e2e_connectivity.py`
- ☐ 执行 §4 三条种子命令
- ☐ 跑 `preflight_check.py`，**阻塞项归零**
- ☐ 通知执行人（A 分册交非开发同学）

### D1（使用向封测）
- ☐ 按 **A 分册** 逐条执行（117 例，5 大用户旅程）
- ☐ 每失败项留证据（截图/录屏）+ 一句话现象
- ☐ 填 C 分册「A 分册执行记录」+「体验建议区」
- ☐ **必测**：A-5-14 / A-7-5（待审可见性）、A-5-1（图片）、A-2-1（AI 准确性）

### D2（技术性封测）
- ☐ 按 **B 分册** 执行（121 例，B-1 ~ B-11）
- ☐ 记录 13 项关键指标数值（RAG hit@3、并发超卖、p95…）
- ☐ 对照 B 分册「已知失败速查表」区分**已知 / 新发现**
- ☐ 填 C 分册「B 分册执行记录」+「缺陷台账」

### D3（回归与结论）
- ☐ 修复 → 由**非修复者**回归
- ☐ 填 C 分册「回归记录」「验收结论」
- ☐ 产出 `第一阶段验收测试报告.md`

---

## 7. 反馈与缺陷管理

| 渠道 | 用途 | 责任人 |
|---|---|---|
| C 分册「缺陷台账」 | 所有缺陷统一登记（P0~P3） | 测试负责人 |
| C 分册「体验建议区」 | 非缺陷的体验问题 | A 分册执行人 |
| `docs/技术方向待处理问题.md` | **单批次**问题登记（项目约定） | 成员3 |

**规则**：缺陷必须落文档（不得只留聊天记录）；修复走分支（不得直改 `dev`）；回归由非修复者执行。

---

## 8. 封测准出（Exit）

见主方案 §5。硬性门槛摘要：

| 门槛 | 阈值 |
|---|---|
| P0 缺陷 | **0 未关闭** |
| A 分册通过率 | ≥ 95% |
| B-1 契约 / B-2 鉴权 / B-4 并发 | **100%** |
| RAG hit@3 | ≥ 80%（当前实测 **100%**） |
| `pytest` | 全绿（当前 **7 passed**） |

---

## 9. 本次准备执行记录（2026-09-12）

### 9.1 已完成 ✅

| # | 动作 | 结果 |
|---|---|---|
| 1 | 编写封测开机自检脚本 `tools/preflight_check.py`（9 组检查） | ✅ 可用，两轮迭代修正（health 不走统一响应体、收藏路径） |
| 2 | 编写业务种子脚本 `tools/seed_testdata.py`（走接口，多账号） | ✅ **20/20 成功** |
| 3 | 新增静态种子 SQL `db/sql/99f_beta_seed.sql`（公司 3 / 岗位 6 / 通知 8） | ✅ 已执行 |
| 4 | 导入 POI 种子（PR #42 的 `99e_poi_seed.sql`） | ✅ POI 0 → 8 |
| 5 | 重跑自检 | ✅ 阻塞项 **5 → 1** |

### 9.2 准备过程中发现的问题 ⚠️

| # | 问题 | 证据 | 处置 |
|---|---|---|---|
| 1 | **二手待审内容泄漏**（`DATA-01` 确证） | 甲发布含敏感词商品 → `audit_status=0` → **乙列表立即可见**（5→6 件）；对照论坛则**他人看不到**待审帖 | **封测前必修** |
| 2 | **`auth.py` 头像未重签名** | `auth.py:43` 仍是裸路径（`user.py` 已修） | 合并前补 1 行 |
| 3 | **Ollama 被系统代理拦成 502** | 直连 `11434` → 200；走代理 → **502** | 已由 **`fix/sec23`（PR #48）** 解决，合入 `dev` 后自动通过 |
| 4 | **`job_post.status` 语义反直觉** | 写 `1` 时 `GET /jobs` 返回空；写 `0` 才可见 | 已在 `99f_beta_seed.sql` 注明并修正 |
| 5 | `preflight_check.py` 初版误判 | `/health` 不走 `{code,data}`；收藏真实路径是 `/favorites` 而非 `/favorites/me` | 已修正脚本 |

### 9.3 封测准备度

| 维度 | 完成度 |
|---|---|
| 测试方案（主方案 + A/B/C 分册） | **100%** |
| 自检工具 | **100%** |
| 种子数据 | **100%** |
| 环境就绪 | **~80%**（差 `fix/sec23` 合入 + 代理/Ollama 联通） |
| **待修代码** | **2 项**（`auth.py` 1 行；二手审核过滤） |
| **综合** | **可进入 D-1，前提是先完成 2 项修复** |

---

## 10. 命令速查

```powershell
cd <repo-root>   # 项目根

# 自检（每次封测前）
python -X utf8 tools/preflight_check.py

# 种子数据
python -X utf8 tools/seed_testdata.py
$m = 'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe'
cmd /c "`"$m`" --host=127.0.0.1 --port=3307 -uroot -p --default-character-set=utf8mb4 xiaojietong < db\sql\99f_beta_seed.sql"

# 冒烟三件套
cd backend; python -m pytest tests -q
cd ..; python -X utf8 tools/e2e_connectivity.py

# 审计复核（对照已知缺陷）
python -X utf8 tools/audit_recheck_260912.py

# AI 检索质量（须在 backend/ 目录跑，因向量库路径是相对路径）
cd backend; python -X utf8 ..\ai\eval\rag_bench.py
```
