# PR：`dev` → `main`｜校捷通阶段性成果合并

> **本文件用途**：把 `dev` 上累积的全部更新整理成**一个总的 Pull Request**。
>
> **创建方式**（GitHub 网页，30 秒）：
> 1. 打开 👉 https://github.com/asuperj1/xiaojietong-project/compare/main...dev?expand=1
> 2. 标题填：`chore(release): dev → main 阶段性合并（C1~C13 / B1~B8 / UI / P0+P1 安全加固）`
> 3. 把下方「**PR 正文**」一节的内容**整体复制**到描述框
> 4. 点击 `Create pull request`（不要点 Merge —— 留给评审）
>
> **背景说明**：项目约定 `main` 为唯一受保护分支、只接受 PR；`dev` 放开直推
> （见 `docs/团队Git合作协议.md` 与提交 `265a58e`）。因此此前各批修复是**直接推 `dev`** 的，
> 本 PR 即用于把 `dev` 的阶段性成果一次性提交 `main` 评审。

---

## 一、基本信息

| 项 | 值 |
|---|---|
| **base** | `main`（`7913c3c`） |
| **head** | `dev`（`74a0a20`） |
| **提交数** | **62** |
| **改动规模** | **166 个文件，+13,552 / −324 行** |
| **合并方式** | 建议 `Create a merge commit`（保留各功能分支合并历史，便于回溯） |
| **关联文档** | `docs/项目审计报告260910序2-技术专项.md`（46 项风险）· `docs/技术方向待处理问题.md`（33 项待办）· `docs/api.md`（接口契约） |

### 按模块的改动分布

| 顶层目录 | 文件数 | 新增 | 删除 | 主要内容 |
|---|---:|---:|---:|---|
| `ui/` | 70 | 3,897 | 0 | 原型素材（43 图标 / 5 配图 / 18 页面原型）+ 跳转图 |
| `backend/` | 32 | 3,253 | 134 | 全部业务接口、AI 编排、审计修复、内容审核、可观测性 |
| `ai/` | 15 | 3,079 | 9 | 微调流水线、训练数据、知识库扩料、RAG 索引 |
| `db/` | 25 | 1,597 | 57 | SQL 治理表/索引、C++ 数据层修复与优化、DAO 扩展 |
| `docs/` | 14 | 1,345 | 122 | 审计报告、任务单、讲解手册、待办清单、API 契约 |
| `scripts/` | 6 | 350 | 0 | 跨平台自动同步工具 |
| `miniprogram/` | 1 | 21 | 0 | 前端工程三件套（F1） |
| 其他 | 3 | 10 | 2 | `.gitignore` / `.gitattributes` / CI 指令 |

---

## PR 正文

> 从下方开始整段复制 👇

---

## 概述

本次将 `dev` 上自上次合并以来的**全部阶段性成果**（62 个提交）一次性提交 `main`，涵盖：

1. **C++ 数据层加固与优化**（C7~C13，成员3）
2. **AI 能力落地**（C1~C5 微调流水线 + B3'/B4'/B5' 真实模型接入，成员2/3）
3. **后端全量接口与 AI 编排**（B1~B8，成员2）
4. **安全与性能加固**（审计 P0 全部 11 项 + P1 首批 8 项，成员3）
5. **UI 原型素材**（成员4/前端）
6. **协作工具与文档体系**（auto-sync 脚本、审计报告、任务单、待办清单）

这批改动使项目从"地基完成"推进到"**可安全演示、可量化评估、可持续迭代**"的状态。

---

## 一、C++ 数据层（`db/cpp_driver/`，C7~C13）

