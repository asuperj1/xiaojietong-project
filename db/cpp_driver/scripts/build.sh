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
# 头文件位置随发行版与包名（libmysqlclient-dev / default-libmysqlclient-dev /
# libmariadb-dev）而变，所以逐路径探测 + 兜底问 mysql_config，
# 而不是只认 /usr/include/mysql 一个路径（Ubuntu 24.04 上曾因此直接 exit 1）。
MYSQL_INC=""
for d in /usr/include/mysql /usr/include/mariadb /usr/local/include/mysql; do
    if [ -f "$d/mysql.h" ]; then MYSQL_INC="$d"; break; fi
done
if [ -z "$MYSQL_INC" ] && command -v mysql_config >/dev/null 2>&1; then
    MYSQL_INC="$(mysql_config --include 2>/dev/null | tr ' ' '\n' | sed -n 's/^-I//p' | head -1)"
fi
if [ -z "$MYSQL_INC" ] || [ ! -f "$MYSQL_INC/mysql.h" ]; then
    echo "[提示] 未找到 mysql.h（已试 /usr/include/mysql、/usr/include/mariadb、mysql_config）"
    echo "        请安装其一：sudo apt install -y libmysqlclient-dev"
    echo "        （或 default-libmysqlclient-dev / libmariadb-dev）"
    exit 1
fi
echo "[MySQL] 头文件目录: $MYSQL_INC"

echo "[CMake] 配置中..."
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release

echo "[Build] 构建中..."
cmake --build build --target jt_db

echo ""
echo "=============================================="
echo "  构建成功！"
echo "  产物: backend/app/db/native/jt_db.cpython-*.so"
echo "=============================================="
