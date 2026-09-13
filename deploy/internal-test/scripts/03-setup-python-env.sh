#!/usr/bin/env bash
# =============================================================================
# 文件：deploy/internal-test/scripts/03-setup-python-env.sh
# 作用：创建 Python 虚拟环境并安装后端依赖（含国内镜像加速）
# 执行：sudo bash deploy/internal-test/scripts/03-setup-python-env.sh
# 说明：venv 放在 backend/.venv，与 systemd 单元的 ExecStart 路径一致
# =============================================================================

set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

require_root
require_cmd python3
banner_internal_only

REPO_DIR="${APP_DIR}/app"
BACKEND_DIR="${REPO_DIR}/backend"
VENV_DIR="${BACKEND_DIR}/.venv"
# 国内服务器直连 PyPI 常超时；默认使用清华镜像，可用 PIP_INDEX_URL 覆盖
PIP_INDEX="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"

[[ -d "$BACKEND_DIR" ]] || die "未找到 backend 目录（请先执行 01-pull-code.sh）"

step "1/4 创建虚拟环境"
if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    sudo -u "$APP_USER" python3 -m venv "$VENV_DIR"
    log_ok "已创建 venv：${VENV_DIR}"
else
    log_info "venv 已存在，复用"
fi
sudo -u "$APP_USER" "${VENV_DIR}/bin/python" -m pip install --upgrade pip setuptools wheel \
    -i "$PIP_INDEX" 2>&1 | tail -n 3 | sed 's/^/  /'

step "2/4 安装后端依赖（requirements.txt）"
[[ -f "${BACKEND_DIR}/requirements.txt" ]] || die "缺少 backend/requirements.txt"
sudo -u "$APP_USER" "${VENV_DIR}/bin/pip" install -r "${BACKEND_DIR}/requirements.txt" \
    -i "$PIP_INDEX" 2>&1 | tail -n 12 | sed 's/^/  /'

step "3/4 安装 C++ 编译所需 pybind11（cmake 需要）"
sudo -u "$APP_USER" "${VENV_DIR}/bin/pip" install "pybind11>=2.10" -i "$PIP_INDEX" \
    2>&1 | tail -n 3 | sed 's/^/  /'

step "4/4 自检关键依赖"
sudo -u "$APP_USER" "${VENV_DIR}/bin/python" - <<'PY' | sed 's/^/  /'
import importlib, sys
mods = ["fastapi", "uvicorn", "pydantic", "pydantic_settings", "jwt", "httpx", "celery", "redis", "pybind11"]
missing = []
for m in mods:
    try:
        importlib.import_module(m)
        print(f"OK   {m}")
    except Exception as exc:
        missing.append(m)
        print(f"MISS {m}  ({exc})")
print("-" * 46)
print("Python:", sys.version.split()[0])
sys.exit(1 if missing else 0)
PY
rc=$?
if [[ $rc -ne 0 ]]; then
    die "存在缺失依赖，请检查上方输出（必要时重跑本脚本）"
fi

log_ok "Python 环境就绪：${VENV_DIR}"
echo "  解释器版本：$("${VENV_DIR}/bin/python" --version 2>&1)"
