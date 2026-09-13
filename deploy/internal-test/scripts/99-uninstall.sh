#!/usr/bin/env bash
# =============================================================================
# 文件：deploy/internal-test/scripts/99-uninstall.sh
# 作用：卸载服务与防火墙规则（**不删代码、不删数据库**，避免误伤）
# 执行：sudo bash deploy/internal-test/scripts/99-uninstall.sh [--purge]
#   --purge  额外删除代码目录与 venv（数据库仍需手动处理）
# =============================================================================

set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

require_root
banner_internal_only

PURGE=0
[[ "${1:-}" == "--purge" ]] && PURGE=1

step "1/3 停止并禁用服务"
if [[ -f "$SYSTEMD_UNIT" ]]; then
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    rm -f "$SYSTEMD_UNIT"
    systemctl daemon-reload
    log_ok "已移除 ${SYSTEMD_UNIT}"
else
    log_info "服务未安装，跳过"
fi

step "2/3 回收防火墙中本方案添加的规则"
if command -v ufw >/dev/null 2>&1 && ufw status >/dev/null 2>&1 && ufw status | grep -qi active; then
    for p in "${APP_PORT}" 80 443 3306 6379; do
        ufw delete allow "${p}/tcp" >/dev/null 2>&1 || true
        ufw delete deny  "${p}/tcp" >/dev/null 2>&1 || true
    done
    log_ok "已清理 ufw 规则（SSH 规则保留，避免锁死）"
elif systemctl is-active --quiet firewalld 2>/dev/null; then
    read -r -p "是否移除 firewalld 中的 ${APP_PORT} 与 80/443/3306/6379 规则？(yes) " a
    if [[ "$a" == "yes" ]]; then
        firewall-cmd --permanent --remove-port="${APP_PORT}/tcp" >/dev/null 2>&1 || true
        for bad in 80 443 3306 6379; do
            firewall-cmd --permanent \
                --remove-rich-rule="rule family=ipv4 port port=${bad} protocol=tcp reject" >/dev/null 2>&1 || true
        done
        firewall-cmd --reload >/dev/null
        log_ok "firewalld 规则已清理"
    fi
elif command -v iptables >/dev/null 2>&1; then
    for p in "${APP_PORT}" 80 443 3306 6379; do
        iptables -D INPUT -p tcp --dport "$p" -j ACCEPT 2>/dev/null || true
        iptables -D INPUT -p tcp --dport "$p" -j REJECT 2>/dev/null || true
    done
    log_ok "iptables 规则已清理"
else
    log_info "无主机防火墙，跳过"
fi

step "3/3 可选清理"
if [[ "$PURGE" -eq 1 ]]; then
    read -r -p "将删除 ${APP_DIR}（代码/venv/密钥文件），是否继续？(yes) " a
    if [[ "$a" == "yes" ]]; then
        rm -rf "$APP_DIR"
        log_ok "已删除 ${APP_DIR}"
    else
        log_info "已取消删除代码目录"
    fi
else
    log_info "保留 ${APP_DIR}（含 server.env 密钥与已编译产物）。如需彻底删除：--purge"
fi

cat <<EOF

$(log_ok 卸载流程结束)

${C_YEL}⚠️ 仍需你手动确认的事项：${C_RST}
  1) 云控制台【安全组】：撤销 8080 的入方向放行（若不再需要）；
  2) 数据库：如需删除数据，执行
       mysql -e "DROP DATABASE xiaojietong;"
  3) 备份：${SHARED_DIR}/server.env 内含密钥，删除前请确认无人仍在使用。
EOF
