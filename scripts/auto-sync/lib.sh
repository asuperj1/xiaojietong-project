#!/usr/bin/env bash
# =============================================================================
# 校捷通 · 主仓库自动同步 —— 函数库（被 sync.sh source）
# 文件路径：scripts/auto-sync/lib.sh
# 依赖：git + 系统自带工具（date/cat/awk/tee）——不引入第三方库。
# =============================================================================

# 日志文件绝对路径（LOG_FILE 在 config.sh 中定义）
_log_path() {
    printf '%s/%s\n' "${ROOT:?}" "${LOG_FILE:-.git/auto_sync.log}"
}

# 记录文件绝对路径（RECORD_FILE 在 config.sh 中定义）
_record_path() {
    printf '%s/%s\n' "${ROOT:?}" "${RECORD_FILE:-.git/auto_sync_head.txt}"
}

# 输出普通日志：终端 + 追加日志文件
xlog() {
    local ts
    ts="$(date '+%Y-%m-%d %H:%M:%S')"
    printf '%s  %s\n' "$ts" "$*" | tee -a "$(_log_path)"
}

# 输出错误日志（stderr）
errlog() {
    local ts
    ts="$(date '+%m-%d %H:%M:%S')"
    printf '%s  [错误] %s\n' "$ts" "$*" >&2
}

# 读取上次记录的 commit（不存在或为空则输出空串）
record_read() {
    local f
    f="$(_record_path)"
    if [ -s "$f" ]; then cat "$f"; fi
}

# 写入记录文件
record_write() {
    printf '%s\n' "$1" > "$(_record_path)"
}

# 场景A：拉取成功 → 输出 AI 代码审查指令
print_prompt_a() {
    if [ -f "${SELF_DIR}/${PROMPT_A_FILE:-prompt_a.txt}" ]; then
        cat "${SELF_DIR}/${PROMPT_A_FILE:-prompt_a.txt}"
    else
        errlog "缺少提示词文件：${SELF_DIR}/${PROMPT_A_FILE}"
    fi
}

# 场景B：合并冲突 → 输出 AI 冲突处理指令
print_prompt_b() {
    if [ -f "${SELF_DIR}/${PROMPT_B_FILE:-prompt_b.txt}" ]; then
        cat "${SELF_DIR}/${PROMPT_B_FILE:-prompt_b.txt}"
    else
        errlog "缺少提示词文件：${SELF_DIR}/${PROMPT_B_FILE}"
    fi
}