| 提交 | 内容 | 价值 |
|---|---|---|
| `017a053` | **C7** 新增内容安全治理表（`audit_word` 19 词 + `audit_log`）、通知投递表、**11 个复合索引**（存储过程实现幂等） | 为 B6 内容审核提供词库；解决慢查询 |
| `6ce07aa` | **C8** 修复 `LAST_INSERT_ID` 跨连接取值 bug | 原实现取到**错误的自增 ID**，会导致"发帖后跳转到别人的帖子" |
| `55c7277` | **C9/大字段** 修复 JSON/LONGTEXT >255 字节读取截断（`mysql_stmt_fetch_column`） | 原实现长文本**只能读到前 255 字节** |
| `482e5df` | **C9** MySQL IO 期间释放 GIL | 大结果集场景**反超 pymysql 2.84×** |
| `1fccafb` | **C10** 新增 `FavoriteDAO`，`favorite.py` 去除原生 SQL | DAO 收敛，消除注入面 |
| `55c7277` | **C11** 模型登记脚本 | 微调模型入库可追溯 |
| `77d8f74` / `701340f` | **C13** 据多轮实测修正点查退化区间（0.40~0.76×，抖动较大）并修正 README 过期结论 | 性能结论**可复现、不夸大** |

> 源码同时包含 P0 修复：`user_dao.cpp` 补齐 `status/is_deleted` 等列（**SEC-06**，禁用账号校验此前是死代码）。

---

## 二、AI 能力（`ai/`）

| 提交 | 内容 | 关键指标 |
|---|---|---|
| `0831820` | **C1/C2** 微调流水线骨架（`build_dataset.py` / `train.py` QLoRA / `eval.py`）+ 首份训练集 810 条 | 流水线可跑 |
| `031cd6d` | **C3** 知识库扩料 18 篇，训练集扩至 **1,780 条** | 语料覆盖图书馆/奖学金/校医院/校历 |
| `0869710` | **C4** 3B QLoRA 试跑完成 | **loss 4.30 → 0.11**，产出训练报告 |
| `3489690` | **C5** 合并 LoRA 权重 → **GGUF 6.2GB** → 部署 Ollama（`xjt-3b`），推理验证通过 | 一键部署脚本 + SHA256 校验 |
| `05a5a59` | **C12** 基座 vs 微调量化评测 | **关键信息命中率 0% → 77.8%** |
| `bd4c887` | **C6** `jt_db` vs 直连性能基准脚本与报告 | 答辩素材 |

> 配套：`ai/finetune/` 内含 `deploy_ollama.ps1` / `.sh`、`Modelfile`、`eval_compare.py`；模型分发指南见 `docs/模型分发与部署.md`。

---

## 三、后端与 AI 编排（`backend/`，B1~B8）

| 提交 | 内容 |
|---|---|
| `84de45a` | **B1~B5** 11 模块约 69 接口全量落地（JWT 登录、统一响应、SSE 流式、BizError 体系） |
| `d8b227b` | **B6** 内容审核闭环：接入 C7 词库 + 审核留痕（`audit_log`），帖子新增审核状态查询 |
| `95e94d4` | **B8** 可观测性：`/health` 返回连接池明细与一键自检 |
| `b66aa5b` | 补齐 `requirements.txt` 缺失的 **PyJWT**（原先全新环境无法启动） |

---

## 四、安全与性能加固（审计整改）

> 依据 `docs/项目审计报告260910序2-技术专项.md`（46 项风险：P0×11 / P1×18 / P2×17）

### P0 · 11/11 全部闭环（`caf17fa` → `d73031f`）

| 编号 | 问题 | 修复 |
|---|---|---|
| SEC-01 | JWT 默认弱密钥可伪造超管 token | 生产环境强制 ≥32B 密钥，否则**拒绝启动** |
| SEC-02 | 微信登录降级后门（任意 code 可登录） | 降级仅限非生产；生产未配凭据直接拒启 |
| SEC-03 | 聊天消息 IDOR（可读任意用户对话） | 增加会话归属校验 |
| SEC-04 | 可向他人会话写入（提示注入外泄） | 同上 |
| SEC-05 | 任意用户可下架他人商品 + 伪造价格 | 契约变更：卖家/金额**服务端反查** |
| SEC-06 | **禁用账号校验是死代码** | `user_dao` 补字段，禁用即时生效 |
| TXN-01 | 座位/房间可双订 | 冲突检查改 `SELECT ... FOR UPDATE` |
| TXN-02 | 二手订单可超卖 | 原子抢占 `UPDATE ... WHERE status=0` |
| CON-01 | 连接池 16 < 线程池 40 | 提升为可配置 4~32 |
| CON-02 | `Transaction.__del__` 跨线程解绑 | 按 owner 线程释放 |
| CAC-01 | RAG 降级 → 全站雪崩 | `to_thread` + 信号量 + TTL 缓存 |

