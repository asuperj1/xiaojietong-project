# 分支管理规范（团队约定）· 速查版

> ⚠️ **本文件为速查**。分支/Commit/PR/保护/冲突/禁止清单的**权威规范见 `docs/团队Git合作协议.md`**（2026-09-08 起生效，替代本文件旧条款，冲突处以协议为准）。
> 本文件定义仓库的分支策略。**所有成员严格遵循**，避免代码提交到错误分支。> 🔒 **当前保护现状（2026-09-09 起）**：GitHub 规则集 `team-branch-protect` 仅保护 `main`；**禁止任何人（含 Owner）推送 `main`**，`main` 只接收 `dev → main` 的 PR；`dev` 不设保护、可直接推送/合并。
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

## 补充：短期临时分支 + PR / 合并 / 保护（详见团队Git合作协议.md）

| 分支 | 说明 | 从哪拉 | 合并到 | 合并后 |
|---|---|---|---|---|
| `feat/用户名-功能` | 新功能 | `dev` | PR → `dev` | 删除 |
| `fix/用户名-bug` | 修 bug | `dev` | PR → `dev` | 删除 |
| `docs/用户名-文档` | 改文档 | `dev` | PR → `dev` | 删除 |
| `refactor/用户名-模块` | 重构 | `dev` | PR → `dev` | 删除 |
| `perf/用户名-优化` | 性能优化 | `dev` | PR → `dev` | 删除 |
| `hotfix/简述` | main 紧急修复 | `main` | merge → `main` + `dev` | 删除 |

- 模块长驻分支 `feature/*`、`docs` 开发后同样走 **PR → `dev`**；`main` 只接收来自 `dev` 的合并。
- **合并一律 Squash and merge**（hotfix 除外）；PR 目标分支只能是 `dev`，不是 `main`。
- PR 必须填模板（`.github/PULL_REQUEST_TEMPLATE.md`）、至少 1 人 approve（本人不可自批）；冲突本地解决。
- 管理员需在 GitHub Settings 开启 `main` / `dev` 分支保护（见协议 §6）。
