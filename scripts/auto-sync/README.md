# 主仓库自动同步工具（校捷通）

> 场景：**多人协作同一个 GitHub 主仓库**（`https://github.com/asuperj1/xiaojietong-project.git`，非 fork 模式）。当团队有人提交 PR 并合并进主仓库后，协作者本地运行本工具即可**自动拉取主仓库最新代码**，让本地与仓库保持一致。
>
> 跨平台：Windows（Git Bash）/ Linux / macOS。仅依赖 `git` 与系统自带工具，**不引入任何第三方库**。

## 目录结构（按功能拆分，勿合并堆砌）

| 文件 | 职责 |
|---|---|
| `config.sh` | **配置区**：远程名 / 目标分支 / 记录文件 / 日志 / 间隔（团队成员只需改这里） |
| `sync.sh` | **主脚本（入口）**：检测 → fetch → 快进拉取 → 更新记录 → 输出 AI 指令 |
| `lib.sh` | **函数库**：日志、记录读写、远端哈希、AI 提示词输出等公共函数 |
| `prompt_a.txt` | 场景A：拉取成功后的 **AI 代码审查指令** |
| `prompt_b.txt` | 场景B：发生合并冲突时的 **AI 冲突处理指令** |
| `README.md` | 本使用文档 |

## 一、配置区（每个协作者必改/确认）

打开 `scripts/auto-sync/config.sh`（也支持环境变量覆盖，方便脚本/本机差异）：

```bash
REMOTE_NAME="origin"     # 主仓库远程名；git clone 后默认 origin，通常不用改
TARGET_BRANCH="main"     # ← 跟随哪个分支：稳定版 main；想实时跟随团队最新集成改 dev
RECORD_FILE=".git/auto_sync_head.txt"   # 记录文件（.git 内，不入库）
LOG_FILE=".git/auto_sync.log"           # 日志文件（.git 内）
SCHEDULE_MINUTES=30      # 调度间隔（供 --daemon / 调度器参考）
```

运行时可临时覆盖（不改文件）：`XJT_REMOTE_NAME=github XJT_TARGET_BRANCH=dev ./sync.sh dev`

> 本项目功能先合入 `dev` 再发 `main`。**想实时跟随最新代码的协作者请把 `TARGET_BRANCH` 改为 `dev`**，并让本地停留在 `dev` 分支。
> ⚠️ **本机开发机特殊说明**：若你的电脑把 gitee 设为 `origin`、GitHub 设为 `github`（本项目仓库即如此），自动同步应指向 **GitHub 主仓库**，请把 `REMOTE_NAME` 改为 `github`，或用环境变量 `XJT_REMOTE_NAME=github`。

## 二、本地仓库初始化

```bash
# 1) 首次克隆（GitHub 主仓库，origin 自动指向主仓库）
git clone https://github.com/asuperj1/xiaojietong-project.git
cd xiaojietong-project

# 2) 设置要跟随的分支为默认（示例跟随 dev，协作者按需选 main/dev）
git checkout dev          # 或 main

# 3) 首次运行一次脚本（只初始化记录，不拉取；验证脚本可执行）
bash scripts/auto-sync/sync.sh dev
# 预期输出：首次运行：记录已初始化为 <hash>，本轮不拉取。
```

对已存在的本地仓库（已 clone/已开发过）：
```bash
cd 你的仓库目录
git fetch origin
git config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*"   # 可选，确保跟踪所有分支
bash scripts/auto-sync/sync.sh dev      # 目标分支按你 config.sh 或参数
```

## 三、每 30 分钟自动执行

### Windows（Git Bash）— 任务计划程序

