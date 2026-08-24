# 校捷通 系统架构方案

> 项目：校捷通 —— 基于轻量化大模型与 RAG + AI Agent 的校园智能服务平台
> 版本：v1.0
> 日期：2026-08-24
> 状态：待评审

---

## 0. 文档说明

| 项 | 内容 |
|---|---|
| 文档目标 | 明确系统整体架构、各层技术选型与接口边界，作为 4 人团队 16 周开发的统一技术基线 |
| 读者 | 全体开发成员（前端 / Python 后端 / C++ 底层 / 数据库与数据） |
| 关键前置决策 | ① C++ 数据访问层通过 **pybind11** 编译为 Python 扩展模块供 FastAPI 调用；② AI 端侧策略为 **服务端本地推理为主**，端侧量化作为答辩创新点演示 |
| 名词约定 | AI 助手=多轮问答入口；Agent=任务自动执行；RAG=检索增强生成；DAO=数据访问对象 |

---

## 1. 需求与范围回顾

### 1.1 八大核心功能模块

| # | 模块 | 核心能力 | 关键 AI 能力 |
|---|---|---|---|
| M1 | 校园 AI 智能助手 | 多轮对话、语音交互、快捷指令 | RAG 精准问答、上下文记忆 |
| M2 | AI Agent 任务执行 | 自然语言指令→自动预约/提醒/发布 | Function Call 工具调用、任务编排 |
| M3 | 图书馆智能预约 | 座位预约、空教室查询 | 人流预测、最优座位推荐 |
| M4 | 二手循环经济 | 闲置发布/求购/交易 | 拍照识别品类、自动定价、供需匹配 |
| M5 | 兼职实习诚信 | 岗位展示/投递 | 虚假岗位识别、可信度评分、岗位匹配 |
| M6 | 校园论坛 | 发帖/评论/点赞/话题 | 内容审核、摘要、热点识别、推荐 |
| M7 | 校园地图可视化 | 电子地图、建筑详情、导航 | 位置联动服务、路线规划 |
| M8 | 生活服务 | 外卖聚合、通知公告、校园服务 | 个性化推荐、通知精准推送 |

### 1.2 六大创新点（架构支撑）

1. 校园垂直小模型微调（LoRA/QLoRA）→ 由 AI 微调流水线支撑
2. RAG 高可信问答 → 由知识库 + 向量检索模块支撑
3. AI Agent 智能交互 → 由 Function Call 工具服务支撑
4. 端侧轻量部署（GGUF 量化）→ 由模型量化 + 服务端推理支撑
5. 多模态融合交互 → 由语音 / 图像 / 定位服务支撑
6. 数据闭环自进化 → 由训练数据回流管道支撑

---

## 2. 总体架构设计

### 2.1 分层架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│  表现层  微信小程序（原生 WXML/WXSS/JS + Vant Weapp）                    │
│  语音输入/播报 · 相机识别 · LBS 定位 · 地图 SDK                           │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ HTTPS / WebSocket（对话流式）
┌───────────────────────────────▼─────────────────────────────────────┐
│  接入层  Nginx 反向代理 + HTTPS · 微信小程序登录鉴权 · 限流               │
└───────────────────────────────┬─────────────────────────────────────┘
┌───────────────────────────────▼─────────────────────────────────────┐
│  业务逻辑层  FastAPI（Uvicorn，异步）                                   │
│  auth 用户   chat 对话   agent 任务   library 预约   secondhand 二手    │
│  job 兼职    forum 论坛   map 地图    life 生活   admin 管理            │
└─────────┬──────────────────────┬─────────────────────┬──────────────┘
          │ REST / SQL 调用        │ 内部 RPC / SDK        │ HTTP 推理接口
┌─────────▼──────────────┐  ┌─────▼──────────────────────▼─────────────┐
│  数据层                │  │  AI 核心层                                │
│  ┌──────────────────┐  │  │  ┌─────────────────┐  ┌───────────────┐ │
│  │ C++ 数据访问模块 │  │  │  │ RAG 检索服务     │  │ 推理服务       │ │
│  │ (pybind11扩展)   │  │  │  │ 知识库→分块→embed│  │ Ollama/llama   │ │
│  │ 连接池·DAO·事务  │  │  │  │ →FAISS/Chroma    │  │ .cpp GGUF      │ │
│  └────────┬─────────┘  │  │  └─────────────────┘  └───────────────┘ │
│  MySQL 8.0 + Redis     │  │  AI Agent 工具编排（Function Call）        │
│  FAISS/Chroma 向量库   │  │  多模态服务（语音/图像识别/定位）           │
└────────────────────────┘  └─────────────────────────────────────────┘
                              ▲
                        ┌─────┴──────────────────────────────┐
                        │ 模型微调流水线（离线，不在请求链路上）│
                        │ 数据采集→清洗→标注→QLoRA→评估→GGUF量化│
                        └────────────────────────────────────┘
