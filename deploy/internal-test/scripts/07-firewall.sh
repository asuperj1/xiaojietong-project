#!/usr/bin/env bash
# =============================================================================
# 文件：deploy/internal-test/scripts/07-firewall.sh
# 作用：配置主机防火墙 —— 放行 8080，**显式拒绝 80/443**，拒绝 3306/6379
# 执行：sudo bash deploy/internal-test/scripts/07-firewall.sh
# 兼容：ufw / firewalld / 裸 iptables（自动识别，无需装 Nginx）
# ⚠️ 本脚本**不生成任何 80/443 的放行规则**，只生成拒绝规则
# ⚠️ 云服务器【安全组】需另行在控制台配置，脚本无法代改（见文末提醒）
# =============================================================================

set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

require_root
banner_internal_only
assert_port_policy "$APP_PORT"

# 团队 4 人固定出口 IP（可选，强烈建议填 —— 把 8080 收敛到已知来源）
# 用法：TEAM_IPS="1.2.3.4,5.6.7.8" sudo bash 07-firewall.sh
TEAM_IPS="${TEAM_IPS:-}"
SSH_PORT="${SSH_PORT:-22}"

step "0/4 安全前置：先确保 SSH 不被锁死"
log_warn "接下来会启用防火墙。已优先放行 SSH(${SSH_PORT})，请勿在未放行前断开当前会话。"

step "1/4 识别防火墙后端"
FW=""
if command -v ufw >/dev/null 2>&1 && ufw status >/dev/null 2>&1; then
    FW=ufw
elif command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active --quiet firewalld; then
    FW=firewalld
elif command -v iptables >/dev/null 2>&1; then
    FW=iptables
else
    log_warn "未检测到 ufw/firewalld/iptables —— 仅依赖云安全组，请务必按文末清单配置"
    print_security_group_reminder "$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || echo '<你的公网IP>')"
    exit 0
fi
log_info "防火墙后端：${FW}"

