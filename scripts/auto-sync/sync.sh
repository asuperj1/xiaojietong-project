#!/usr/bin/env bash
# =============================================================================
# 校捷通 · 主仓库自动同步 —— 主脚本（入口）
# 文件路径：scripts/auto-sync/sync.sh
#
# 适用：多人协作同一 GitHub 主仓库（非 fork 模式）。当团队有人 PR 合并进
#       主仓库后，协作者本地运行本脚本即自动拉取主仓库最新代码。
#
# 用法：
#   ./scripts/auto-sync/sync.sh                 # 执行一次检测
#   ./scripts/auto-sync/sync.sh dev             # 执行一次检测（目标分支覆盖为 dev）
#   ./scripts/auto-sync/sync.sh --daemon        # 常驻模式，每 30 分钟自动检测
#   ./scripts/auto-sync/sync.sh --help
#
# 依赖：仅 git + 系统自带工具（bash/date/cat/awk/tee/sleep）
# 配置：见同目录 config.sh；Windows 任务计划 / cron 配置见同目录 README.md
# =============================================================================

set -u

# ---- 定位自身目录与仓库根（脚本位于 <仓库>/scripts/auto-sync/ 下）----
SELF_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" && pwd)"
ROOT="$(CDPATH= cd -- "${SELF_DIR}/../.." && pwd)"

. "${SELF_DIR}/config.sh"
. "${SELF_DIR}/lib.sh"

DAEMON=0
TARGET="${TARGET_BRANCH:-main}"

usage() {
    cat <<'EOF'
用法: sync.sh [选项] [目标分支]

选项:
  -d, --daemon   常驻模式，每隔 SCHEDULE_MINUTES 分钟检测一次（config.sh 配置，默认30）
  -h, --help     显示本帮助

位置参数（可选）: 覆盖 config.sh 中的 TARGET_BRANCH，例如：sync.sh dev
EOF
    exit 0
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        -d|--daemon) DAEMON=1; shift ;;
        -h|--help)   usage ;;
        *)           TARGET="$1"; shift ;;
    esac
done

# ---------------- 单次检测核心 ----------------
run_once() {
    # 0) 基础校验（全部只读，出错即退出，绝不改动本地代码）
    if ! git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        errlog "${ROOT} 不是 Git 仓库，退出。"; return 1
    fi
    if ! git -C "$ROOT" remote get-url "$REMOTE_NAME" >/dev/null 2>&1; then
        errlog "找不到远程 '$REMOTE_NAME'（请 git remote -v 检查），退出。"; return 1
    fi

    # 1) 获取远端目标分支最新 commit（失败=网络/权限/分支不存在 → 容错退出）
    local remote_hash
    remote_hash="$(git ls-remote --exit-code "$REMOTE_NAME" "refs/heads/${TARGET}" 2>/dev/null | awk '{print $1}')"
    if [ -z "$remote_hash" ]; then
        errlog "无法获取远端 '${REMOTE_NAME}/${TARGET}'（网络异常/无权限/分支不存在），未改动本地代码。"
        return 1
    fi

    # 2) 与本地记录对比（首次运行则初始化记录，不执行拉取）
    local record cur
    record="$(record_read)"
    if [ -z "$record" ]; then
        cur="$(git -C "$ROOT" rev-parse --verify -q "refs/heads/${TARGET}" 2>/dev/null || true)"
        record_write "${cur:-$remote_hash}"
        xlog "首次运行：记录已初始化为 ${cur:-$remote_hash}，本轮不拉取。"
        return 0
    fi

    # 3) ① 无更新：直接退出，不做任何操作
    if [ "$remote_hash" = "$record" ]; then
        xlog "无更新：${REMOTE_NAME}/${TARGET} 仍为 $remote_hash，脚本退出。"
        return 0
    fi

    # 有更新：② fetch（容错，失败不破坏本地）
    xlog "检测到更新：记录=$record → 远端=$remote_hash，执行 fetch ..."
    local fetch_out
    fetch_out="$(git -C "$ROOT" fetch "$REMOTE_NAME" "$TARGET" 2>&1)" || {
        errlog "git fetch 失败（网络/权限/仓库连接）：$(printf '%s' "$fetch_out" | tail -n 3)"
        errlog "已退出，未改动本地代码。"
        return 1
    }

    # 4) 保护工作区：仅当本地当前分支 == 目标分支时才执行快进合并
    cur="$(git -C "$ROOT" rev-parse --abbrev-ref HEAD)"
    if [ "$cur" != "$TARGET" ]; then
        xlog "当前分支为 '${cur}'，目标为 '${TARGET}'：为不打扰你的工作区，不自动切换/覆盖。"
        xlog "已 fetch 完成；如需同步请手动执行：git checkout ${TARGET} && git merge --ff-only ${REMOTE_NAME}/${TARGET}。记录未更新。"
        return 0
    fi

    # 5) 快进合并（等价 git pull --ff-only）
    local merge_out
    merge_out="$(git -C "$ROOT" merge --ff-only "${REMOTE_NAME}/${TARGET}" 2>&1)" || {
        errlog "合并冲突/无法快进：已中止，未覆盖本地代码，记录未更新。"
        errlog "冲突输出（末 5 行）："
        printf '%s\n' "$merge_out" | tail -n 5 >&2
        printf '\n'
        print_prompt_b
        return 2
    }

    # 拉取成功：更新记录 + 打印场景A审查指令
    record_write "$remote_hash"
    xlog "同步成功：本地 '${TARGET}' 已更新至 $remote_hash（记录已更新）。"
    print_prompt_a
    return 0
}

# ---------------- 入口 ----------------
if [ "$DAEMON" -eq 1 ]; then
    xlog "daemon 模式启动：每 ${SCHEDULE_MINUTES} 分钟检测一次（Ctrl+C 退出）。"
    while :; do
        run_once
        sleep "$((SCHEDULE_MINUTES * 60))"
    done
else
    run_once
fi
