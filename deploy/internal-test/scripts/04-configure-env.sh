#!/usr/bin/env bash
# =============================================================================
# 文件：deploy/internal-test/scripts/04-configure-env.sh
# 作用：生成/更新后端环境变量文件 $SHARED_DIR/server.env（权限 600）
# 执行：sudo bash deploy/internal-test/scripts/04-configure-env.sh [数据库密码]
# 特性：**幂等** —— 已存在的密钥（JWT / 访问令牌 / 上传签名）不会被覆盖
# ⚠️ 本文件不含任何域名、SSL、80/443 相关配置
# =============================================================================

set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

require_root
require_cmd openssl curl
banner_internal_only

DB_PASSWORD="${1:-}"
REPO_DIR="${APP_DIR}/app"

[[ -d "$REPO_DIR" ]] || die "未找到代码目录（请先执行 01-pull-code.sh）"

step "1/5 生成随机密钥（已存在则保留）"
rand_token() { openssl rand -base64 48 | tr -d '\n=+/' | cut -c1-48; }

EXISTING_JWT="$(env_get XJT_JWT_SECRET)";        JWT_SECRET="${EXISTING_JWT:-$(rand_token)}"
EXISTING_GATE="$(env_get XJT_ACCESS_TOKEN)";     ACCESS_TOKEN="${EXISTING_GATE:-$(rand_token)}"
EXISTING_UPLOAD="$(env_get XJT_UPLOAD_URL_SECRET)"; UPLOAD_SECRET="${EXISTING_UPLOAD:-$(rand_token)}"

if [[ -n "$EXISTING_GATE" ]]; then
    log_info "复用已有访问令牌（未重新生成）"
else
    log_warn "首次生成访问令牌 —— 请立即记录并分发给团队 4 人（只显示一次）"
fi

step "2/5 处理数据库密码"
if [[ -z "$DB_PASSWORD" ]]; then
    EXISTING_DB="$(env_get XJT_DB_PASSWORD)"
    DB_PASSWORD="${EXISTING_DB:-}"
fi
if [[ -z "$DB_PASSWORD" ]]; then
    log_warn "未提供数据库密码。用法：sudo bash $0 '<你的MySQL密码>'"
    log_warn "将写入占位符，请稍后手工编辑 ${SERVER_ENV_FILE}"
    DB_PASSWORD="<请填写MySQL密码>"
fi

step "3/5 探测同机 Ollama（可选组件）"
OLLAMA_URL="http://127.0.0.1:11434"
if curl -fsS --max-time 3 "${OLLAMA_URL}/api/tags" >/dev/null 2>&1; then
    log_ok "检测到本机 Ollama，AI 能力可启用"
    OLLAMA_NOTE="# Ollama 已探测到（$(date '+%F %T')）"
else
    log_warn "未检测到本机 Ollama —— AI 问答/审核将走规则兜底降级（不影响其它接口）"
    OLLAMA_NOTE="# Ollama 未探测到（$(date '+%F %T')）；安装后可复用本文件无需改动"
fi

step "4/5 写入 ${SERVER_ENV_FILE}"
mkdir -p "$SHARED_DIR"
cat > "$SERVER_ENV_FILE" <<EOF
# =============================================================================
# 校捷通 · 内部联调环境变量（由 04-configure-env.sh 生成）
# 生成时间：$(date '+%F %T')
# 权限：600（仅 ${APP_USER} 可读）—— 含密钥，禁止提交进仓库、禁止外传
# 说明：本文件【不包含】域名、SSL/HTTPS、80/443 相关配置（本阶段不对外提供网站服务）
# =============================================================================

# ---- 应用 ----
XJT_APP_NAME=校捷通
XJT_DEBUG=false
XJT_API_PREFIX=/api/v1
# ⚠️ 固定 dev 而非 prod：prod 会硬校验微信 AppID/Secret（本阶段走 mock 登录，无凭据），
#    会导致服务直接拒绝启动。安全防护改由下方【访问闸门】+ 强随机 JWT 密钥承担。
XJT_ENV=dev

# ---- 数据库（MySQL，仅本机 127.0.0.1，公网不可达）----
XJT_DB_HOST=127.0.0.1
XJT_DB_PORT=3306
XJT_DB_USER=root
XJT_DB_PASSWORD=${DB_PASSWORD}
XJT_DB_NAME=xiaojietong
XJT_DB_MIN_CONN=4
XJT_DB_MAX_CONN=32

