# 校捷通 · 完整开发文档

> 面向开发人员与 AI 辅助工具（Copilot）的项目全景文档。
> 覆盖：项目定位、技术栈、已完成工作量、待办路线、各层开发指南、规范与命令。
> 配套：`.github/copilot-instructions.md`（Copilot 自动加载的精简指令）、`docs/api.md`（接口契约）、`docs/architecture.md`（架构决策）。

---

## 1. 项目概述

**校捷通 —— 基于轻量化大模型与 RAG + AI Agent 的校园智能服务平台**
吉林大学大学生创新创业项目。一站式覆盖校园学习、生活、交易、社交、求职场景，AI 深度嵌入各模块。

- **形态**：微信小程序（前端）+ 云端服务 + 端侧 AI 探索
- **亮点**：校园垂直模型微调（LoRA/QLoRA）、RAG 高可信问答、AI Agent 任务自动执行、端侧 GGUF 量化、多模态交互、数据闭环自进化
- **团队**：4 人（前端 / 后端 / C++数据层 / 数据库+文档+数据）

## 2. 技术栈

| 层 | 技术 | 关键点 |
|---|---|---|
| 前端 | 微信小程序原生 (WXML/WXSS/JS) + Vant Weapp | 地图/定位/语音/相机 API |
| 后端 | Python 3.10+ / FastAPI / Uvicorn | 异步、pydantic、SSE 流式、JWT |
| 数据层 | **C++17 + MySQL C API + pybind11** | 连接池、预处理语句、事务、6 个 DAO |
| 关系库 | MySQL 8.0 (utf8mb4) | 42 张表，逻辑外键，软删除 |
| 缓存/任务 | Redis + Celery（规划） | 热门数据、Agent 异步执行 |
| 向量库 | ChromaDB（开发）/ FAISS（生产对标） | RAG 向量检索（框架已接入，待加载 bge-m3 全量建索引） |
| AI | Ollama(GGUF) + 微调模型；bge-m3 embedding | 对话、RAG、Agent、内容治理 |
| 部署 | Ubuntu + Docker + Nginx + HTTPS | 阿里/腾讯云学生机 |

## 3. 目录结构

```
xiaojietong-project/
├── .github/copilot-instructions.md   # Copilot 自动加载的开发指令
├── .vscode/                          # VS Code 开发配置（构建/调试/任务）
├── backend/                          # Python 后端（FastAPI）
│   ├── app/
│   │   ├── main.py                   # 入口：路由挂载、异常处理、连接池初始化
│   │   ├── core/                     # config / security(JWT) / deps / response
│   │   ├── db/cpp_bridge.py          # C++ 数据层桥接（唯一 DB 入口）
│   │   ├── models/                   # pydantic 请求/响应模型
│   │   ├── routers/                  # 11 个模块路由（auth/user/chat/agent/library/
│   │   │                             #   secondhand/job/forum/map_api/life/admin/upload）
│   │   └── services/                 # model_client(Ollama) / rag(占位) / 后续 Agent
│   └── tests/
├── db/
│   ├── cpp_driver/                   # ★ C++ 数据访问层
│   │   ├── include/jt_db/            # types / db_config / mysql_connection /
│   │   │                             #   connection_pool / transaction / db_session / dao/
│   │   ├── src/                      # 实现 + dao/ 各业务 DAO
│   │   ├── pybind/pybind_wrapper.cpp # jt_db 模块绑定
│   │   ├── test/                     # main.cpp / test_py.py / test_dao.py / test_all_dao.py
│   │   └── CMakeLists.txt            # 跨平台构建（Win .pyd / Linux .so）
│   └── sql/                          # 00_database + 01~10 分模块建表 + 99_init_data
├── miniprogram/                      # 微信小程序（前端规划）
├── ai/                               # finetune(微调) / rag / edge(端侧) / eval
├── deploy/                           # Docker/Nginx/HTTPS（规划）
└── docs/                             # architecture / api / datagrip / DEVELOPMENT
```

## 4. 已完成工作量（交付清单）

### 4.1 C++ 数据访问层（db/cpp_driver）— 核心
- **MySQL C API 封装**：连接管理、utf8mb4、健康检查
- **连接池**：mutex + condition_variable，RAII 获取/归还，min/max 可配，预热校验（避免"假成功"）
- **预处理语句**：`MYSQL_STMT` + 参数绑定，防注入；查询统一转 `list[dict]`
- **事务**：RAII 自动回滚；**DbSession 线程绑定**，事务内所有 SQL/DAO 走同一连接
- **6 个 DAO**（pybind 绑定为 `jt_db.XXXDAO()`）：
  - `UserDAO`：openid 查询 / 分页 / 创建 / 更新 / 删除 / 计数
  - `LibraryDAO`：空教室(NOT EXISTS 课表) / 座位 / 预约 / 取消 / 我的预约 / 拥挤度预测(均值占位)
  - `ForumDAO`：帖子分页 / 发帖 / 评论 / 点赞切换 / 待审核 / 热点
  - `SecondhandDAO`：物品分页搜索 / 发布 / 求购 / AI 供需匹配 / 状态 / 订单
  - `JobDAO`：岗位分页 / 投递 / 我的投递 / 可信度 / 黑名单
  - `LifeDAO`：通知 / 已读 / 商家 / 菜单 / 外卖下单