### P1 · 8/18 已闭环（`c651edc` / `809bb83` / `4e9f02e` / `c905460`）

- **SEC-10** 接口限流（滑动窗口，登录 10 次/分）
- **SEC-11** 上传魔数校验 + 静态资源安全头（`nosniff` / CSP `sandbox`）
- **SEC-12** 补齐 PyJWT 依赖
- **SEC-13** CORS 修复（通配来源 + 携带凭据的危险组合）
- **SEC-14** 明文数据库口令从 11 个入库文件清除
- **CAC-02** RAG **single-flight**（10 并发同问 → 回源 1 次）+ 空值短 TTL
- **CAC-03** 浏览量内存聚合（写放大 20:1 → 1:1，消除热门帖行锁热点）
- **CAC-06** 向量库**读写锁**（读并发/写独占/写优先）+ 修正元数据竞态
- **CAC-07** 索引**覆盖式重建**（消除数分钟检索空窗，失败可重入）

> 剩余 26 项已逐条登记在 `docs/技术方向待处理问题.md`，含"为何单列 / 建议做法 / 验收方式"。

---

## 五、UI 与前端

- `8731a8d` 抠取并分类原型素材：**43 图标 / 5 配图 / 18 页面原型**
- `93fafd9` / `c2b64f2` 前端跳转图 1.0（页面跳转 + 功能包含设计）+ 规格书落地要求
- `miniprogram/` F1 工程三件套（`app.json` / `app.js` / `services/request.js`）

---

## 六、协作与文档

- `fcd7378` / `4cb9ecd` / `15c6ce8` 跨平台主仓库自动同步脚本（支持 github/gitee 双远程、环境变量覆盖）
- `265a58e` 明确 **`main` 唯一受保护、禁止直推；`dev` 放开直推**
- `54194a7` 首轮审计报告；`docs/项目审计报告260910序2-技术专项.md` 四维专项审计（46 项风险）
- `docs/技术方向待处理问题.md` **待办总清单**（33 项，含批次打包建议）
- `docs/团队讲解与任务分工.md` 讲解手册

---

## ⚠️ 七、破坏性变更（务必周知）

### 1. `POST /api/v1/secondhand/orders` 请求体变更

| | 旧 | 新 |
|---|---|---|
| 请求体 | `{ "item_id": 3, "seller_id": 1, "amount": 25 }` | `{ "item_id": 3, "remark": "周末自取" }` |
| 响应 | `{ "order_id": 8 }` | `{ "order_id": 8, "amount": 25.0 }` |

**原因**：原实现信任客户端传入的 `seller_id` / `amount`，可伪造卖家（嫁祸）与 0 元订单。
现由服务端从 `secondhand_item` 反查，并向客户端**忽略**这两个字段。

**影响面**：小程序端下单请求需同步修改（已同步 `docs/api.md` v1.4）。

### 2. 错误码新增

- `1010` 请求过于频繁（HTTP 429 + `Retry-After`）
- `2003` 账号已禁用
- `3001` 商品已售出/已下架/不能购买自己发布的物品

### 3. 部署前置要求

- **需本地重编译 C++ 扩展**：`SEC-06` 修改了 `user_dao.cpp`，`jt_db.pyd` 为 Python ABI 绑定且不入库。
  ```bash
  powershell -ExecutionPolicy Bypass -File db/cpp_driver/scripts/build.ps1
  ```
- **数据库口令改由环境变量注入**：`XJT_DB_PASSWORD`（文档与配置中已全部脱敏）

---

## 八、验证证据

### 自动化测试（均可复现）