```

### 2.2 各层职责边界

| 层 | 职责 | 不允许 |
|---|---|---|
| 表现层（小程序） | 交互展示、本地状态、流式渲染、上传 | 直连数据库、持有密钥 |
| 接入层（Nginx） | 反向代理、TLS、静态资源、限流 | 业务逻辑 |
| 业务层（FastAPI） | 业务编排、鉴权、与 AI/C++ 层协作 | 绕过 C++ 层直连 MySQL（性能敏感路径除外） |
| C++ 数据访问层 | 数据库连接池、高性能 CRUD、事务、批量操作 | 业务规则、HTTP |
| AI 核心层 | RAG、推理、Agent 工具、多模态 | 用户数据的持久化 |
| 微调流水线（离线） | 数据回流、训练、评估、模型发布 | 在线请求处理 |

### 2.3 技术选型总表

| 域 | 选型 | 备选 | 决策理由 |
|---|---|---|---|
| 小程序 | 原生框架 + Vant Weapp | Taro/uniapp | 文档既定，原生可控、免编译链 |
| 后端框架 | FastAPI + Uvicorn | Flask | 异步性能好、原生 OpenAPI 文档、pydantic 校验 |
| 数据库驱动（Python侧） | C++ pybind11 扩展模块 | SQLAlchemy 直连 | 突出 C++ 底层能力，性能与团队分工要求 |
| C++ 驱动 | MySQL C API（libmysqlclient） | MySQL Connector C++ | 轻量、跨平台（Win/Linux）、符合文档"MySQL C API"表述、pybind11 绑定简单 |
| 关系库 | MySQL 8.0 | — | 文档既定 |
| 缓存 | Redis | 内存缓存 | 会话、验证码、热门数据、Agent 任务状态 |
| 向量库 | ChromaDB（开发持久化） | FAISS（高并发生产） | Chroma 自带持久化与元数据过滤，适合校园数据量；FAISS 作为性能对标点 |
| 基座模型 | Qwen2.5-7B-Instruct | MiniCPM3-4B | 中文强、生态成熟；4B 备选降低显存门槛 |
| 微调 | PEFT(LoRA/QLoRA) + BitsAndBytes | 全参 SFT | 普通笔记本/单卡可训，成本低 |
| Embedding | bge-m3 | text2vec-base-chinese | 中文检索效果好、支持长文本 |
| 推理服务 | Ollama（GGUF） | llama.cpp server | 部署简单、HTTP 接口友好 |
| 端侧 | llama.cpp / GGUF 桌面 demo | 小程序真端侧 | 受小程序包体积限制，务实为主 |
| 部署 | Docker + Nginx + HTTPS | 裸机部署 | 一致性、可复现 |
| 云主机 | 阿里云/腾讯云轻量 2C4G+ | — | 学生优惠，够用 |

### 2.4 关键架构决策记录（ADR）

| # | 决策 | 说明 |
|---|---|---|
| ADR-1 | C++ 层用 **pybind11** 集成进 Python | 编译为 `jt_db` Python 扩展，Python 直接 import。类型安全、性能最好、调用链最短，最能体现底层能力 |
| ADR-2 | C++ 层数据库驱动用 **MySQL C API** | 已有 `cpp_driver` 用 Connector C++（仅 Windows/VS 测试），生产部署到 Linux 时 Connector C++ 依赖较重；改用 libmysqlclient 跨平台一致，且与大创文档一致 |
| ADR-3 | 端侧 AI **服务端本地推理为主** | 小程序总包上限约 20MB，300MB GGUF 无法真正入包；服务端 Ollama 推理保证体验，端侧量化用桌面 demo 展示创新点 |
| ADR-4 | 在线请求链路**不让大模型串行阻塞** | 对话/Agent 任务用异步任务队列（Celery + Redis）解耦，长耗时任务后台执行、状态查询式返回 |
| ADR-5 | 外部系统（图书馆/教务）**模拟数据 + 预留接口** | 学校系统无开放 API，先实现本地模拟数据与清晰的数据访问接口，答辩说明对接方案 |
| ADR-6 | 数据闭环采用**定时回流**而非实时 | 论坛/对话数据每日批处理进训练数据仓库，避免在线链路污染与隐私风险 |

---

## 3. 前端架构（微信小程序）

### 3.1 目录结构

```
miniprogram/
├── app.js / app.json / app.wxss        # 全局配置、路由、TabBar
├── pages/
│   ├── chat/            # AI 助手对话页（流式）
│   ├── agent/           # Agent 任务中心（任务列表、状态）
│   ├── library/         # 图书馆预约 / 空教室
│   ├── secondhand/      # 二手发布 / 浏览 / 求购
│   ├── job/             # 兼职实习
│   ├── forum/           # 论坛
│   ├── map/             # 校园地图
│   ├── life/            # 生活服务（外卖、通知）
│   └── user/            # 我的、设置
├── components/          # 复用组件（对话气泡、商品卡片…）
├── services/            # API 封装层（request.js，统一 token/错误处理）
├── utils/               # 工具（存储、鉴权、格式化）
└── static/              # 图片、图标
```

### 3.2 关键设计

- **统一请求封装** `services/request.js`：携带 `token`，统一错误码处理，SSE 流式对话解析。
- **多模态接入**：微信语音 `RecorderManager` + `wx.createWebAudioContext`；图像用 `wx.chooseMedia` + 上传识别；定位 `wx.getLocation`。
- **流式对话**：后端返回 SSE（`text/event-stream`），前端逐块渲染，支持停止生成。
- **状态管理**：`app.globalData` + `wx.setStorage` 持久化；Agent 任务状态通过轮询/长连接获取。

---

## 4. 后端架构（FastAPI）

### 4.1 工程结构

```
backend/
├── app/
│   ├── main.py                 # FastAPI 入口、路由注册、中间件
│   ├── core/                   # 配置、日志、安全、依赖注入
│   │   ├── config.py           # 环境配置（pydantic-settings）
│   │   ├── security.py         # JWT、密码哈希、权限
│   │   └── deps.py             # 依赖：get_db、get_current_user
│   ├── db/                     # 数据库访问（走 C++ 扩展 + SQLAlchemy 两用）
│   │   ├── cpp_bridge.py       # pybind11 扩展封装（连接池入口、DAO 门面）
│   │   └── redis.py            # Redis 客户端
│   ├── models/                 # pydantic 请求/响应模型
│   ├── schemas/                # 数据模型（与数据库对应）
│   ├── routers/                # 业务路由（按模块）
│   │   ├── auth.py  user.py  chat.py  agent.py
│   │   ├── library.py  secondhand.py  job.py
│   │   ├── forum.py  map_api.py  life.py  admin.py
│   │   └── ws.py               # 对话流式端点
│   ├── services/               # 业务服务层
│   │   ├── rag.py              # RAG 编排（检索+生成）
│   │   ├── agent_engine.py     # Agent 任务编排、工具注册表
│   │   ├── model_client.py     # 推理服务客户端（Ollama HTTP）
│   │   ├── multimodal.py       # 图像识别 / 语音 / 定位
│   │   └── notify.py           # 消息推送（订阅消息）
│   ├── tasks/                  # 异步任务（Celery）
│   │   ├── agent_tasks.py
│   │   └── data_reflow.py      # 数据回流（训练数据）
│   └── utils/
├── tests/
├── alembic/                    # 数据库迁移（配合 C++ 层不冲突，见 6.4）
├── Dockerfile
└── requirements.txt
```

### 4.2 与 C++ 数据访问层的协作方式

- Python 侧通过 `app/db/cpp_bridge.py` 封装 pybind11 扩展 `jt_db`，对业务层暴露**数据访问门面**（尽量不直接散落 SQL 调用）。
- 读多写少的高频路径（列表页、搜索）走 C++ 扩展；复杂分析型查询（报表、后台统计）可直接 SQLAlchemy。**默认所有业务 CRUD 走 C++ 扩展**。
- 事务：通过 C++ 层 `transaction()` 上下文管理器显式开启/提交/回滚。

### 4.3 与 AI 层的协作方式

- 对话：`routers/chat.py` → `services/rag.py` → （检索）→ `services/model_client.py` → Ollama 流式接口 → SSE 回传。
- Agent 任务：`routers/agent.py` → `services/agent_engine.py` → 解析意图 → 查工具注册表 → 调用 `routers` 对应业务服务 → 回写任务状态 → Celery 异步执行。

### 4.4 API 规范

- RESTful，资源复数命名，`/api/v1/` 前缀；鉴权用 JWT Bearer。
- 统一响应体：`{code, message, data}`，code=0 成功，业务错误码分段（见 8.4）。
- 错误处理：全局异常中间件，日志记录 request-id 便于追踪。
- 限流：Nginx 层按 IP，业务层对写接口加 Redis 计数器。

---

## 5. C++ 数据访问层设计（重点 · 成员3 职责）

> 定位：整个系统唯一直接操作 MySQL 的结构化数据通道，向 Python 提供高性能、线程安全的数据库访问能力。

### 5.1 分层设计

```
┌─────────────────────────────────────────────┐
│  Python 业务层（FastAPI）                      │
└───────────────▲─────────────────────────────┘
                │ import jt_db
