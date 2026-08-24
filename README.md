<div align="center">

# 🎓 校捷通 · XJT Campus

**吉林大学一站式校园智能服务平台**

> 微信小程序 + FastAPI + C++ 高性能数据层 + 轻量大模型（RAG / AI Agent）

![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110-009688?logo=fastapi&logoColor=white)
![C++ 17](https://img.shields.io/badge/C%2B%2B-17-00599C?logo=cplusplus&logoColor=white)
![MySQL 8](https://img.shields.io/badge/MySQL-8.0-4479A1?logo=mysql&logoColor=white)
![微信小程序](https://img.shields.io/badge/小程序-原生-07C160?logo=wechat&logoColor=white)
![Ollama](https://img.shields.io/badge/Ollama-GGUF%20%2F%20bge--m3-000000?logo=ollama&logoColor=white)

</div>

一站式覆盖 **学习 · 生活 · 交易 · 社交 · 求职** 五大校园场景，AI 深度嵌入：知识库问答（RAG）、任务自动执行（Agent）、校园垂直模型微调、端侧量化部署。

## ✨ 功能亮点

- 🧠 **RAG 智能问答** — bge-m3 向量检索 + 校园知识库（图书馆 / 校医院 / 政策 / FAQ），相似度阈值过滤防幻觉
- 🤖 **AI Agent** — 任务编排与工具调用（座位预约 / 提醒 / 二手发布）
- 🚀 **C++ 高性能数据层** — 连接池 + 预处理语句防注入，pybind11 绑定为 `jt_db` 扩展
- 🗄️ **42 张表** 分模块设计 — 软删除 / 逻辑外键 / JSON 灵活字段
- 📱 **微信小程序原生前端** — 地图 / 定位 / 语音 / 相机

## 🧱 技术栈

| 层 | 技术 |
|---|---|
| 前端 | 微信小程序原生 (WXML/WXSS/JS) |
| 后端 | Python 3.10+ / FastAPI / Uvicorn / pyjwt |
| 数据层 | C++17 + MySQL C API + pybind11 |
| 数据库 | MySQL 8.0 (utf8mb4) |
| AI | Ollama(GGUF) + bge-m3 + ChromaDB + RAG / Agent |

## 📁 仓库结构

> 目录 → 对应开发分支

```
xiaojietong-project/
├── backend/        # FastAPI 后端（业务路由 + RAG/Agent 服务）        → feature/backend
├── db/
│   ├── cpp_driver/ # C++ 数据访问层（连接池 / DAO / pybind11）        → feature/db-cpp_driver_src
│   └── sql/        # 42 张表建表脚本 + 种子数据                       → feature/db
├── ai/             # 微调 / RAG / 端侧 / 评估                        → feature/backend（AI 随后端）
├── miniprogram/    # 微信小程序前端                                  → feature/frontend
├── ui/             # UI 素材：图片 / 图标 / 静态资源                  → feature/ui
├── docs/           # 架构 / 接口契约 / 开发文档                       → docs
├── deploy/         # Docker / Nginx / HTTPS（部署规划）               → （部署阶段）
└── .github/        # Copilot 开发指令                                → docs
```

## 🚀 快速开始

```bash
# 1) 建库（MySQL 8，必须 utf8mb4）
mysql -u root -P 3307 -p --default-character-set=utf8mb4 < db/sql/00_database.sql
#    再逐模块导入 01~10 与 99_init_data

# 2) 构建 C++ 数据层（Windows Git Bash，改了 C++ 必须重编）
cmake --build db/cpp_driver/build --config Release --target jt_db

# 3) 启动后端（DB 凭据用环境变量注入，勿写死在代码）
cd backend
XJT_DB_PORT=3307 XJT_DB_PASSWORD=*** python -m uvicorn app.main:app --reload --port 8000
# Swagger: http://127.0.0.1:8000/docs

# 4) RAG 索引（可选，需 Ollama 加载 bge-m3 后执行）
cd backend && XJT_DB_PORT=3307 XJT_DB_PASSWORD=*** python ../ai/rag/build_index.py --force
```

## 🌿 分支架构

采用 **Git Flow 简化版**：各模块在 `feature/*` 分支开发 → 合入 `dev` 联调 → 发布到 `main` 稳定版。

```mermaid
gitGraph
    commit id: "init"
    branch feature/backend
    branch feature/frontend
    branch feature/db
    checkout feature/backend
    commit id: "backend"
    checkout feature/db
    commit id: "db"
    checkout dev
    merge feature/backend
    merge feature/db
    checkout feature/frontend
    commit id: "frontend"
    checkout dev
    merge feature/frontend
    checkout main
    merge dev
```

| 分支 | 用途 | 说明 |
|---|---|---|
| `main` | 正式稳定版 | 仅从 `dev` 合并，禁止直接改代码 |
| `dev` | 联调总分支 | 汇总各 `feature/*` 做整体联调测试 |
| `docs` | 文档 | README / 架构图 / 接口文档 / 开发手册 |
| `feature/backend` | 后端模块 | `backend/`（FastAPI + RAG / Agent） |
| `feature/db` | 业务数据库 | `db/sql/`（建表 SQL / 种子数据） |
| `feature/db-cpp_driver_src` | C++ 驱动层 | `db/cpp_driver/`（连接池 / DAO / pybind11） |
| `feature/frontend` | 小程序前端 | `miniprogram/` |
| `feature/ui` | UI 资源 | `ui/`（图片 / 图标 / 静态资源） |

> 提交流程：`feature/*` → `dev` 联调 → `main` 发布。完整规范见 [docs/BRANCH_STRATEGY.md](docs/BRANCH_STRATEGY.md)。

## 📚 文档

| 文档 | 说明 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 系统架构与技术决策 (ADR) |
| [docs/api.md](docs/api.md) | 接口契约 v1.0（11 模块 ~60 接口） |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | 完整开发文档与工作量 |
| [backend/README.md](backend/README.md) · [db/cpp_driver/README.md](db/cpp_driver/README.md) | 各层说明 |

## 👥 团队

 · 4 人协作

| 角色 | 职责域 |
|---|---|
| 前端 | 微信小程序页面与交互 |
| 后端 + AI | FastAPI 接口、RAG / Agent、模型微调 |
| C++ 数据层 | 连接池 / DAO / pybind11 扩展 |
| 数据库 + 文档 | 表设计、文档、测试、训练数据处理 |

---


