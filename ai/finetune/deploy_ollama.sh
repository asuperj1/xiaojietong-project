#!/usr/bin/env bash
# 校捷通 · Ollama 微调模型一键部署（Linux / macOS）
#
# 面向"接收方"队友：拿到 GGUF 后一条命令完成 校验 → 生成 Modelfile → ollama create → 验证。
# GGUF 约 5.75 GiB，体积大不入 Git，请先从团队网盘（夸克）下载。
#
# 用法：
#   bash ai/finetune/deploy_ollama.sh ~/models/xjt-3b-f16.gguf
#   bash ai/finetune/deploy_ollama.sh ~/models/xjt-3b-f16.gguf xjt-3b
#   SKIP_SHA256=1 bash ai/finetune/deploy_ollama.sh ~/models/xjt-3b-f16.gguf   # 跳过校验（不推荐）
#
# 作者：成员3（C++ 数据层 / 模型微调 / 数据库）

set -euo pipefail

GGUF_PATH="${1:-$HOME/models/xjt-3b-f16.gguf}"
MODEL_NAME="${2:-xjt-3b}"
EXPECTED_SHA256="f4edd50b9d3759f8c742927a7dcf41ce979f8e2e7a93fceb6dccdb7131be75a3"
SKIP_SHA256="${SKIP_SHA256:-0}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/Modelfile"

# ---------- 1/5 检查模板 ----------
if [[ ! -f "$TEMPLATE" ]]; then
  echo "❌ 找不到 Modelfile 模板：$TEMPLATE" >&2
  exit 1
fi

# ---------- 2/5 检查 GGUF ----------
if [[ ! -f "$GGUF_PATH" ]]; then
  echo "❌ 找不到 GGUF 文件：$GGUF_PATH" >&2
  echo "   请先从团队网盘（夸克）下载 xjt-3b-f16.gguf（约 5.75 GiB），" >&2
  echo "   或传入实际路径：bash ai/finetune/deploy_ollama.sh <你的gguf路径>" >&2
  exit 1
fi
SIZE_GIB="$(awk -v b="$(wc -c < "$GGUF_PATH")" 'BEGIN{printf "%.2f", b/1073741824}')"
echo "[1/5] GGUF 文件：$GGUF_PATH  (${SIZE_GIB} GiB)"

# ---------- 3/5 SHA256 校验 ----------
if [[ "$SKIP_SHA256" != "1" && -n "$EXPECTED_SHA256" ]]; then
  echo "[2/5] 正在校验 SHA256（约 10~30 秒）..."
  if command -v sha256sum >/dev/null 2>&1; then
    ACTUAL="$(sha256sum "$GGUF_PATH" | awk '{print $1}')"
  else
    ACTUAL="$(shasum -a 256 "$GGUF_PATH" | awk '{print $1}')"
  fi
  if [[ "$ACTUAL" != "$EXPECTED_SHA256" ]]; then
    echo "❌ SHA256 不匹配，文件可能在下载/传输中损坏！" >&2
    echo "   期望：$EXPECTED_SHA256" >&2
    echo "   实际：$ACTUAL" >&2
    exit 1
  fi
  echo "      ✅ 校验通过"
else
  echo "[2/5] 已跳过 SHA256 校验"
fi

# ---------- 4/5 检查 Ollama ----------
if ! command -v ollama >/dev/null 2>&1; then
  echo "❌ 未找到 ollama 命令，请先安装：https://ollama.com/download" >&2
  exit 1
fi
echo "[3/5] Ollama：$(command -v ollama)"

# ---------- 5/5 生成临时 Modelfile 并创建模型 ----------
TMP_MODELFILE="$(mktemp -t xjt_modelfile_XXXXXX)"
trap 'rm -f "$TMP_MODELFILE"' EXIT

# 把模板里的 FROM 行替换成本机实际路径
awk -v from="FROM $GGUF_PATH" '
  /^FROM[[:space:]]/ { print from; next }
  { print }
' "$TEMPLATE" > "$TMP_MODELFILE"
echo "[4/5] 已按本机路径生成 Modelfile：$TMP_MODELFILE"

ollama create "$MODEL_NAME" -f "$TMP_MODELFILE"

echo "[5/5] 当前模型列表："
ollama list

echo ""
echo "✅ 部署完成！"
echo "后端启用：在 backend/.env 中配置 XJT_OLLAMA_MODEL=$MODEL_NAME 后重启后端服务。"
echo "快速自测： ollama run $MODEL_NAME \"图书馆几点关门？\""
