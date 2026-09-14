#!/usr/bin/env bash
# =============================================================================
# 文件：deploy/internal-test/scripts/10-status.sh
# 作用：一键体检 —— 服务状态 / 端口占用 / 访问闸门 / 数据库 / 防火墙 / 日志
# 执行：bash deploy/internal-test/scripts/10-status.sh     （无需 root）
# =============================================================================

set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

banner_internal_only

TOKEN="$(env_get XJT_ACCESS_TOKEN)"
SERVER_IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || echo '<你的公网IP>')"
BASE="http://127.0.0.1:${APP_PORT}"

hr() { printf '%s%s%s\n' "$C_BLU" "────────────────────────────────────────────────────────────" "$C_RST"; }

step "1/6 systemd 服务"
if systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICE_NAME}\.service"; then
    printf '  状态       : %s\n' "$(systemctl is-active "$SERVICE_NAME" 2>/dev/null || echo unknown)"
    printf '  开机自启   : %s\n' "$(systemctl is-enabled "$SERVICE_NAME" 2>/dev/null || echo unknown)"
    printf '  运行时长   : %s\n' "$(systemctl show -p ActiveEnterTimestamp --value "$SERVICE_NAME" 2>/dev/null || echo -)"
    printf '  主进程 PID : %s\n' "$(systemctl show -p MainPID --value "$SERVICE_NAME" 2>/dev/null || echo -)"
    printf '  内存占用   : %s\n' "$(systemctl show -p MemoryCurrent --value "$SERVICE_NAME" 2>/dev/null | awk '{printf "%.1f MB", $1/1048576}' || echo -)"
else
    log_warn "服务 ${SERVICE_NAME} 未安装"
fi

step "2/6 端口占用（校验只有 ${APP_PORT}，且无 80/443）"
if command -v ss >/dev/null 2>&1; then
    ss -lntp 2>/dev/null | awk 'NR==1 || /:(80|443|3306|6379|'"${APP_PORT}"')\s*$|:'"${APP_PORT}"'[[:space:]]/' | sed 's/^/  /' || true
else
    netstat -lntp 2>/dev/null | grep -E ":(80|443|3306|6379|${APP_PORT})\b" | sed 's/^/  /' || true
fi
for bad in 80 443; do
    if command -v ss >/dev/null 2>&1 && ss -lnt 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${bad}$"; then
        log_warn "有进程监听 ${bad} —— 本方案禁止，请立即排查"
    fi
done
if command -v ss >/dev/null 2>&1 && ss -lnt 2>/dev/null | awk '{print $4}' | grep -qE "[:.]3306$"; then
    ADDR="$(ss -lnt 2>/dev/null | awk '$4 ~ /:3306$/ {print $4}' | head -n1)"
    if [[ "$ADDR" == 127.0.0.1:3306 || "$ADDR" == "[::1]:3306" ]]; then
        printf '  %sMySQL 3306 仅绑回环（正确）%s\n' "$C_GRN" "$C_RST"
    else
        log_warn "MySQL 监听在 ${ADDR} —— 公网可达风险！请改 bind-address=127.0.0.1"
    fi
fi

step "3/6 健康检查与访问闸门"
HP="${BASE}${HEALTH_PATH}"
printf '  匿名   %-22s: HTTP %s（应 200 = 豁免放行）\n' "$HEALTH_PATH" \
    "$(curl -sS -o /dev/null -w '%{http_code}' -m 5 "$HP" 2>/dev/null || echo 000)"
printf '  匿名   /api/v1/health/detail : HTTP %s（应 403 = 未豁免）\n' \
    "$(curl -sS -o /dev/null -w '%{http_code}' -m 5 "${BASE}/api/v1/health/detail" 2>/dev/null || echo 000)"
printf '  匿名   业务接口             : HTTP %s（应 403 = 闸门生效）\n' \
    "$(curl -sS -o /dev/null -w '%{http_code}' -m 5 "${BASE}/api/v1/secondhand/items" 2>/dev/null || echo 000)"
printf '  带令牌 业务接口             : HTTP %s（应 401/2001 = 已过闸门）\n' \
    "$(curl -sS -o /dev/null -w '%{http_code}' -m 5 -H "X-Access-Token: ${TOKEN}" "${BASE}/api/v1/secondhand/items" 2>/dev/null || echo 000)"
[ -n "$TOKEN" ] || log_warn "server.env 中 XJT_ACCESS_TOKEN 为空 —— 闸门处于关闭状态（仅限本机开发！）"

step "4/6 数据库"
DB_HOST="$(env_get XJT_DB_HOST)"; DB_PORT="$(env_get XJT_DB_PORT)"; DB_NAME="$(env_get XJT_DB_NAME)"
printf '  配置       : %s:%s/%s\n' "${DB_HOST:-127.0.0.1}" "${DB_PORT:-3306}" "${DB_NAME:-xiaojietong}"
if command -v mysql >/dev/null 2>&1; then
    CNF="$(mktemp)"; chmod 600 "$CNF"
    cat > "$CNF" <<EOF
[client]
host=${DB_HOST:-127.0.0.1}
port=${DB_PORT:-3306}
user=$(env_get XJT_DB_USER)
password=$(env_get XJT_DB_PASSWORD)
EOF
    T="$(mysql --defaults-extra-file="$CNF" -N -B -e \
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='${DB_NAME:-xiaojietong}'" 2>/dev/null || echo ERR)"
    rm -f "$CNF"
    [[ "$T" == "ERR" ]] && log_warn "数据库连接失败（检查凭据/服务）" || printf '  表数量     : %s\n' "$T"
else
    log_info "未安装 mysql 客户端，跳过连通性检查"
fi

step "5/6 防火墙"
if command -v ufw >/dev/null 2>&1 && ufw status >/dev/null 2>&1 && ufw status | grep -qi active; then
    ufw status | sed 's/^/  /'
elif systemctl is-active --quiet firewalld 2>/dev/null; then
    firewall-cmd --list-all 2>/dev/null | grep -E 'ports|rich rules' | sed 's/^/  /'
elif command -v iptables >/dev/null 2>&1; then
    iptables -L INPUT -n 2>/dev/null | grep -E "dpt:(80|443|3306|6379|${APP_PORT})" | sed 's/^/  /' || echo "  （无相关规则）"
else
    log_warn "未检测到主机防火墙 —— 请依赖云安全组"
fi

step "6/6 最近日志（20 行）"
journalctl -u "$SERVICE_NAME" -n 20 --no-pager 2>/dev/null | sed 's/^/  /' || log_info "无 journalctl 记录"

hr
cat <<EOF
  ${C_BLD}访问地址${C_RST} : http://${SERVER_IP}:${APP_PORT}/api/v1
  ${C_BLD}访问令牌${C_RST} : ${TOKEN:-<未设置>}
  ${C_BLD}实时日志${C_RST} : journalctl -u ${SERVICE_NAME} -f
EOF
print_security_group_reminder "$SERVER_IP"
