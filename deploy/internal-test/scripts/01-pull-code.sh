#!/usr/bin/env bash
# =============================================================================
# 文件：deploy/internal-test/scripts/01-pull-code.sh
# 作用：拉取 GitHub 最新代码到 APP_DIR/app（可重复执行，自动处理本地改动）
# 执行：sudo bash deploy/internal-test/scripts/01-pull-code.sh [分支名]
# 说明：默认拉取 dev 分支（团队集成分支）；首次执行会自动 clone
# ⚠️ 私有仓库需要凭据：推荐用「只读 Deploy Key」或 PAT（见 docs/02-部署操作手册.md §4）
# =============================================================================

set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

require_root
require_cmd git
banner_internal_only

BRANCH="${1:-$GIT_BRANCH}"
REPO_DIR="${APP_DIR}/app"

step "1/4 准备目录与运行账号"
mkdir -p "$APP_DIR" "$SHARED_DIR"
if ! id -u "$APP_USER" >/dev/null 2>&1; then
    useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
    log_ok "已创建运行账号：$APP_USER（禁止交互登录）"
else
    log_info "运行账号已存在：$APP_USER"
fi
chown -R "${APP_USER}:${APP_USER}" "$APP_DIR"
log_info "代码目录：${REPO_DIR}   共享目录：${SHARED_DIR}"

step "2/4 检查 git 凭据（私有仓库必看）"
if [[ ! -d "${REPO_DIR}/.git" ]]; then
    # 首次 clone：优先使用环境变量里的 PAT；否则回退为匿名（公开仓库可用）
    if [[ -n "${GITHUB_TOKEN:-}" ]]; then
        CLONE_URL="https://x-access-token:${GITHUB_TOKEN}@github.com/asuperj1/xiaojietong-project.git"
        log_info "使用 GITHUB_TOKEN 克隆"
    else
        CLONE_URL="$APP_REPO_URL"
        log_info "未提供 GITHUB_TOKEN，按公开仓库匿名克隆"
    fi
    log_info "克隆 ${CLONE_URL%@*}@... （分支 ${BRANCH}）"
    sudo -u "$APP_USER" git clone --branch "$BRANCH" --depth 1 "$CLONE_URL" "$REPO_DIR" \
        || die "克隆失败。私有仓库请先 export GITHUB_TOKEN=<你的PAT> 后重跑本脚本"
else
    log_info "仓库已存在，执行增量更新"
fi

step "3/4 拉取最新提交"
cd "$REPO_DIR"

# 让该仓库使用干净的拉取策略：本地若有手改，先备份为 patch 再丢弃
if ! sudo -u "$APP_USER" git diff --quiet 2>/dev/null; then
    STAMP="$(date +%Y%m%d%H%M%S)"
    PATCH="${SHARED_DIR}/local-changes-${STAMP}.patch"
    sudo -u "$APP_USER" git diff > "$PATCH" || true
    log_warn "检测到本地未提交改动，已备份到 ${PATCH}"
fi
# 未跟踪文件（如 .env）保留不动
sudo -u "$APP_USER" git -C "$REPO_DIR" stash list >/dev/null 2>&1 || true

sudo -u "$APP_USER" git fetch --prune origin "+refs/heads/${BRANCH}:refs/remotes/origin/${BRANCH}"
sudo -u "$APP_USER" git checkout -B "$BRANCH" "origin/${BRANCH}"
sudo -u "$APP_USER" git reset --hard "origin/${BRANCH}"
sudo -u "$APP_USER" git clean -fd \
    -e '.env' -e 'app/db/native' -e '.venv' 2>/dev/null || true

step "4/4 结果"
echo "  分支    : ${BRANCH}"
echo "  提交    : $(git -C "$REPO_DIR" log -1 --format='%h  %ad  %s' --date=short)"
echo "  时间    : $(git -C "$REPO_DIR" log -1 --format='%cd' --date=iso)"
echo "  工作树  : $(git -C "$REPO_DIR" status --porcelain | wc -l) 处未提交改动"

log_ok "代码已更新到 ${BRANCH}"
cat <<EOF

下一步：bash deploy/internal-test/scripts/02-build-cpp.sh   （或直接一键 08-deploy.sh）
EOF