**图形界面步骤**：
1. `Win+R` → `taskschd.msc` → 右侧“创建任务”。
2. 常规：名称 `XJT-AutoSync`；勾选“使用最高权限运行”可不用（普通权限即可）。
3. 触发器 → 新建 → 开始任务：按计划 → **重复任务间隔：30 分钟**，持续无限期。
4. 操作 → 新建 → 程序：`C:\Program Files\Git\bin\bash.exe`
   参数：`-lc "cd /d/xiaojietongproject/xiaojietong-project && ./scripts/auto-sync/sync.sh dev"`
   （把路径换成你的仓库路径与目标分支）
5. 条件：建议取消“只有在计算机使用交流电源时才启动”。

**命令行方式（等价，管理员 PowerShell）**：
```powershell
schtasks /Create /TN "XJT-AutoSync" /F /SC MINUTE /MO 30 `
  /TR "\"C:\Program Files\Git\bin\bash.exe\" -lc \"cd /d/xiaojietongproject/xiaojietong-project && ./scripts/auto-sync/sync.sh dev\""
```
> 路径按你的实际仓库位置调整（Git Bash 中 `C:\` 写作 `/c/`，如 `/d/xiaojietongproject/...`）。

### Linux / macOS — cron

```bash
crontab -e
# 追加一行（每 30 分钟；把 /home/you/... 换成你的仓库路径）
*/30 * * * *  cd /home/you/xiaojietong-project && ./scripts/auto-sync/sync.sh dev >> /dev/null 2>&1
```

或使用常驻模式（终端/服务里运行，无需 cron）：
```bash
./scripts/auto-sync/sync.sh --daemon      # 每 30 分钟循环检测，Ctrl+C 退出
```

## 四、脚本行为（自动逻辑）

1. 每次运行：`git ls-remote origin refs/heads/<目标>` 获取远端最新 commit。
2. 与 `.git/auto_sync_head.txt` 记录对比：
   - **相同** → 打印“无更新”，直接退出，零操作；
   - **不同（PR 已合并）** → `git fetch` → 若本地当前分支就是目标分支 → `git merge --ff-only`：
     - 成功：更新记录、打印**场景A 代码审查指令**（`prompt_a.txt`）；
     - 冲突/无法快进：**立即中止、不覆盖本地代码**、记录不更新、打印**场景B 冲突处理指令**（`prompt_b.txt`）。
3. 容错：网络异常 / git 权限 / 仓库连接失败 → 捕获并打印错误，**不破坏本地代码**。

> ⚠️ 自动快进只发生在**本地当前分支 == 目标分支**时，以保护你在其它分支上的未提交工作。建议把工作分支日常停留在 `dev`/`main`。

## 五、团队使用规则（务必遵守）

1. **不要在 `main` 上留未推送的领先提交**：`main` 只接收 `dev→main` PR（见 `docs/团队Git合作协议.md`）。本地 `main` 与远端分叉会导致自动快进失败，从而打印冲突提示。
2. **同步目标建议用 `dev`**：功能都在 `dev` 集成后再发 `main`，协作者跟 `dev` 才能实时拿到队友的 PR 成果。
3. 出现**合并冲突**：按 `prompt_b.txt` 把冲突场景交给 AI 分析，本地 VS Code 解决后提交；解决前脚本不会覆盖你的代码。
4. 每次同步成功后，按 `prompt_a.txt` 让 AI 审查一遍最新 diff，再继续开发。
5. 若换机器/清空 `.git`，重新运行一次脚本即可重新初始化记录。

## 六、常见问题

| 问题 | 处理 |
|---|---|
| `bash: 不是内部或外部命令`（Windows） | 安装 Git for Windows，并用 `C:\Program Files\Git\bin\bash.exe` 调用 |
| 拉取报 CRLF 相关 | 仓库已含 `.gitattributes` 强制 `.sh/.txt` 用 LF；如有异常执行 `git add --renormalize .` |
| 一直提示 fetch 失败 | 检查网络/代理（`git config http.https://github.com.proxy` 是否正确）；脚本不会改动本地代码，可安全重试 |
| 想手动立即同步一次 | `bash scripts/auto-sync/sync.sh dev` |
