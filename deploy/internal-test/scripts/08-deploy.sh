#!/usr/bin/env bash
# =============================================================================
# 文件：deploy/internal-test/scripts/08-deploy.sh
# 作用：★一键部署/更新 —— 拉取 GitHub 最新代码 → 编译 C++ → 装依赖 → 配环境 → 重启服务
# 执行：sudo bash deploy/internal-test/scripts/08-deploy.sh [选项]
# 选项：
#   --no-pull       跳过拉取代码（仅重新编译与重启）
#   --no-build      跳过 C++ 编译（代码未改 C++ 层时可省时间）
#   --restart-only  只重启服务（等价于 09-restart.sh）
#   --branch NAME   指定分支（默认 dev）
# 示例（日常更新）：sudo bash deploy/internal-test/scripts/08-deploy.sh
# =============================================================================

set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

require_root
banner_internal_only
assert_port_policy "$APP_PORT"

DO_PULL=1
DO_BUILD=1
RESTART_ONLY=0
BRANCH="$GIT_BRANCH"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-pull)       DO_PULL=0 ;;
        --no-build)      DO_BUILD=0 ;;
        --restart-only)  RESTART_ONLY=1 ;;
        --branch)        BRANCH="${2:?--branch 需要参数}"; shift ;;
        -h|--help)       sed -n '2,16p' "$0"; exit 0 ;;
        *)               die "未知参数：$1（--help 查看用法）" ;;
    esac
    shift
done

SCRIPT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
START_TS="$(date +%s)"

run_step() {
    local name="$1"; shift
    local t0 t1
    t0="$(date +%s)"
    step "$name"
    if "$@"; then
        t1="$(date +%s)"
        log_ok "${name} 完成（$((t1 - t0))s）"
    else
        die "${name} 失败（详见上方输出）"
    fi
}

if [[ "$RESTART_ONLY" -eq 1 ]]; then
    run_step "重启服务" bash "${SCRIPT_PATH}/09-restart.sh"
    exit 0
fi

# ---------------- 0. 前置检查 ----------------
step "0/6 前置检查"
require_cmd git cmake g++ python3 mysql curl
[[ -d "${APP_DIR}/app" ]] || log_warn "代码目录不存在，将由第 1 步创建"
log_info "APP_DIR=${APP_DIR}  APP_USER=${APP_USER}  PORT=${APP_PORT}  BRANCH=${BRANCH}"

# ---------------- 1. 拉取代码 ----------------
if [[ "$DO_PULL" -eq 1 ]]; then
    run_step "1/6 拉取 GitHub 最新代码" bash "${SCRIPT_PATH}/01-pull-code.sh" "$BRANCH"
else
    step "1/6 拉取代码 —— 已跳过（--no-pull）"
fi

# ---------------- 2. Python 环境 ----------------
run_step "2/6 安装/更新 Python 依赖" bash "${SCRIPT_PATH}/03-setup-python-env.sh"

# ---------------- 3. 编译 C++ 数据层 ----------------
if [[ "$DO_BUILD" -eq 1 ]]; then
    run_step "3/6 编译 C++ 数据层（jt_db）" bash "${SCRIPT_PATH}/02-build-cpp.sh"
else
    step "3/6 编译 C++ —— 已跳过（--no-build）"
fi

# ---------------- 4. 环境变量 ----------------
run_step "4/6 生成/校验 server.env" bash "${SCRIPT_PATH}/04-configure-env.sh"

# ---------------- 5. systemd ----------------
run_step "5/6 安装并启动 systemd 服务" bash "${SCRIPT_PATH}/06-install-systemd.sh"

# ---------------- 6. 健康检查 ----------------
step "6/6 健康检查"
if wait_for_health; then
    TOKEN="$(env_get XJT_ACCESS_TOKEN)"
    CODE="$(curl -sS -o /dev/null -w '%{http_code}' -m 5 \
        "http://127.0.0.1:${APP_PORT}${HEALTH_PATH}" || echo 000)"
    log_ok "${HEALTH_PATH} 返回 HTTP ${CODE}"

    ANON_CODE="$(curl -sS -o /dev/null -w '%{http_code}' -m 5 \
        "http://127.0.0.1:${APP_PORT}/api/v1/secondhand/items" || echo 000)"
    if [[ "$ANON_CODE" == "403" ]]; then
        log_ok "匿名访问业务接口被访问闸门拦截（HTTP 403）—— 防护生效"
    else
        log_warn "匿名访问业务接口返回 HTTP ${ANON_CODE}（预期 403）—— 请检查 XJT_ACCESS_TOKEN 是否已生效"
    fi
else
    log_err "健康检查超时，最近 40 行日志："
    journalctl -u "$SERVICE_NAME" -n 40 --no-pager | sed 's/^/  /'
    die "部署完成但服务未就绪，请按上方日志排障（见 docs/05-运维排障与验收清单.md）"
fi

END_TS="$(date +%s)"
SERVER_IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || echo '<你的公网IP>')"
TOKEN="$(env_get XJT_ACCESS_TOKEN)"

cat <<EOF

${C_GRN}${C_BLD}════════════════════ 部署成功（耗时 $((END_TS - START_TS))s）════════════════════${C_RST}
   代码分支 : ${BRANCH} @ $(git -C "${APP_DIR}/app" log -1 --format='%h')
   服务状态 : $(systemctl is-active "$SERVICE_NAME")
   监听端口 : ${APP_PORT}（已校验无 80/443）
   访问地址 : ${C_BLD}http://${SERVER_IP}:${APP_PORT}/api/v1${C_RST}
   访问令牌 : ${TOKEN}
   调用示例 : curl -H "X-Access-Token: ${TOKEN}" http://${SERVER_IP}:${APP_PORT}/api/v1/secondhand/items
   查看日志 : journalctl -u ${SERVICE_NAME} -f
${C_GRN}${C_BLD}════════════════════════════════════════════════════════════════════${C_RST}
EOF

print_security_group_reminder "$SERVER_IP"
