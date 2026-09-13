#!/usr/bin/env bash
# =============================================================================
# 文件：deploy/internal-test/scripts/06-install-systemd.sh
# 作用：安装并启用 systemd 服务（固定监听 8080）
# 执行：sudo bash deploy/internal-test/scripts/06-install-systemd.sh
# 说明：单元来源 deploy/internal-test/systemd/xiaojietong-api.service（占位符替换后落盘）
# =============================================================================

set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

require_root
require_cmd systemctl
banner_internal_only

assert_port_policy "$APP_PORT"     # ★ 端口策略守卫：非 8080 直接拒绝

REPO_DIR="${APP_DIR}/app"
UNIT_SRC="${REPO_DIR}/deploy/internal-test/systemd/xiaojietong-api.service"
SERVICE_FILE="${SERVICE_NAME}.service"

[[ -f "$UNIT_SRC" ]] || die "未找到单元模板：${UNIT_SRC}（请先执行 01-pull-code.sh）"
[[ -x "${REPO_DIR}/backend/.venv/bin/python" ]] \
    || die "venv 不存在（请先执行 03-setup-python-env.sh）"
[[ -f "$SERVER_ENV_FILE" ]] \
    || die "环境变量文件不存在（请先执行 04-configure-env.sh）"

step "1/4 渲染单元文件（替换占位符）"
TMP_UNIT="$(mktemp)"
sed -e "s|__APP_DIR__|${APP_DIR}|g" \
    -e "s|__APP_USER__|${APP_USER}|g" \
    -e "s|__APP_PORT__|${APP_PORT}|g" \
    "$UNIT_SRC" > "$TMP_UNIT"

# 双保险：渲染结果里绝不允许出现 80/443 监听
if grep -nE '^[^#]*--port[[:space:]]+(80|443)\b' "$TMP_UNIT"; then
    rm -f "$TMP_UNIT"
    die "单元渲染结果包含 80/443 监听，已中止（违反端口策略）"
fi
log_ok "渲染完成，监听端口 = ${APP_PORT}"

step "2/4 安装到 /etc/systemd/system/"
install -m 644 -o root -g root "$TMP_UNIT" "$SYSTEMD_UNIT"
rm -f "$TMP_UNIT"
log_info "已安装：${SYSTEMD_UNIT}"

step "3/4 启用并启动服务"
systemctl daemon-reload
systemctl enable "$SERVICE_FILE" >/dev/null 2>&1
systemctl restart "$SERVICE_FILE"

sleep 2
if svc_is_active; then
    log_ok "服务已启动（${SERVICE_NAME}）"
else
    log_err "服务启动失败，最近日志："
    journalctl -u "$SERVICE_NAME" -n 40 --no-pager | sed 's/^/  /'
    die "请修复后重跑本脚本"
fi

step "4/4 校验监听端口（必须只有 ${APP_PORT}）"
# ss 优先；无 ss 时回退 netstat
PORTS=""
if command -v ss >/dev/null 2>&1; then
    PORTS="$(ss -lntp 2>/dev/null | awk -v p=":${APP_PORT}" '$4 ~ p {print $4}' | sort -u)"
else
    PORTS="$(netstat -lntp 2>/dev/null | awk -v p=":${APP_PORT}" '$4 ~ p {print $4}' | sort -u)"
fi
if [[ -z "$PORTS" ]]; then
    log_warn "未在监听表中看到 ${APP_PORT}，请检查：journalctl -u ${SERVICE_NAME} -n 50"
else
    log_ok "监听中：$(echo "$PORTS" | tr '\n' ' ')"
fi

# 显式确认没有监听 80/443
for bad in 80 443; do
    if ss -lnt 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${bad}$"; then
        log_warn "检测到有进程监听 ${bad} —— 本方案禁止，请排查：ss -lntp | grep ':${bad}'"
    fi
done

print_security_group_reminder "$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || echo '<你的公网IP>')"
log_ok "systemd 服务安装完成"
