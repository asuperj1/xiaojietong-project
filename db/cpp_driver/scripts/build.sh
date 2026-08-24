#!/usr/bin/env bash
# ============================================================
# 校捷通 C++ 数据访问层 · Linux 一键构建（环境自适应）
# 用法: bash scripts/build.sh
# 环境: 需安装 gcc/g++、cmake、libmysqlclient-dev、python3-dev、pybind11
# ============================================================
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# 检查依赖
command -v cmake >/dev/null || { echo "[错误] 未安装 cmake"; exit 1; }

echo "[MySQL] 自动检测 libmysqlclient..."
if [ ! -f /usr/include/mysql/mysql.h ]; then
    echo "[提示] 未找到 libmysqlclient，请执行: sudo apt install -y libmysqlclient-dev"
    exit 1
fi

echo "[CMake] 配置中..."
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release

echo "[Build] 构建中..."
cmake --build build --target jt_db

echo ""
echo "=============================================="
echo "  构建成功！"
echo "  产物: backend/app/db/native/jt_db.cpython-*.so"
echo "=============================================="
