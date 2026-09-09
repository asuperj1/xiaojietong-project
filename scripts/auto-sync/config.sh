#!/usr/bin/env bash
# =============================================================================
# 校捷通 · 主仓库自动同步 —— 配置区（团队成员只需要修改本文件）
# 文件路径：scripts/auto-sync/config.sh
# 说明：本文件被 sync.sh source；修改后保存即可生效。
#       支持用环境变量覆盖（便于脚本化）：XJT_REMOTE_NAME / XJT_TARGET_BRANCH
# =============================================================================

# 1) 主仓库远程名称
#    git remote -v 可查看；从 GitHub 主仓库 clone 后默认即为 origin。
#    本项目主仓库：https://github.com/asuperj1/xiaojietong-project.git
#    ⚠️ 本机开发机若镜像配置了 gitee 为 origin、GitHub 为 github，请设为 github，
#       或运行时加环境变量：XJT_REMOTE_NAME=github ./sync.sh
REMOTE_NAME="${XJT_REMOTE_NAME:-origin}"

# 2) 要自动跟随的目标分支
#    - 跟随稳定版：main（GitHub 已保护 main，只收 dev→main PR）
#    - 跟随最新集成（推荐协作者）：dev（本仓库功能先合入 dev 再发 main）
#    修改成 dev 即可实时跟随团队最新功能；本地请保持当前停在对应分支。
TARGET_BRANCH="${XJT_TARGET_BRANCH:-main}"

# 3) 本地"上次拉取完成 commit"的记录文件
#    默认放在 .git 目录内：不污染工作区、不会被误提交。
RECORD_FILE="${XJT_RECORD_FILE:-.git/auto_sync_head.txt}"

# 4) 日志文件（追加写入；同样放 .git 内不入库）
LOG_FILE="${XJT_LOG_FILE:-.git/auto_sync.log}"

# 5) 调度间隔（分钟）：仅供 --daemon 模式使用；
#    日常由 Windows 任务计划 / cron 按该间隔触发 sync.sh（见 README.md）
SCHEDULE_MINUTES=30

# 6) AI 提示词文件（位于本脚本同目录，通常无需修改）
PROMPT_A_FILE="prompt_a.txt"   # 场景A：拉取成功 → 代码审查指令
PROMPT_B_FILE="prompt_b.txt"   # 场景B：合并冲突 → 冲突处理指令