step "2/4 放行 SSH 与 ${APP_PORT}，显式拒绝 80/443/3306/6379"
case "$FW" in
    ufw)
        ufw --force reset >/dev/null 2>&1 || true
        ufw default deny incoming  >/dev/null
        ufw default allow outgoing >/dev/null

        ufw allow "${SSH_PORT}/tcp" comment 'SSH 运维' >/dev/null
        if [[ -n "$TEAM_IPS" ]]; then
            IFS=',' read -ra IPS <<< "$TEAM_IPS"
            for ip in "${IPS[@]}"; do
                ip="$(echo "$ip" | tr -d ' ')"
                [[ -n "$ip" ]] && ufw allow from "$ip" to any port "${APP_PORT}" proto tcp \
                    comment '校捷通联调(团队IP)' >/dev/null
                log_info "仅放行来源 ${ip} → ${APP_PORT}"
            done
        else
            ufw allow "${APP_PORT}/tcp" comment '校捷通联调(IP:8080)' >/dev/null
            log_warn "未设置 TEAM_IPS，${APP_PORT} 对全网开放（建议改为仅团队 IP）"
        fi

        # ★ 显式拒绝 80/443（即使有服务误绑，外部也进不来）
        ufw deny 80/tcp  comment '禁止：不对外提供网站服务' >/dev/null
        ufw deny 443/tcp comment '禁止：本阶段不使用 HTTPS' >/dev/null
        ufw deny 3306/tcp comment '禁止：数据库不可公网访问' >/dev/null
        ufw deny 6379/tcp comment '禁止：缓存不可公网访问' >/dev/null

        ufw --force enable >/dev/null
        log_ok "ufw 规则已生效"
        ;;

    firewalld)
        firewall-cmd --permanent --add-port="${SSH_PORT}/tcp" >/dev/null
        if [[ -n "$TEAM_IPS" ]]; then
            IFS=',' read -ra IPS <<< "$TEAM_IPS"
            for ip in "${IPS[@]}"; do
                ip="$(echo "$ip" | tr -d ' ')"
                [[ -n "$ip" ]] && firewall-cmd --permanent \
                    --add-rich-rule="rule family=ipv4 source address=${ip} port port=${APP_PORT} protocol=tcp accept" >/dev/null
            done
        else
            firewall-cmd --permanent --add-port="${APP_PORT}/tcp" >/dev/null
            log_warn "未设置 TEAM_IPS，${APP_PORT} 对全网开放"
        fi
        for bad in 80 443 3306 6379; do
            firewall-cmd --permanent \
                --add-rich-rule="rule family=ipv4 port port=${bad} protocol=tcp reject" >/dev/null
        done
        firewall-cmd --reload >/dev/null
        log_ok "firewalld 规则已生效"
        ;;

    iptables)
        # 幂等：先清掉本脚本可能重复添加的规则
        iptables -C INPUT -p tcp --dport "${APP_PORT}" -j ACCEPT 2>/dev/null \
            && iptables -D INPUT -p tcp --dport "${APP_PORT}" -j ACCEPT || true
        for bad in 80 443 3306 6379; do
            iptables -C INPUT -p tcp --dport "$bad" -j REJECT 2>/dev/null \
                && iptables -D INPUT -p tcp --dport "$bad" -j REJECT || true
        done

        iptables -A INPUT -p tcp --dport "${SSH_PORT}" -j ACCEPT
        if [[ -n "$TEAM_IPS" ]]; then
            IFS=',' read -ra IPS <<< "$TEAM_IPS"
            for ip in "${IPS[@]}"; do
                ip="$(echo "$ip" | tr -d ' ')"
                [[ -n "$ip" ]] && iptables -A INPUT -p tcp -s "$ip" --dport "${APP_PORT}" -j ACCEPT
            done
        else
            iptables -A INPUT -p tcp --dport "${APP_PORT}" -j ACCEPT
            log_warn "未设置 TEAM_IPS，${APP_PORT} 对全网开放"
        fi
        for bad in 80 443 3306 6379; do
            iptables -A INPUT -p tcp --dport "$bad" -j REJECT --reject-with tcp-reset
        done
        log_ok "iptables 规则已生效（注：重启后可能丢失，建议安装 iptables-persistent）"
        if command -v netfilter-persistent >/dev/null 2>&1; then
            netfilter-persistent save >/dev/null 2>&1 && log_info "已持久化 iptables 规则"
        fi
        ;;
esac

step "3/4 规则回显"
case "$FW" in
    ufw)      ufw status numbered | sed 's/^/  /' ;;
    firewalld) firewall-cmd --list-all | sed 's/^/  /' ;;
    iptables) iptables -L INPUT -n --line-numbers | sed 's/^/  /' ;;
esac

step "4/4 本机验证"
log_info "本机探测（应成功）：curl -sS -o /dev/null -w '%{http_code}\\n' http://127.0.0.1:${APP_PORT}/health"
if curl -sS -o /dev/null -m 3 "http://127.0.0.1:${APP_PORT}/health" 2>/dev/null; then
    log_ok "127.0.0.1:${APP_PORT} 可达"
else
    log_warn "127.0.0.1:${APP_PORT} 暂不可达 —— 服务可能尚未启动（属正常，先跑 06/08 脚本）"
fi

print_security_group_reminder "$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || echo '<你的公网IP>')"

cat <<EOF

${C_YEL}ℹ️ 关于 80/443 的说明（合规要求，勿改）：${C_RST}
   本阶段 ICP 备案未完成，服务器**不得对外提供网站服务**。
   因此本脚本只做「拒绝」动作，不产生任何 80/443 放行规则，
   也**未安装任何 Web 服务器（Nginx/Apache）**。
   备案通过并拿到证书后，再由负责人单独立项改造，不要在本次联调中私自放开。
EOF

log_ok "防火墙配置完成"
