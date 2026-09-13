#!/usr/bin/env bash
# =============================================================================
# 文件：deploy/internal-test/scripts/02-build-cpp.sh
# 作用：在服务器本机编译 C++ 数据层 jt_db → backend/app/db/native/jt_db.cpython-*.so
# 执行：sudo bash deploy/internal-test/scripts/02-build-cpp.sh
# 说明：.pyd/.so 与 Python ABI 绑定，**不可跨机拷贝**，每台服务器必须本机编译
# 依赖：cmake / g++ / libmysqlclient-dev（由 00-install-system-deps.sh 安装）
# =============================================================================

set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

require_root
require_cmd cmake g++ python3
banner_internal_only

REPO_DIR="${APP_DIR}/app"
CPP_DIR="${REPO_DIR}/db/cpp_driver"
NATIVE_DIR="${REPO_DIR}/backend/app/db/native"
JOBS="${JOBS:-$(nproc)}"

[[ -d "$CPP_DIR" ]] || die "未找到 C++ 源码目录：${CPP_DIR}（请先执行 01-pull-code.sh）"

step "1/4 检查编译前置（MySQL C API）"
if [[ ! -f /usr/include/mysql/mysql.h ]]; then
    die "缺少 /usr/include/mysql/mysql.h。请执行：sudo apt install -y libmysqlclient-dev"
fi
log_ok "mysql.h 存在"

step "2/4 CMake 配置（Release + 释放 GIL 并发优化）"
cd "$CPP_DIR"
rm -rf build
sudo -u "$APP_USER" cmake -B build -S "$CPP_DIR" \
    -DCMAKE_BUILD_TYPE=Release \
    -DJT_DB_RELEASE_GIL=ON \
    -DJT_DB_BUILD_TEST=OFF \
    2>&1 | sed 's/^/  /'

step "3/4 编译（并行 ${JOBS} 线程）"
sudo -u "$APP_USER" cmake --build build --config Release -j "$JOBS" 2>&1 | tail -n 30 | sed 's/^/  /'

step "4/4 校验产物"
EXPECTED="$(find "$NATIVE_DIR" -maxdepth 1 -name 'jt_db.cpython-*.so' -print -quit 2>/dev/null || true)"
[[ -n "$EXPECTED" ]] || die "未生成 jt_db.cpython-*.so，请查看上方编译错误"

ls -lh "$NATIVE_DIR"/jt_db* | sed 's/^/  /'
chown -R "${APP_USER}:${APP_USER}" "$NATIVE_DIR"

# 用与 systemd 相同的解释器加载一次，确认 ABI 与依赖链正常
VENV_PY="${REPO_DIR}/backend/.venv/bin/python"
if [[ -x "$VENV_PY" ]]; then
    if sudo -u "$APP_USER" "$VENV_PY" -c "import jt_db; print('  jt_db 版本标记:', getattr(jt_db,'__version__','(无)'))" 2>&1 | sed 's/^/  /'; then
        log_ok "jt_db 扩展可在 venv 中成功加载"
    else
        log_warn "jt_db 加载失败 —— 常见原因：ctypes/cffi 缺 libmysqlclient.so（见 docs/05 排障）"
    fi
else
    log_info "venv 尚未创建，跳过加载自检（请先跑 03-setup-python-env.sh）"
fi

log_ok "C++ 数据层编译完成：${EXPECTED}"