| 脚本 | 结果 | 验证内容 |
|---|---|---|
| `backend/tests/verify_p0_authz.py` | **10/10** | **真实 HTTP 越权实测**：跨用户读/写会话被拒、伪造价格被忽略、二次下单被拒、禁用账号即时失效 |
| `backend/tests/verify_p1_ratelimit.py` | **5/5** | 13 连击登录第 11 次起 429 + `code=1010` + `Retry-After`；CORS 无 `allow-credentials` |
| `backend/tests/verify_p1_upload.py` | **5/5** | HTML 冒充 PNG 被拒、类型不符被拒、合法 PNG 通过、静态响应带 `nosniff`+`sandbox` |
| `backend/tests/verify_p1_viewcounter.py` | **4/4** | 连读 20 次后**立即查库仍为 0**（证明无逐次写），8s 后合并为 20 |
| `backend/tests/test_rwlock.py` | **5/5** | 3 读者可同时持锁；写者独占；持续读流量下写者等待 2ms（无饥饿） |
| `backend/tests/test_rag_singleflight.py` | **13/13** | 10 并发同问只回源 1 次；异常后 inflight 正确清理 |
| `backend/tests/test_index_rebuild.py` | **12/12** | 重建**进行中**检索仍命中旧数据；`clear()` 调用 0 次；孤儿向量被清理 |
| `db/cpp_driver/test/test_all_dao.py` | **15/15** | 含事务回滚 |
| `db/cpp_driver/test/test_favorite_dao.py` | **13/13** | C10 新增 DAO |
| `db/cpp_driver/test/test_last_insert_id.py` | **2/2** | C8 并发回归 |

### 人工验证

- 启动日志：`✅ jt_db 连接池已初始化`；`/health` 返回 `{"db":"ok","cpp_ext":true,"pool":{...}}`
- `XJT_ENV=prod` 且使用默认 JWT 密钥时**拒绝启动**（SEC-01 生效）

---

## 九、风险与回滚

| 风险 | 缓解 |
|---|---|
| 破坏性契约变更影响前端 | 已同步 `docs/api.md` 并在任务单交接说明中显著标注 |
| C++ 源码变更需重编译 | 已在文档中写明构建命令；`jt_db.pyd` 不入库是本项目既有设计 |
| 单次合入 62 个提交，评审量大 | 建议**按模块分批评审**（见下节），或先评审 C++/安全两个高风险面 |
| 回滚 | 本 PR 未改写历史，`main` 可用 `git revert -m 1 <merge_commit>` 整体回退；各功能分支（`feature/*`）仍保留 |

---

## 十、评审建议

考虑到提交量较大，建议**按依赖与风险分批看**：

| 优先级 | 评审范围 | 关注点 |
|---|---|---|
| **P0（必看）** | `db/cpp_driver/src/dao/user_dao.cpp`、`backend/app/routers/{chat,secondhand,library}.py`、`backend/app/core/config.py` | 安全修复是否真正闭环、契约变更是否可接受 |
| **P1** | `backend/app/services/{rag,vector_store,rwlock}.py`、`backend/app/core/{ratelimit,view_counter}.py` | 并发正确性（读写锁语义、single-flight、聚合落库） |
| **P2** | `ai/finetune/`、`ai/rag/`、`db/sql/` | 微调流程与数据表结构 |
| **P3** | `ui/`、`docs/`、`scripts/` | 素材与文档，可快速浏览 |

**需重点确认的开放问题**：
1. 二手订单契约变更对小程序端的影响是否已被成员1 接收？
2. `XJT_ENV=prod` 的硬校验是否会影响现有部署流程（需确保 CI/生产注入环境变量）？
3. 连接池上限由 16 提到 32，是否需要同步调整 MySQL `max_connections`（当前 151，充裕）？

---

> 复制到此为止 👆

---

## 附录：如何在没有 `gh` CLI 的情况下创建

本项目环境**未安装 `gh` CLI**，MCP 工具也未提供 PR 创建能力，因此采用「网页一键创建」：

```
https://github.com/asuperj1/xiaojietong-project/compare/main...dev?expand=1
```

打开后 GitHub 会自动比对 `main...dev`，把上面的 PR 正文粘贴进描述框即可。

> 若希望以后能命令行创建，可安装 `gh`：`winget install GitHub.cli`，然后 `gh auth login`。