# ---- 安全（JWT）----
XJT_JWT_SECRET=${JWT_SECRET}
XJT_JWT_ALGORITHM=HS256
XJT_JWT_EXPIRE_SECONDS=7200
XJT_JWT_REFRESH_EXPIRE_SECONDS=604800

# ---- ★访问闸门（防止外网陌生人访问测试接口）----
# 置空 = 关闭闸门（仅限本机开发）；服务器上**必须非空**
# 团队 4 人请求时带请求头：  X-Access-Token: <本值>
# 浏览器临时调试可用查询串：  ?access_token=<本值>
XJT_ACCESS_TOKEN=${ACCESS_TOKEN}
# 闸门豁免路径：只放行基础存活探针（供部署脚本/监控）
# ⚠️ 刻意不豁免 health/detail 与 selfcheck：它们会回显数据库/连接池/Ollama 状态
# 注意：health 挂在 api_prefix 下，完整路径为 /api/v1/health
XJT_ACCESS_GATE_EXEMPT=/api/v1/health

# ---- 微信小程序（本阶段留空 → 使用 mock 登录）----
XJT_WX_APPID=
XJT_WX_SECRET=

# ---- CORS ----
# 公网暴露场景**不要用 *** 通配；如需真机调试，追加开发者工具来源即可
XJT_CORS_ORIGINS=http://127.0.0.1:${APP_PORT},http://localhost:${APP_PORT}

# ---- 接口限流 ----
XJT_RATE_LIMIT_ENABLED=true
XJT_RATE_LIMIT_PER_MINUTE=300
XJT_RATE_LIMIT_LOGIN_PER_MINUTE=10

# ---- AI 推理服务（Ollama）----
${OLLAMA_NOTE}
XJT_OLLAMA_BASE_URL=${OLLAMA_URL}
XJT_OLLAMA_MODEL=xjt-3b

# ---- RAG 检索增强 ----
XJT_RAG_EMBED_MODEL=bge-m3
XJT_RAG_EMBED_DIM=1024
XJT_RAG_CHUNK_SIZE=600
XJT_RAG_CHUNK_OVERLAP=100
XJT_RAG_EMBED_BATCH=16
XJT_RAG_TOP_K=3
XJT_RAG_SCORE_THRESHOLD=0.35
XJT_RAG_VECTOR_DIR=data/rag

# ---- 文件存储与上传访问 ----
XJT_STORAGE_BACKEND=local
XJT_UPLOAD_SIGNED_URL_ENABLED=true
XJT_UPLOAD_URL_TTL_SECONDS=604800
XJT_UPLOAD_URL_SECRET=${UPLOAD_SECRET}

# ---- 异步任务（无 Redis 时保持 false，eager 就地同步执行）----
XJT_CELERY_ENABLED=false
XJT_CELERY_BROKER_URL=redis://127.0.0.1:6379/0
XJT_CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/1
EOF

chmod 600 "$SERVER_ENV_FILE"
chown "${APP_USER}:${APP_USER}" "$SERVER_ENV_FILE"
log_ok "已写入（权限 600，属主 ${APP_USER}）"

# 同步一份软链到 backend/.env，方便手动执行 uvicorn 调试；systemd 走 EnvironmentFile
ln -sfn "$SERVER_ENV_FILE" "${REPO_DIR}/backend/.env"
log_info "已建立软链：backend/.env -> ${SERVER_ENV_FILE}"

step "5/5 打印访问信息"
SERVER_IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || echo '<你的公网IP>')"
cat <<EOF

${C_BLD}┌──────────────── 团队访问方式（请私发给 4 人，勿公开）────────────────┐${C_RST}
   接口基地址 : http://${SERVER_IP}:${APP_PORT}/api/v1
   健康检查   : curl -H "X-Access-Token: ${ACCESS_TOKEN}" http://${SERVER_IP}:${APP_PORT}/api/v1/health
   访问令牌   : ${ACCESS_TOKEN}
${C_BLD}└──────────────────────────────────────────────────────────────────┘${C_RST}

${C_YEL}⚠️ 令牌等同本阶段唯一的门禁凭据：不要发到公开群 / 不要截图外传 / 不要提交进仓库。${C_RST}
EOF

print_security_group_reminder "$SERVER_IP"
