#!/usr/bin/env bash
# =============================================================================
# 文件：deploy/internal-test/scripts/00-install-system-deps.sh
# 作用：安装服务器系统依赖（编译链 / Python / MySQL 客户端与服务 / 常用工具）
# 适用：Ubuntu 20.04+ / Debian 11+（apt）；CentOS 8+ / Rocky / Alma（dnf|yum）
# 执行：sudo bash deploy/internal-test/scripts/00-install-system-deps.sh
# ⚠️ 本脚本**不配置任何域名、不开放 80/443、不安装/配置 Nginx、不申请 SSL 证书**
# =============================================================================

set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

require_root
banner_internal_only

step "1/4 识别系统与包管理器"
PKG=""
if command -v apt-get >/dev/null 2>&1; then
    PKG=apt
elif command -v dnf >/dev/null 2>&1; then
    PKG=dnf
elif command -v yum >/dev/null 2>&1; then
    PKG=yum
else
    die "未识别的包管理器（仅支持 apt / dnf / yum）。请手动安装依赖后重试。"
fi
log_info "包管理器：${PKG}"
[[ -f /etc/os-release ]] && log_info "系统：$(. /etc/os-release; echo "${PRETTY_NAME}")"

step "2/4 安装编译链与 Python 运行时"
# 说明：C++ 数据层 jt_db 需要在服务器本机编译（.pyd/.so 不可跨机复用）
case "$PKG" in
    apt)
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -y
        apt-get install -y --no-install-recommends \
            git curl ca-certificates \
            build-essential cmake pkg-config \
            python3 python3-dev python3-venv python3-pip \
            libmysqlclient-dev
        ;;
    dnf|yum)
        "$PKG" install -y \
            git curl ca-certificates \
            gcc gcc-c++ make cmake pkgconfig \
            python3 python3-devel python3-pip \
            mysql-devel
        ;;
esac
log_ok "编译链与 Python 就绪"

step "3/4 安装 MySQL 服务端（可选，数据库也可用云数据库）"
if command -v mysqld >/dev/null 2>&1 || systemctl list-unit-files 2>/dev/null | grep -q '^mysql\.service'; then
    log_info "已检测到 MySQL，跳过安装"
else
    case "$PKG" in
        apt) apt-get install -y mysql-server ;;
        *)   "$PKG" install -y mysql-server || log_warn "自动安装 MySQL 失败，请手动安装（学生机常需先加 MySQL 官方源）" ;;
    esac
fi

# ⚠️ 关键安全约束：MySQL 只监听本机回环，绝不暴露公网
MYSQL_CNF=""
for f in /etc/mysql/mysql.conf.d/mysqld.cnf /etc/my.cnf /etc/mysql/my.cnf; do
    [[ -f "$f" ]] && { MYSQL_CNF="$f"; break; }
done
if [[ -n "$MYSQL_CNF" ]]; then
    cp -n "$MYSQL_CNF" "${MYSQL_CNF}.bak.$(date +%Y%m%d%H%M%S)" 2>/dev/null || true
    if grep -qE '^[[:space:]]*bind-address' "$MYSQL_CNF"; then
        sed -i -E 's|^[[:space:]]*bind-address.*|bind-address = 127.0.0.1|' "$MYSQL_CNF"
    else
        printf '\n[mysqld]\nbind-address = 127.0.0.1\n' >> "$MYSQL_CNF"
    fi
    systemctl enable --now mysql 2>/dev/null || systemctl enable --now mysqld 2>/dev/null || true
    log_ok "MySQL 已限定 bind-address=127.0.0.1（公网无法直连 3306）"
else
    log_warn "未找到 MySQL 配置文件，请**手动确认** bind-address=127.0.0.1"
fi

step "4/4 版本确认"
{
    echo "  git      : $(git --version 2>/dev/null || echo '缺失')"
    echo "  cmake    : $(cmake --version 2>/dev/null | head -n1 || echo '缺失')"
    echo "  g++      : $(g++ --version 2>/dev/null | head -n1 || echo '缺失')"
    echo "  python3  : $(python3 --version 2>/dev/null || echo '缺失')"
    echo "  mysql    : $(mysql --version 2>/dev/null || echo '缺失')"
    echo "  mysql.h  : $([[ -f /usr/include/mysql/mysql.h ]] && echo '存在' || echo '缺失（C++ 编译会失败）')"
} | sed 's/^/  /'

cat <<EOF

$(log_ok 系统依赖安装完成)

下一步：
  1) 配置云服务器【安全组】—— $(printf '%s' '放行 8080；拒绝 80/443/3306/6379')（控制台操作）
  2) 执行防火墙脚本：sudo bash deploy/internal-test/scripts/07-firewall.sh
  3) 执行一键部署：  sudo bash deploy/internal-test/scripts/08-deploy.sh
EOF

print_security_group_reminder "<你的公网IP>"