┌───────────────┴─────────────────────────────┐
│  pybind11 绑定层（导出 C++ API 为 Python 模块）│
│  pybind_wrapper.cpp                          │
├─────────────────────────────────────────────┤
│  DAO 层（按业务聚合查询方法）                   │
│  UserDAO / LibraryDAO / ForumDAO / …         │
├─────────────────────────────────────────────┤
│  连接池层（核心）                              │
│  ConnectionPool · RAII 连接获取/归还           │
├─────────────────────────────────────────────┤
│  驱动封装层                                   │
│  MySQL C API 封装（mysql_init/real_connect/…）│
│  结果集 → 通用 Row 类型                        │
└─────────────────────────────────────────────┘
```

### 5.2 目录结构

```
db/cpp_driver/
├── CMakeLists.txt              # 跨平台构建（Win dev / Linux prod）
├── include/jt_db/
│   ├── db_config.h             # 连接配置结构
│   ├── mysql_wrapper.h         # MySQL C API 封装
│   ├── connection_pool.h       # 连接池
│   ├── result.h                # 结果集（行/列）表示
│   ├── transaction.h           # 事务 RAII
│   ├── dao/
│   │   ├── user_dao.h
│   │   ├── library_dao.h
│   │   ├── forum_dao.h
│   │   ├── secondhand_dao.h
│   │   ├── job_dao.h
│   │   └── life_dao.h
│   └── jt_db_api.h             # 统一 API 入口
├── src/
│   ├── mysql_wrapper.cpp
│   ├── connection_pool.cpp
│   ├── result.cpp
│   ├── dao/*.cpp
│   └── pybind/
│       └── pybind_wrapper.cpp  # 唯一 pybind11 绑定文件
├── test/
│   ├── main.cpp                # C++ 原生测试（现有）
│   └── test_py.py              # Python 侧集成测试
└── scripts/
    ├── build_win.bat           # Windows 开发构建
    └── build_linux.sh          # Linux 生产构建
```

### 5.3 连接池设计（核心）

- **池大小**：`min=4, max=32`（可配置），按服务器规格调整。
- **实现**：`std::mutex` + `std::condition_variable`，空闲队列 + 活跃计数。
- **获取**：有空闲则弹出；否则若未达上限则新建；达到上限则阻塞等待（可设超时，如 5s 抛异常）。
- **归还**：连接检查 `mysql_ping`，失效则丢弃重建，避免脏连接。
- **RAII**：提供 `ConnectionGuard`，作用域结束自动归还（异常安全）。
- **线程安全**：每个业务请求一个连接，天然避免 SQL 句柄并发竞争；连接不跨线程使用。

```cpp
// include/jt_db/connection_pool.h 核心接口
namespace jt_db {
class ConnectionPool {
public:
    static ConnectionPool& instance();           // 单例
    void init(const DbConfig& cfg);              // 启动时初始化
    ConnectionGuard get();                       // RAII 获取连接
    size_t idle_count() const; size_t active_count() const; // 监控
private:
    std::vector<std::unique_ptr<MysqlConnection>> idle_;
    std::mutex mu_; std::condition_variable cv_;
    DbConfig cfg_; size_t active_ = 0;
};
}
```

### 5.4 DAO 层设计

- 每个 DAO 面向一个业务域，方法返回 `QueryResult`（行数组）或 `int64_t`（影响行数/自增ID）。
- **禁止拼 SQL 字符串**：统一使用 `MYSQL_STMT`（预处理语句）防注入、支持二进制与流式读写。
- 示例接口：

```cpp
// user_dao.h
class UserDAO {
public:
    // 按手机号/学号查询用户（预处理语句）
    std::optional<Row> find_by_account(const std::string& account);
    // 分页查询帖子列表
    QueryResult page_posts(int page, int size, const std::string& category);
    // 批量写入训练数据（批量预处理，性能关键）
    int64_t batch_insert_train_records(const std::vector<TrainRecord>& rows);
};
```

### 5.5 pybind11 绑定示例

```cpp
// src/pybind/pybind_wrapper.cpp
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <jt_db/jt_db_api.h>

namespace py = pybind11;
using namespace jt_db;

PYBIND11_MODULE(jt_db, m) {
    m.doc() = "校捷通 C++ 高性能数据访问模块";
    m.def("init_pool", &ConnectionPool::init, "初始化连接池", py::arg("host"),
          py::arg("port"), py::arg("user"), py::arg("password"),
          py::arg("dbname"), py::arg("min_conn") = 4, py::arg("max_conn") = 32);

    m.def("query", &execute_query,
          "执行查询，返回 [{'col': value}...]", py::arg("sql"), py::arg("params") = py::list());

    m.def("execute", &execute_update,
          "执行写操作，返回影响行数", py::arg("sql"), py::arg("params") = py::list());

    m.def("ping", &ping_pool, "健康检查");

    py::class_<Transaction>(m, "Transaction")
        .def("commit", &Transaction::commit)
        .def("rollback", &Transaction::rollback);

    m.def("begin", &begin_transaction, "开启事务，返回 Transaction");
    m.def("pool_stats", &pool_stats, "返回 {idle, active} 统计");
}
```

Python 侧使用：

```python
# backend/app/db/cpp_bridge.py
import jt_db

_pool_ready = False

def init_db(host, port, user, password, db):
    jt_db.init_pool(host, port, user, password, db)
    global _pool_ready; _pool_ready = True

def query(sql, params=None):
    if not _pool_ready: raise RuntimeError("DB 未初始化")
    return jt_db.query(sql, params or [])

def execute(sql, params=None):
    return jt_db.execute(sql, params or [])

# 事务使用示例
# with jt_db.begin() as tx: ...  jt_db 自动 commit/rollback
```

### 5.6 事务支持

- `Transaction` 对象封装 `BEGIN / COMMIT / ROLLBACK`，析构时若未提交则自动回滚（RAII，异常安全）。
- Python 侧 `with jt_db.begin() as tx:` 块内全部 DAO 操作共享同一连接（通过 thread-local 绑定当前事务连接）。
- 连接池在事务期间**不归还连接**，事务结束才归还。

### 5.7 CMake 跨平台构建

```cmake
cmake_minimum_required(VERSION 3.20)
project(jt_db CXX)
set(CMAKE_CXX_STANDARD 17)
find_package(pybind11 REQUIRED)         # pybind11 需安装

if(WIN32)
    set(MYSQL_INCLUDE_DIR "C:/Program Files/MySQL/MySQL Server 8.0/include")
    set(MYSQL_LIB_DIR "C:/Program Files/MySQL/MySQL Server 8.0/lib")
    add_library(jt_db MODULE src/... pybind/pybind_wrapper.cpp)
    target_link_libraries(jt_db PRIVATE libmysql.lib)
    set_target_properties(jt_db PROPERTIES PREFIX "" SUFFIX ".pyd")
else()
    find_package(MySQL REQUIRED)        # 需安装 libmysqlclient-dev
    add_library(jt_db MODULE src/... pybind/pybind_wrapper.cpp)
    target_link_libraries(jt_db PRIVATE MySQL::client)
    set_target_properties(jt_db PROPERTIES PREFIX "")
endif()
```

- 开发环境（成员3 本机）：Windows + VS2022 + CMake，产出 `jt_db.pyd` 供 Python 调用。
- 生产环境（Ubuntu）：`apt install libmysqlclient-dev python3-dev` + pip pybind11，产出 `jt_db.cpython-*.so`。
- 构建产物统一放置 `backend/app/db/native/`，由 `cpp_bridge.py` 动态加载。

### 5.8 性能与监控

- 慢查询日志：C++ 层记录超过阈值（默认 200ms）的 SQL。
- 池状态暴露：`pool_stats()` 返回空闲/活跃数，接入 `/api/v1/admin/metrics`。
- 压测：`test/bench` 对典型查询（列表分页、批量插入）对比直连 SQL 与 C++ 扩展耗时。

### 5.9 测试策略

| 层级 | 方式 |
|---|---|
| C++ 单元 | Catch2 / doctest，覆盖连接池、预处理语句、事务 |
| Python 集成 | pytest + `test_py.py`，覆盖 DAO 门面、参数化查询、错误分支 |
| 回归 | GitHub Actions 或本机脚本，改 SQL 结构后跑全量 |

---

## 6. 数据库设计

### 6.1 设计原则

- 业务表 **UTF8MB4**；主键统一 `BIGINT AUTO_INCREMENT`；每表含 `created_at / updated_at`。
- 软删除统一 `is_deleted TINYINT`。
- 高频查询字段建索引；组合索引遵循最左前缀。
- AI 训练数据表独立成组，便于导出与脱敏。

### 6.2 表清单（按模块）

**用户体系**
- `user`：id, openid, unionid, phone, nickname, avatar, role(0学生/1管理员), student_no, major, grade, created_at
- `user_tag`：user_id, tag（兴趣标签，供推荐）
- `student_profile`：user_id, department, campus, year_grade, preferences

**AI 助手 / 对话**
- `ai_conversation`：id, user_id, title, model_name, created_at
- `ai_message`：id, conversation_id, role(user/assistant/system), content, created_at
- `quick_command`：id, keyword, template, target_module

**Agent 任务**
- `agent_task`：id, user_id, task_type(预约/提醒/查询/发布), status(pending/running/success/failed), params_json, result_json, created_at, finished_at
- `agent_tool`：id, name, description, endpoint（工具注册表）
- `reminder`：id, user_id, content, remind_at, is_done, task_id

**图书馆**
- `building`：id, name, campus, location, floor_count
- `room`：id, building_id, floor, name, capacity, type(阅览室/教室), has_power
- `seat`：id, room_id, seat_no, status, has_power
- `seat_reservation`：id, seat_id, user_id, date, begin_time, end_time, status(预约/签到/取消/过期)
- `occupancy_record`：id, room_id, ts, occupancy_rate（人流/拥挤度历史，供 AI 预测）
- `classroom_schedule`：id, room_id, weekday, period, course_name, status（空教室判定数据）

**二手交易**
- `secondhand_item`：id, user_id, title, description, category, price, images_json, status(在售/已售/下架), is_ai_audited, trust_score
- `secondhand_wish`：id, user_id, content, category, budget, status
- `secondhand_order`：id, item_id, buyer_id, seller_id, amount, status, created_at
- `item_message`：id, item_id, from_user, to_user, content

**兼职实习**
- `company`：id, name, qualification, credit_score, address
- `job_post`：id, company_id, title, type(校内勤工/实习/兼职), description, pay, tags_json, risk_level, trust_score, status
- `job_application`：id, job_id, user_id, resume_text, status, created_at
- `user_job_blacklist`：id, user_id, company_id, reason（诚信档案）

**论坛**
- `topic`：id, title, category, content, author_id, like_count, comment_count, status, is_audited, ai_summary, is_hot
- `comment`：id, topic_id, parent_id, author_id, content
- `like_record`：id, user_id, target_type, target_id, created_at
- `favorite`：id, user_id, target_type, target_id

**生活服务**
- `merchant`：id, name, category, location, delivery_fee, avg_score
- `menu_item`：id, merchant_id, name, price, image
- `takeaway_order`：id, user_id, merchant_id, items_json, amount, status, created_at
- `campus_notice`：id, title, content, source, category, target_grade, published_at
- `notice_read`：id, notice_id, user_id（精准推送已读回执）

**地图**
- `poi`：id, name, category, building_id, latitude, longitude, floor
- `navigation_log`：id, user_id, from_poi, to_poi, path_json（路线，供定位联动）

**AI 训练数据（数据闭环）**
- `train_corpus`：id, source_type(forum/chat/notice), content, raw_id, created_at
- `train_annotation`：id, corpus_id, instruction, output, is_verified（标注后指令样本）
- `model_version`：id, name, base_model, method(QLoRA), quant_level, metrics_json, status, trained_at（模型版本管理）
- `knowledge_doc`：id, title, category, content, chunk_count, source_url, updated_at（RAG 知识库元数据）
- `feedback`：id, user_id, target_type, target_id, rating, content（回答/服务反馈）

### 6.3 ER 关系要点

```
user 1─N user_tag / student_profile / ai_conversation
user 1─N ai_message (通过 conversation)
user 1─N agent_task / reminder / seat_reservation
building 1─N room 1─N seat / occupancy_record
user 1─N secondhand_item(卖家) / secondhand_order(买家/卖家)
company 1─N job_post 1─N job_application N─1 user
user 1─N topic 1─N comment；user N─N topic(like/favorite)
user 1─N takeaway_order；merchant 1─N menu_item
knowledge_doc → chunk → 向量（向量库）(RAG)
train_corpus 1─N train_annotation → model_version
```

### 6.4 SQL 与迁移管理

- `sql/` 目录组织：`01_schema.sql`（建库建表）、`02_init_data.sql`（模拟数据/种子数据）、`03_indexes.sql`。
- 项目使用 **Alembic** 做表结构版本迁移（Python 侧），C++ 层只做数据操作不管理 DDL，避免双轨冲突。
- 现有 `sql/er` 占位文件 → 迁移为 `docs/db/ER-图.md` 与 `sql/` 正式脚本。

---

## 7. AI 架构

### 7.1 三引擎总览

```
             ┌────────────── 在线推理链路 ──────────────┐
用户问题 ──▶ 意图路由
              ├─ 高频校园知识 → RAG 引擎（检索+增强）──▶ 生成
              ├─ 通用对话/创作 → 基础模型直答
              └─ 可执行指令  → Agent 引擎（工具调度）─▶ 业务接口
             └─────────────────────────────────────────┘
             ┌────────────── 离线链路 ──────────────┐
校园数据 → 清洗/脱敏 → 标注 → QLoRA 微调 → 评估 → GGUF 量化 → Ollama 发布
                                        └──▶ RAG 知识库更新（embedding）
```

### 7.2 RAG 高可信问答

- **知识来源**：学校官方通知、院系政策、图书馆/校医院规则、校园手册、FAQ（先收集公开信息，答辩说明官方对接方案）。
- **构建流程**：文档 → 去重/清洗 → 按语义分块（500~800 字符，带重叠 50）→ `bge-m3` 向量化 → 存入 ChromaDB（collection 按分类）。
- **检索**：查询向量化 → top-k（默认 5）→ 可加元数据过滤（分类）。
- **生成**：`[系统] 仅依据以下知识回答，不得臆造；附来源` + 检索片段 + 用户问题 → Qwen2.5-7B → 流式返回；支持"来源可点击"。
- **兜底**：检索置信度低（cosine 低于阈值）时明确回答"未收录，建议咨询官方"，避免幻觉。
- **更新**：知识变更走管理后台触发增量入库。

### 7.3 模型微调流水线（成员2/3 联合职责）

| 阶段 | 输入 | 处理 | 输出 |
|---|---|---|---|
| 数据采集 | 论坛帖子、对话记录、官方通知、FAQ | 定时任务拉取 `train_corpus` | 原始语料 |
| 清洗脱敏 | 原始语料 | 去重、去 PII（学号/手机号）、过滤广告垃圾 | 干净语料 |
| 构建指令集 | 干净语料 | 组织成 `instruction / input / output` 格式（system 固定校园助手人设） | `train.jsonl` |
| 训练 | `train.jsonl` | `peft` + `bitsandbytes`，QLoRA：`r=16, alpha=32, dropout=0.1`，target `q/k/v/o_proj`，lr 2e-4，epochs 3，batch 视显存 | LoRA adapter |
| 评估 | 留出测试集 | 准确率 / BLEU / 人工抽检问答 | 评估报告（写入 `model_version`） |
| 发布 | adapter + 基座 | 合并 → `llama.cpp` 转 `GGUF` → `q4_k_m` 量化 | `model-xxx.gguf` |
| 部署 | GGUF | 导入 Ollama / llama.cpp server | 推理服务 |

- 训练硬件建议：单卡 16G+（可跑 7B QLoRA）；学生本 8G 显存 → 用 Qwen2.5-3B 或 MiniCPM3-4B 备选。
- 训练代码库：`ai/finetune/`（独立目录，含数据脚本、训练脚本、评估脚本）。

### 7.4 推理服务

- **Ollama**（首选）：`ollama create xjt-model -f Modelfile` 从 GGUF 创建，`/api/chat` 流式接口，FastAPI 通过 `httpx` 异步调用。
- **llama.cpp server**（备选/端侧）：`llama-server -m model.gguf --port 8080`，OpenAI 兼容接口。
- 多轮记忆：对话历史存入 `ai_message`，请求时取最近 N 轮拼入 context。

### 7.5 AI Agent 工具编排（M2）

- **架构**：`agent_engine.py` 接收自然语言 → Qwen function-calling 能力解析意图与参数（JSON）→ 匹配工具注册表 → 逐工具调用业务服务 → 汇总结果回用户。
- **工具注册表**（对应 `agent_tool` 表）：

| 工具名 | 触发意图 | 对应服务 |
|---|---|---|
| reserve_seat | "帮我预约图书馆座位" | library.reserve |
| query_free_room | "查空教室" | library.rooms |
| add_reminder | "提醒我下午4点选课" | reminder.create |
| post_secondhand | "发布二手：卖自行车" | secondhand.create |
| match_wish | "帮我找求购" | secondhand.match |
| apply_job | "投递岗位" | job.apply |
| query_notice | "最近的通知" | life.notices |
| create_post | "发帖" | forum.create |

- **执行模型**：重任务（预约、批量）走 Celery 异步，`agent_task` 记录状态，前端轮询；轻查询同步返回。
- **失败兜底**：工具调用失败 → 返回原因 + 替代方案（如该时段座位满了推荐其他楼层）。

### 7.6 多模态

- **图像识别**：二手拍照识别 → 用微调后模型或现成 CLIP/Blip 文本描述 + 关键词映射品类；教学楼识别 → 匹配 POI。
- **语音**：前端录音 → 后端语音识别（微信同声传译插件或 Whisper 小模型）→ 文本入对话 → 文本转语音（Edge-TTS / 微信播报 API）。
- **定位**：`wx.getLocation` → 后端 POI 最近邻 → 周边服务/空教室/活动推送。

### 7.7 端侧 AI 策略（ADR-3）

- 在线体验全部走服务端 Ollama 推理，保证质量与可演示性。
- 端侧创新点：`ai/edge/` 提供 **桌面端 llama.cpp 离线推理 demo**（同一 GGUF 模型），展示"无网离线问答、本地隐私"，答辩时演示，并保留"小程序端侧"作为后续演进说明。
- 文档如实说明小程序包体积限制（约 20MB）与"真端侧"的取舍，避免答辩被质疑夸大。

### 7.8 数据闭环（自进化）

```
用户行为（论坛/对话/反馈）→ 定时回流（data_reflow）→ train_corpus
→ 清洗标注 → train_annotation → 季度微调 → model_version 更新
→ 服务优化 → 用户反馈 → 再回流
```
- 数据隐私：脱敏（学号、姓名、手机号打码）后才进训练库；提供用户退出采集的开关。

---

## 8. 接口设计（核心 API 清单）

### 8.1 通用约定

- Base：`https://api.xjt.example.com/api/v1`
- 认证：`Authorization: Bearer <JWT>`
- 分页：`?page=1&size=20`，返回 `{items, total, page, size}`

### 8.2 核心接口

**Auth**
- `POST /auth/wechat-login`（code 换 openid+token）
- `POST /auth/refresh`、`POST /auth/logout`

**用户**
- `GET /user/me`、`PUT /user/me`、`PUT /user/tags`

**AI 助手**
- `POST /chat/send`（SSE 流式返回，RAG 对话）
- `POST /chat/{id}/stop`、`GET /chat/history`
- `POST /chat/quick`（快捷指令）

**Agent**
- `POST /agent/tasks`（创建任务）、`GET /agent/tasks/{id}`、`GET /agent/tasks`（列表）
- `DELETE /agent/tasks/{id}`
- `POST /agent/tools`（管理员注册工具）

**图书馆**
- `GET /library/rooms?campus=&floor=&period=`（空教室）
- `GET /library/seats?room_id=&date=`、`POST /library/reservations`
- `GET /library/reservations/me`、`POST /library/reservations/{id}/cancel`
- `GET /library/occupancy?room_id=&date=`（拥挤度/AI 预测）

**二手**
- `GET /secondhand/items?category=&q=`、`POST /secondhand/items`
- `POST /secondhand/items/ai-describe`（拍照自动描述+定价）
- `POST /secondhand/wishes`、`GET /secondhand/match`（AI 供需匹配）
- `POST /secondhand/orders`

**兼职**
- `GET /jobs?type=&q=`、`POST /jobs/{id}/apply`
- `GET /jobs/{id}/trust`（可信度评分）

**论坛**
- `GET /topics?category=`、`POST /topics`
- `POST /topics/{id}/comments`、`POST /topics/{id}/like`
- `POST /topics/{id}/report`（举报）

**地图**
- `GET /map/pois`、`GET /map/nearby?lat=&lng=`（周边服务）
- `POST /map/navigate`（路线规划）

**生活**
- `GET /life/merchants`、`GET /life/merchants/{id}/menu`
- `POST /life/orders`、`GET /life/orders/{id}`（配送进度）
- `GET /life/notices`（按标签精准推送）

**管理/运维**
- `GET /admin/metrics`（含 C++ 连接池 stats）
- `POST /admin/knowledge/ingest`（知识入库）、`GET /admin/train-corpus`
- `GET /health`

### 8.3 错误码分段

| 区间 | 含义 |
|---|---|
| 0 | 成功 |
| 100x | 通用/参数错误 |
| 200x | 认证/权限 |
| 300x | 业务冲突（如座位已被预约） |
| 500x | 服务端/C++ 层/模型错误 |

---

## 9. 部署与运维

### 9.1 部署拓扑

```
微信小程序 ──HTTPS──▶ Nginx(:443)
                        ├── /api → Uvicorn(FastAPI) :8000
                        ├── /static → 静态资源
Redis :6379（缓存/任务队列）
Celery worker（异步任务：Agent、数据回流）
Ollama :11434（GGUF 推理）
MySQL 8.0 :3306（数据，C++ 扩展连接）
（可选）ChromaDB / FAISS 索引目录
```

### 9.2 容器化

```
├── docker-compose.yml   # nginx + api + celery + redis + mysql + ollama
├── backend/Dockerfile   # 构建时：编译 C++ 扩展(so) → 安装依赖
└── deploy/
    ├── nginx.conf
    └── .env.example
```

- `backend/Dockerfile` 关键点：`RUN cmake -B build && cmake --build build` 编译 `jt_db.so` → 复制到 `app/db/native/`。
- Ollama 镜像内置模型拉取脚本（首次启动下载 GGUF）。

### 9.3 服务器规划（学生机）

| 资源 | 建议 | 说明 |
|---|---|---|
| CPU | 2~4 核 | 足够业务 + 7B QLoRA 推理（CPU 推理可用，速度慢） |
| 内存 | 8G+ | GGUF q4 7B 约 5~6G 内存 |
| 磁盘 | 40G+ | 模型 + 数据库 + 日志 |
| GPU | 无则 CPU 推理（演示够用）；有则加速 | 训练用本地笔记本完成，服务器只做推理 |

### 9.4 安全

- HTTPS 证书（Let's Encrypt / 云厂商免费证书），微信小程序强制要求。
- 密码/密钥存环境变量，JWT 过期短；`openid` 用于用户绑定。
- 敏感接口限流；文件上传校验类型与大小；论坛内容入库前调 AI 审核。
- 日志脱敏（不记录 token、手机号明文）。

---

## 10. 开发规范与协作

### 10.1 Git 分支模型

```
main（稳定，可发布）
 └── dev（集成联调）
      ├── feature/frontend      # 成员1 小程序
      ├── feature/backend       # 成员2 Python 后端 + AI 集成
      ├── feature/db-cpp_driver # 成员3 C++ 数据访问层
      ├── feature/ai-finetune   # 成员2 模型微调
      └── docs                  # 成员4 文档/数据库
```
（现有远程分支已基本对应，继续沿用。）

### 10.2 环境约定

| 环境 | 用途 | 说明 |
|---|---|---|
| dev | 成员本机 | Windows/VS 编译 `.pyd`，本地 MySQL |
| staging | 服务器（可选） | 联调 |
| prod | 学生机 | Docker 部署，Linux 编译 `.so` |

### 10.3 目录总览（建议仓库结构）

```
xiaojietong-project/
├── docs/                  # 架构方案、需求、ER 图、接口文档
├── backend/               # FastAPI 后端（含 C++ 扩展桥接层）
├── miniprogram/           # 微信小程序前端
├── db/
│   ├── cpp_driver/        # C++ 数据访问层（pybind11）
│   └── sql/               # 建表脚本 + 种子数据
├── ai/
│   ├── finetune/          # 微调训练代码 + 数据脚本
│   ├── rag/               # 知识库构建/检索代码
│   ├── edge/              # 端侧 llama.cpp demo
│   └── eval/              # 评估脚本
├── deploy/                # docker-compose、nginx、脚本
└── README.md
```

---

## 11. 16 周落地计划（按职责对齐）

> 与既有《16 周具体计划》对齐，补充 C++/数据/AI 视角的关键任务。

| 周 | 阶段 | 成员3（C++ 底层+MySQL） | 成员2（Python 后端+AI） | 成员1（前端） | 成员4（DB+文档+数据） |
|---|---|---|---|---|---|
| 1 | 筹备 | 环境：MySQL C API + pybind11 编译链跑通（Win） | FastAPI 骨架 + Ollama 安装 | 小程序空工程 + TabBar | 需求文档、接口清单初稿 |
| 2 | 筹备 | 连接池设计与开发（核心类） | API 规范落地 | 路由/登录页 | 数据库 ER 图、表设计 |
| 3 | 基础 | 驱动封装 + 预处理语句 + 事务 | 登录/注册接口（调 C++） | 登录联调 | `01_schema.sql` 建表 |
| 4 | 基础 | UserDAO + 分页通用 DAO | 通用工具类、权限 | 用户体系页面 | 种子数据 `02_init_data.sql` |
| 5 | 业务 | LibraryDAO（座位/预约） | AI 助手对话接口（SSE） | AI 对话页 | 知识库语料收集 |
| 6 | AI | 连接池压测 + 慢查询监控 | RAG 流程（ChromaDB+bge） | 对话流式渲染 | 知识库分块、FAQ 整理 |
| 7 | 业务 | SecondhandDAO + 批量写 | 图书馆预约接口 + 拥挤度 | 图书馆页 | 测试用例 |
| 8 | 业务 | ForumDAO（帖子/评论/点赞） | 二手 AI 描述/定价、论坛审核 | 二手页 | AI 训练数据初版 |
| 9 | 业务 | JobDAO + 事务用例 | 兼职接口、论坛推荐 | 兼职+论坛页 | 数据脱敏脚本 |
| 10 | 业务 | 全 DAO 回归 + 性能优化 | 地图 POI、生活服务接口 | 地图+生活页 | 接口文档 v1 |
| 11 | AI | 训练数据批量导入接口（C++ 加速） | Agent 引擎 + 工具注册 | Agent 任务中心页 | 标注工具/流程 |
| 12 | AI | 数据集导出工具 | QLoRA 微调 → GGUF → Ollama 发布 | 语音/图像接入 | 评估集、训练报告 |
| 13 | AI | 指标接口 /admin/metrics | 多模态、通知精准推送 | 全功能联调 | 端侧 demo 脚本 |
| 14 | 集成 | C++ 扩展 Linux 编译 + 容器化 | 全链路联调、异常处理 | BUG 修复 | 集成测试 |
| 15 | 上线 | 服务器部署 + 监控 | 生产部署、HTTPS | 提审内测 | 部署文档 |
| 16 | 验收 | 压测报告、性能总结 | 训练报告、架构文档定稿 | 演示视频 | 结题材料、PPT |

---

## 12. 风险与应对

| # | 风险 | 影响 | 应对 |
|---|---|---|---|
| 1 | C++ 扩展跨平台编译（Win .pyd → Linux .so）出问题 | 数据层阻塞 | 第 14 周提前做 Linux 编译验证；抽象编译脚本；保留 SQLAlchemy 直连作为应急旁路 |
| 2 | 7B 模型训练显存不足 | 微调失败 | 降级 Qwen2.5-3B / MiniCPM3-4B；QLoRA 4bit；数据精简 |
| 3 | 学校系统无开放接口 | 预约/空教室"真实数据"难获取 | 模拟数据 + 预留 DAO 接口，答辩如实说明对接方案（ADR-5） |
| 4 | 端侧部署被质疑不可行 | 答辩扣分 | 使用"服务端为主 + 桌面端离线 demo"的务实表述（ADR-3） |
| 5 | 多模态（语音/图像识别）工作量大 | 延期 | 分优先级：文本为核心，语音/图像用现成 API 插件实现 |
| 6 | 团队并发协作冲突 | 联调混乱 | 严格按 10.1 分支模型 + 接口文档先行；每周对齐接口契约 |
| 7 | 数据隐私（论坛/对话回流训练） | 合规风险 | 脱敏管线、用户退出开关、答辩强调隐私保护 |

---

## 13. 下一步行动

1. **评审本文档**：全体成员过一遍 2.3 选型表与 5 节 C++ 层设计。
2. **成员3 优先启动**：第 1 周跑通「MySQL C API + pybind11 → `jt_db.pyd` → Python import 查询」最小闭环。
3. **成员4 补数据库设计**：将 6.2 表清单落地为 `sql/01_schema.sql` 与 ER 图。
4. **补充接口契约**：按第 8 节清单在 `docs/` 维护 `api.md` 接口文档，作为前端/后端对齐基线。
