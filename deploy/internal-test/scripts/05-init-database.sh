#!/usr/bin/env bash
# =============================================================================
# 文件：deploy/internal-test/scripts/05-init-database.sh
# 作用：创建库 xiaojietong 并按顺序导入 db/sql/ 下全部建表与种子脚本
# 执行：sudo bash deploy/internal-test/scripts/05-init-database.sh [--yes]
#       不带 --yes 时，若库中已有表会要求二次确认（导入可能覆盖数据）
# ⚠️ MySQL 仅监听 127.0.0.1；本脚本不会、也不能把 3306 暴露到公网
# =============================================================================

set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

require_root
require_cmd mysql
banner_internal_only

REPO_DIR="${APP_DIR}/app"
SQL_DIR="${REPO_DIR}/db/sql"
ASSUME_YES=0
[[ "${1:-}" == "--yes" ]] && ASSUME_YES=1

[[ -d "$SQL_DIR" ]] || die "未找到 ${SQL_DIR}（请先执行 01-pull-code.sh）"

DB_HOST="$(env_get XJT_DB_HOST)"; DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="$(env_get XJT_DB_PORT)"; DB_PORT="${DB_PORT:-3306}"
DB_USER="$(env_get XJT_DB_USER)"; DB_USER="${DB_USER:-root}"
DB_NAME="$(env_get XJT_DB_NAME)"; DB_NAME="${DB_NAME:-xiaojietong}"
DB_PASSWORD="$(env_get XJT_DB_PASSWORD)"

[[ -n "$DB_PASSWORD" && "$DB_PASSWORD" != "<请填写MySQL密码>" ]] \
    || die "未在 ${SERVER_ENV_FILE} 读到有效 XJT_DB_PASSWORD，请先执行 04-configure-env.sh"

# 避免密码出现在 ps / 命令历史：用临时 my.cnf
MYSQL_CNF="$(mktemp)"
chmod 600 "$MYSQL_CNF"
trap 'rm -f "$MYSQL_CNF"' EXIT
cat > "$MYSQL_CNF" <<EOF
[client]
host=${DB_HOST}
port=${DB_PORT}
user=${DB_USER}
password=${DB_PASSWORD}
default-character-set=utf8mb4
EOF
MYSQL="mysql --defaults-extra-file=${MYSQL_CNF}"

step "1/3 探测 MySQL 连通性"
if ! $MYSQL -e "SELECT 1" >/dev/null 2>&1; then
    die "无法连接 MySQL（${DB_HOST}:${DB_PORT}, 用户 ${DB_USER}）。请检查服务是否启动、密码是否正确。"
fi
log_ok "MySQL 连通（${DB_HOST}:${DB_PORT}）"

step "2/3 确认是否已初始化"
EXISTING_TABLES="$($MYSQL -N -B -e "
    SELECT COUNT(*) FROM information_schema.tables
    WHERE table_schema='${DB_NAME}'" 2>/dev/null || echo 0)"
if [[ "${EXISTING_TABLES:-0}" -gt 0 ]]; then
    log_warn "库 ${DB_NAME} 已存在 ${EXISTING_TABLES} 张表"
    if [[ "$ASSUME_YES" -ne 1 ]]; then
        read -r -p "继续导入可能覆盖现有数据，是否继续？(输入 yes 确认) " ans
        [[ "$ans" == "yes" ]] || die "已取消"
    fi
fi

step "3/3 按顺序导入 SQL"
mapfile -t FILES < <(find "$SQL_DIR" -maxdepth 1 -name '*.sql' | sort)
[[ "${#FILES[@]}" -gt 0 ]] || die "${SQL_DIR} 下没有 .sql 文件"

for f in "${FILES[@]}"; do
    name="$(basename "$f")"
    printf '  → %-32s' "$name"
    if $MYSQL < "$f" >/tmp/xjt_sql_out.log 2>&1; then
        printf '%sOK%s\n' "$C_GRN" "$C_RST"
    else
        printf '%s失败%s\n' "$C_RED" "$C_RST"
        sed 's/^/      /' /tmp/xjt_sql_out.log | tail -n 8
        rm -f /tmp/xjt_sql_out.log
        die "导入 ${name} 失败，请检查上方错误"
    fi
done
rm -f /tmp/xjt_sql_out.log

TOTAL="$($MYSQL -N -B -e "
    SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='${DB_NAME}'")"
log_ok "导入完成：${DB_NAME} 共 ${TOTAL} 张表"

# 完整性自检（与 docs/architecture.md 记录一致：45 张表）
if [[ "$TOTAL" -ne 45 ]]; then
    log_warn "表数 ${TOTAL} ≠ 预期 45 —— 请核对 db/sql/ 是否完整（或后续版本已加表）"
fi

$MYSQL -e "
    SELECT 'knowledge_doc' AS 表, COUNT(*) AS 行数 FROM ${DB_NAME}.knowledge_doc
    UNION ALL SELECT 'poi',            COUNT(*) FROM ${DB_NAME}.poi
    UNION ALL SELECT 'campus_notice',  COUNT(*) FROM ${DB_NAME}.campus_notice
    UNION ALL SELECT 'user',           COUNT(*) FROM ${DB_NAME}.user
" 2>/dev/null | sed 's/^/  /' || true

cat <<EOF

$(log_ok 数据库初始化完成)

${C_YEL}⚠️ 生产/公网环境提醒：${C_RST}
   1) 请执行  mysql_secure_installation  删除匿名账号、禁止 root 远程登录；
   2) 确认 bind-address=127.0.0.1（00-install-system-deps.sh 已处理）；
   3) 确认云安全组【不放行】3306。
EOF
