# 分支管理规范（团队约定）

> 本文件定义仓库的分支策略。**所有成员严格遵循**，避免代码提交到错误分支。

## 分支一览（完整树）

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
>
> **完整树**：所有分支都包含完整项目目录，通过规范约束"在哪个分支改哪个目录"，各模块可独立开发、互不冲突，且可直接使用 GitHub PR 合并。

## 提交流程

```
feature/xxx（各模块开发） → dev（联调） → main（正式发布）
```

1. 各成员在对应 `feature/*` 分支开发，**只改自己模块的目录**。
2. 功能完成 → 合并到 `dev`（GitHub PR 或本地 merge），做整体联调，修复模块对接 bug。
3. `dev` 测试全部通过 → 合并到 `main`（GitHub PR），作为稳定版本。
4. 文档（`*.md`）改动一律走 `docs` 分支，不要跑到业务代码分支改文档。

```bash
# 本地合并示例
git checkout dev && git merge feature/backend     # 后端合入 dev 联调
git checkout main && git merge dev                # 联调通过后发版
```

## 约束

- `main` 分支受保护，禁止直接 push 代码（从 PR / `dev` 合并）。
- 各 `feature/*` 分支**只改自己的目录**，不要改动其它模块目录。
- 各 `feature/*` 分支定期从 `dev` 同步（`git merge dev`），避免长期分叉。
- 文档与代码分开：README / 架构 / 接口 / 开发手册在 `docs` 分支维护。