- **跨平台 CMake**：自动探测 MySQL/pybind11/生成器；Windows 出 `.pyd` + 自动拷 libmysql.dll；MSVC `/utf-8`
- **测试**：C++ 原生测试 + `test_all_dao.py`（15 组断言，含事务回滚验证）全绿

### 4.2 数据库（db/sql）
- **42 张表** 分 11 个 SQL 文件按模块落地并**已导入 MySQL(3307)**
- 模块：用户(3) / AI对话(4) / Agent(3) / 图书馆(6) / 二手(4) / 兼职(4) / 论坛(5) / 地图(2) / 生活(5) / AI数据(6) / 通用(1)
- 设计约定：主键 BIGINT 自增、逻辑外键(注释引用)、软删除、JSON 存可变结构、统一时间戳
- 种子数据：用户/指令/工具/建筑/座位/课表/商家/菜单/通知/知识库示例

### 4.3 后端（backend/）— 已实测
- **认证**：微信登录(code→openid→JWT)、refresh、JWT 依赖注入
- **11 模块路由** 全按 `docs/api.md` 实现，业务接口直接调 C++ DAO
- **统一响应** `{code,message,data}` + BizError 异常体系 + 全局处理器
- **SSE 流式对话**（chat/send）：sources→chunk→done 事件链，Ollama 未就绪时优雅降级
- **事务冲突校验**：座位预约(3001)、重复投递(3002)
- **实测通过**：登录/资料/空教室/商家/发帖/Agent任务/提醒/二手发布/预约/SSE

### 4.4 文档
- `docs/architecture.md`（架构+ADR）、`docs/api.md`（接口契约 v1.0，11 模块 ~60 接口）、`docs/datagrip.md`、`backend/README.md`、`db/cpp_driver/README.md`、`.github/copilot-instructions.md`

### 4.5 RAG 向量检索（AI 能力，框架已落地）
- **向量化**：`services/embedder.py` 封装 Ollama `/api/embed`（bge-m3），批量嵌入 + 内存缓存 + 熔断（失败 30s 内不再重试）
- **分块**：`services/chunker.py` 按中文句子聚合 500~800 字符，块间重叠 100
- **向量库**：`services/vector_store.py` ChromaDB 持久化；缺依赖自动降级纯 numpy 余弦（pickle 本地持久化）
- **索引**：`rag.build_index()/index_doc()` 分块 → embedding → 向量库 → 落 `knowledge_chunk`（去重哈希）→ 更新文档状态；`admin /knowledge/ingest` 入库即自动向量化
- **检索**：`rag.retrieve()` 向量优先 + 相似度阈值过滤，失败自动降级关键词分词匹配；命令行运维 `ai/rag/build_index.py` / `retrieve.py`
- **真实生效**：Ollama 加载 bge-m3 后执行 `cd backend && python ../ai/rag/build_index.py --force` 全量建索引

## 5. 待办与路线图（工作量预估）

### 阶段 A：AI 能力落地（用户主导：模型/微调/数据库）
| 任务 | 说明 | 涉及文件 |
|---|---|---|
| 模型微调流水线 | 数据采集→清洗→QLoRA→评估→GGUF | `ai/finetune/` |
| 模型接入 Ollama | 加载微调模型，替换降级占位 | `services/model_client.py` |
| RAG 向量化 | ✅ 框架已落地（bge-m3 + ChromaDB，缺依赖降级 numpy，模型未就绪降级关键词）；待加载 bge-m3 全量建索引 | `services/rag.py`、`ai/rag/` |
| Agent Function Call | 意图解析+工具调用，替换关键词规则 | `routers/agent.py`、`ai/` |
| 多模态 | 图像识别(二手/教学楼)、语音 | `services/` |
| 数据闭环 | 用户数据回流 train_corpus → 迭代微调 | `ai/finetune/`、`services/` |

### 阶段 B：前端（成员1）
| 任务 | 说明 |
|---|---|
| 小程序工程 | 按 `miniprogram/` 规划搭页 |
| 接口对接 | 按 `docs/api.md` 调后端 |
| 多模态交互 | 语音/相机/定位 |

