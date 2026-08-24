# 校捷通 —— 吉林大学一站式校园智能服务平台

> 基于轻量化大模型与 RAG + AI Agent 的校园智能服务平台（大学生创新创业训练计划项目）

## 仓库结构

```
xiaojietong-project/
├── docs/                  # 架构方案、需求、ER、接口文档
├── backend/               # Python 后端（FastAPI，含 C++ 扩展桥接层）
├── miniprogram/           # 微信小程序前端
├── db/
│   ├── cpp_driver/        # C++ 数据访问层（pybind11 扩展，高性能/连接池）
│   └── sql/               # MySQL 建表脚本与种子数据
├── ai/
│   ├── finetune/          # 大模型微调（LoRA/QLoRA）
│   ├── rag/               # RAG 知识库构建与检索
│   ├── edge/              # 端侧 llama.cpp 离线 demo
│   └── eval/              # 模型/接口评估
├── deploy/                # docker-compose、nginx、部署脚本
└── README.md
```

## 团队分工

| 角色 | 负责人 | 职责域 |
|---|---|---|
| 前端 | 成员1 | 微信小程序页面与交互 |
| Python 后端 + AI | 成员2 | FastAPI 接口、RAG/Agent、模型微调 |
| C++ 底层 + MySQL | 成员3 | C++ 数据访问层（连接池/DAO/pybind11） |
| 数据库 + 文档 + 数据 | 成员4 | 表设计、文档、测试、训练数据处理 |

## 快速开始

1. **C++ 数据访问层**（成员3 优先启动）：见 `db/cpp_driver/README.md`，编译 `jt_db` 扩展并跑通 Python 查询。
2. **后端**：见 `backend/README.md`。
3. **数据库**：`db/sql/01_schema.sql` 建库建表。

> 系统整体架构与设计决策见 `docs/architecture.md`。
