# 分支管理规范（团队约定）

> 本文件定义仓库的分支策略。**所有成员严格遵循**，避免代码提交到错误分支。

## 分支一览

| 分支 | 用途 | 改动范围 |
|---|---|---|
| `main` | **稳定版**：测试通过、可跑的正式版本；大创结题/提交文档用它 | 禁止直接改代码，只从 `dev` 合并 |
| `dev` | **联调总分支**：各 feature 合并后整体联调 | 接收各 feature 分支合并 |
| `docs` | 文档：README、架构图、接口文档、开发手册、竞赛申报材料 | 所有 `*.md` 文档 |
| `feature/backend` | 后端 Python FastAPI 业务模块 | `backend/` |
| `feature/db` | 数据库（业务层）：建表 SQL、种子数据 | `db/sql/` |
| `feature/db-cpp_driver_src` | **C++ 数据库驱动层**：连接池、DAO、pybind11 | `db/cpp_driver/` |
| `feature/frontend` | 微信小程序前端 | `miniprogram/` |
| `feature/ui` | UI 素材：图片、图标、静态资源 | `ui/`（只放资源，不写业务逻辑） |

> 注意区分：`feature/db`（业务数据库脚本）与 `feature/db-cpp_driver_src`（C++ 驱动层）。

## 提交流程

```
feature/xxx（开发） → dev（联调） → main（正式发布）
```

1. 在对应 `feature/*` 分支开发，**只改自己模块的目录**。
2. 功能完成 → 合并到 `dev`，做模块间整体联调，发现并修复对接 bug。
3. `dev` 测试全部通过 → 合并到 `main`，作为稳定版本。
4. 文档（`*.md`）改动一律走 `docs` 分支，不要跑到业务代码分支改文档。

## 约束

- `main` 分支受保护，禁止直接 push 代码（Web 端已开启保护则从 PR 合并）。
- 文档与代码分开：README / 架构 / 接口 / 开发手册在 `docs` 分支维护。
- 各 feature 分支**基于最新 `dev`** 拉取，避免长期分叉。
