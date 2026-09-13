#!/usr/bin/env bash
# =============================================================================
# 文件：deploy/internal-test/scripts/09-restart.sh
# 作用：重启后端服务并做就绪检查（不拉代码、不编译）
# 执行：sudo bash deploy/internal-test/scripts/09-restart.sh
# =============================================================================

set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

require_root
banner_internal_only
assert_port_policy "$APP_PORT"

[[ -f "$SYSTEMD_UNIT" ]] || die "服务未安装（请先执行 06-install-systemd.sh）"
[[ -f "$SERVER_ENV_FILE" ]] || die "缺少 ${SERVER_ENV_FILE}（请先执行 04-configure-env.sh）"

step "1/2 重启 ${SERVICE_NAME}"
svc_restart
sleep 2
svc_is_active || {
    journalctl -u "$SERVICE_NAME" -n 40 --no-pager | sed 's/^/  /'
    die "重启后服务未处于 active 状态"
}
log_ok "服务已重启"

step "2/2 就绪检查"
if wait_for_health; then
    log_ok "健康检查通过（HTTP $(curl -sS -o /dev/null -w '%{http_code}' -m 5 \
        "http://127.0.0.1:${APP_PORT}${HEALTH_PATH}")）"
else
    log_err "健康检查超时，最近 40 行日志："
    journalctl -u "$SERVICE_NAME" -n 40 --no-pager | sed 's/^/  /'
    die "请按 docs/05-运维排障与验收清单.md 排障"
fi
