#!/usr/bin/env bash
# =============================================================================
# 文件：deploy/internal-test/scripts/lib/common.sh
# 作用：内部联调部署 · 公共库（日志 / 颜色 / 全局配置 / 前置校验 / 端口策略守卫）
# 用法：由其他脚本 `source` 引入，**不要单独执行**
#        source "$(dirname "$0")/lib/common.sh"
# =============================================================================

set -euo pipefail

# ------------------------------------------------------------------ 全局配置
# 可用环境变量覆盖（例如：APP_DIR=/srv/xiaojietong ./08-deploy.sh）
export APP_NAME="${APP_NAME:-xiaojietong}"
export APP_DIR="${APP_DIR:-/opt/${APP_NAME}}"          # 代码仓库根目录
export APP_USER="${APP_USER:-xjt}"                     # 运行服务的非 root 账号
export APP_PORT="${APP_PORT:-8080}"                    # ★ 后端固定监听端口
export SERVICE_NAME="${SERVICE_NAME:-xiaojietong-api}" # systemd 服务名
export GIT_REMOTE="${GIT_REMOTE:-github}"
export GIT_BRANCH="${GIT_BRANCH:-dev}"
export APP_REPO_URL="${APP_REPO_URL:-https://github.com/asuperj1/xiaojietong-project.git}"
export SHARED_DIR="${SHARED_DIR:-${APP_DIR}/shared}"   # 存放 server.env / 日志

export SERVER_ENV_FILE="${SHARED_DIR}/server.env"
export SYSTEMD_UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
# 健康检查完整路径：health 路由挂在 api_prefix(/api/v1) 下，**不是** /health
# 该路径已列入闸门豁免，无需令牌即可探活
export HEALTH_PATH="${HEALTH_PATH:-/api/v1/health}"

# 脚本自身定位（lib/ 的上一级 = scripts/）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export SCRIPT_DIR

# ------------------------------------------------------------------ 颜色/日志
if [[ -t 1 ]]; then
    C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YEL=$'\033[33m'
    C_BLU=$'\033[36m'; C_BLD=$'\033[1m'; C_RST=$'\033[0m'
else
    C_RED=''; C_GRN=''; C_YEL=''; C_BLU=''; C_BLD=''; C_RST=''
fi
export C_RED C_GRN C_YEL C_BLU C_BLD C_RST

log_info() { printf '%s[信息]%s %s\n' "$C_BLU" "$C_RST" "$*"; }
log_ok()   { printf '%s[成功]%s %s\n' "$C_GRN" "$C_RST" "$*"; }
log_warn() { printf '%s[警告]%s %s\n' "$C_YEL" "$C_RST" "$*" >&2; }
log_err()  { printf '%s[错误]%s %s\n' "$C_RED" "$C_RST" "$*" >&2; }
die()      { log_err "$*"; exit 1; }

step() {
    printf '\n%s%s──── %s ────%s\n' "$C_BLD" "$C_BLU" "$*" "$C_RST"
}

# ------------------------------------------------------------------ 前置校验
require_root() {
    if [[ "${EUID}" -ne 0 ]]; then
        die "本步骤需要 root 权限，请用 sudo 执行：sudo $0"
    fi
}

require_cmd() {
    local c
    for c in "$@"; do
        command -v "$c" >/dev/null 2>&1 || die "缺少命令：$c（请先执行 00-install-system-deps.sh）"
    done
}

# ------------------------------------------------------------------ ★端口策略守卫
# 硬性约束（团队规定，代码级兜底）：
#   - 后端固定 8080
#   - 禁止监听 80 / 443（本站不对外、备案未完成）
#   - 禁止业务占用 22(SSH) / 3306(MySQL) / 6379(Redis)
assert_port_policy() {
    local port="${1:-$APP_PORT}"
    case "$port" in
        8080) : ;;
        80|443) die "端口策略违规：禁止监听 ${port}（本站仅内网联调，不对外提供网站服务）" ;;
        22)     die "端口策略违规：22 为 SSH 管理端口，禁止业务占用" ;;
        3306)   die "端口策略违规：3306 为 MySQL 端口，禁止业务占用（数据库仅绑 127.0.0.1）" ;;
        6379)   die "端口策略违规：6379 为 Redis 端口，禁止业务占用" ;;
        *)      die "端口策略违规：本方案只允许 ${APP_PORT}，收到 ${port}" ;;
    esac
}

# 打印统一横幅，提醒当前阶段定位（每次执行都提示，防误操作）
banner_internal_only() {
    printf '\n%s╔══════════════════════════════════════════════════════════════════╗%s\n' "$C_YEL" "$C_RST"
    printf '%s║%s  校捷通 · 内部联调部署（公网 IP + 8080）                       %s║%s\n' "$C_YEL" "$C_BLD" "$C_YEL" "$C_RST"
    printf '%s║%s  ⚠️  本阶段【不对外提供网站服务】·【不使用域名】·【不使用 80/443】 %s║%s\n' "$C_YEL" "$C_RED" "$C_YEL" "$C_RST"
    printf '%s║%s  ⚠️  ICP 备案未完成期间，禁止任何形式的公网推广或站点发布        %s║%s\n' "$C_YEL" "$C_RED" "$C_YEL" "$C_RST"
    printf '%s╚══════════════════════════════════════════════════════════════════╝%s\n' "$C_YEL" "$C_RST"
}

# ------------------------------------------------------------------ .env 辅助
# 读取 server.env 中的某个键（不 source，避免执行任意代码）
env_get() {
    local key="$1" file="${2:-$SERVER_ENV_FILE}"
    [[ -f "$file" ]] || return 0
    sed -n "s/^[[:space:]]*${key}=//p" "$file" | tail -n 1
}

# ------------------------------------------------------------------ 服务辅助
svc_is_active() {
    systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null
}

svc_restart() {
    systemctl daemon-reload
    systemctl restart "$SERVICE_NAME"
}

# 等待后端 /health 就绪（带令牌，因为生产门禁会拦截匿名请求）
wait_for_health() {
    local token url i
    token="$(env_get XJT_ACCESS_TOKEN)"
    url="http://127.0.0.1:${APP_PORT}${HEALTH_PATH}"
    for i in $(seq 1 30); do
        if curl -fsS --max-time 2 ${token:+-H "X-Access-Token: ${token}"} "$url" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

# ------------------------------------------------------------------ 安全提示
print_security_group_reminder() {
    local ip="${1:-<你的公网IP>}"
    cat <<EOF

${C_RED}${C_BLD}⚠️  云服务器【安全组】必须同步配置（控制台操作，脚本无法代改）：${C_RST}
    ✅ 入方向放行：${C_BLD}${APP_PORT}/TCP${C_RST}（来源建议限制为团队 4 人固定出口 IP）
    ❌ 入方向拒绝/不开放：${C_BLD}80、443${C_RST}（本阶段不对外提供网站服务）
    ❌ 入方向拒绝/不开放：${C_BLD}3306、6379${C_RST}（数据库/缓存严禁暴露公网）
    ✅ 入方向放行：22/TCP（仅运维；建议同时限制来源 IP）

${C_YEL}访问方式（仅团队内部）：  http://${ip}:${APP_PORT}/api/v1${C_RST}
EOF
}

export -f log_info log_ok log_warn log_err die step \
           require_root require_cmd assert_port_policy banner_internal_only \
           env_get svc_is_active svc_restart wait_for_health print_security_group_reminder