### 阶段 C：生产化
| 任务 | 说明 |
|---|---|
| Docker 化 | 后端+C++扩展(.so)+MySQL+Redis+Ollama | `deploy/` |
| HTTPS + 域名 | 微信小程序强制要求 |
| 小程序提审发布 | |

## 6. 各层开发指南

### 6.1 后端新增一个业务接口
1. `routers/xxx.py` 加路由，请求体 `class XxxIn(BaseModel)`
2. 优先复用 DAO；无 DAO 用 `cpp_bridge.query/execute`
3. 返回 `ok(data)`；错误抛 `BizError`
4. 需要登录用 `Depends(get_current_user)`；管理用 `get_current_admin`
5. `main.py` 已统一挂载，无需改

### 6.2 C++ 新增一个 DAO
1. `include/jt_db/dao/xxx_dao.h` 声明方法（返回 `QueryResult` / `std::optional<Row>` / `long long` / `bool`）
2. `src/dao/xxx_dao.cpp` 实现，内部 `DbSession::current()->query/execute`
3. `CMakeLists.txt` 的 `jt_db_core` 加源文件
4. `pybind/pybind_wrapper.cpp` 绑定类
5. 重新构建 + `test_all_dao.py` 补断言

### 6.3 数据库变更
- 改 `db/sql/` 脚本；表结构变更建议用 Alembic（Python 迁移）而非直接改表
- 导入务必 `--default-character-set=utf8mb4`

### 6.4 RAG 知识库运维
1. 建索引：`cd backend && XJT_DB_PORT=3307 XJT_DB_PASSWORD=*** python ../ai/rag/build_index.py --force`（全量重建；`--doc 1,2` 增量指定）
2. 检索验证：`python ../ai/rag/retrieve.py "查询词" 3`
3. 入库即向量化：管理端 `POST /admin/knowledge/ingest`（embedding 不可用时返回 `embed_failed`，Ollama 就绪后重跑建索引）
4. 向量库位置：`backend/data/rag/`（ChromaDB 持久化，勿提交到 Git）

## 7. 编码规范（Copilot 已按此执行）

- 语言：注释/文档/消息简体中文；标识符英文
- Python：FastAPI + pydantic；SQL 一律 `?` 占位符；JSON 列 `json.dumps`
- C++：C++17、RAII、预处理语句；`DbSession::current()` 取连接
- 错误码：100x 参数 / 200x 认证 / 300x 业务冲突 / 500x 服务端
- 事务：`with cpp_bridge.begin():` 正常退出 commit，回滚须显式 `tx.rollback()` 或异常

## 8. 常用命令

```bash
# 构建 C++（改了 C++ 必须重编）
cmake --build db/cpp_driver/build --config Release --target jt_db

# 数据层测试
cd db/cpp_driver/test && XJT_DB_PORT=3307 XJT_DB_PASSWORD=jhq000000 python test_all_dao.py

# 启动后端
cd backend && XJT_DB_PORT=3307 XJT_DB_PASSWORD=jhq000000 python -m uvicorn app.main:app --reload --port 8000

# 建库（utf8mb4）
mysql -u root -P 3307 -p --default-character-set=utf8mb4 < db/sql/00_database.sql
# 然后逐模块 01~10 + 99_init_data

# VS Code 一键：Ctrl+Shift+B 任务面板
```

## 9. 关键环境

| 项 | 值 |
|---|---|
| MySQL | 127.0.0.1:3307 / root / jhq000000 / xiaojietong |
| Python | E:/miniconda3/python.exe (3.14) |
| C++ 构建 | VS2026(MSVC) + CMake，产物 `backend/app/db/native/jt_db.pyd` |
| 后端端口 | 8000（Swagger /docs） |
| Ollama | 127.0.0.1:11434（待加载模型） |

> ⚠️ 敏感信息（密码/密钥）勿写入代码，用环境变量 `XJT_*` 注入。

## 10. 里程碑回顾

| 里程碑 | 日期 | 状态 |
|---|---|---|
| 架构方案 + 技术选型 | 2026-08-24 | ✅ |
| C++ 数据层原型（连接池+pybind） | 2026-08-24 | ✅ |
| 42 张表设计 + 导入 | 2026-08-24 | ✅ |
| 6 个 DAO + 测试 | 2026-08-24 | ✅ |
| 接口契约 api.md | 2026-08-24 | ✅ |
| 后端 11 模块路由实测 | 2026-08-24 | ✅ |
| RAG 向量检索框架（bge-m3+ChromaDB，含降级） | 2026-08-24 | ✅ |
| VS Code/Copilot 开发环境 | 2026-08-24 | ✅ |
| AI 能力（模型/RAG/Agent） | — | ⏳ |
| 前端小程序 | — | ⏳ |
| 生产部署 | — | ⏳ |
