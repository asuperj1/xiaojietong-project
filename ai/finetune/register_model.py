#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把微调模型产物登记进 `model_version` 表（C11）。

【为什么需要】
    `model_version` 表此前**零使用** —— 模型训出来、部署好了，库里却没有
    任何记录，"数据闭环"讲不圆，也无法被 `/health/detail` 之类接口读取展示。
    本脚本把 C4/C5 的全部产物与训练超参登记入库。

【数据来源（全部取自真实产物，非手填）】
    · GGUF      ：文件字节数 + SHA256（可 --skip-sha256 跳过校验和计算）
    · LoRA 配置 ：从 adapter_config.json 读取 r / alpha / dropout / target_modules
    · 训练指标  ：CLI 传入（与 ai/finetune/训练报告.md 一致）

【幂等】
    按 `name` 判断：已存在则 UPDATE，不存在则 INSERT —— 可反复执行。

【用法】
    # 需要能访问 MySQL 的 Python（走 C++ 数据层）
    $env:XJT_DB_PASSWORD='***'
    python ai/finetune/register_model.py \
        --gguf E:/models/xjt-3b-f16.gguf \
        --adapter ai/finetune/out/qwen3b-lora \
        --name xjt-3b

作者：成员3（C++ 数据层 / 模型微调 / 数据库）· C11
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from app.db import cpp_bridge  # noqa: E402


def sha256_of(path: Path, chunk: int = 8 * 1024 * 1024) -> str:
    """流式计算大文件 SHA256（避免一次性读入内存）。"""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def load_lora_config(adapter_dir: Optional[Path]) -> dict[str, Any]:
    """从 adapter_config.json 读取 LoRA 真实配置。"""
    if not adapter_dir or not (adapter_dir / "adapter_config.json").is_file():
        return {}
    try:
        cfg = json.loads((adapter_dir / "adapter_config.json").read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"[警告] 读取 adapter_config.json 失败：{exc}")
        return {}
    return {
        "r": cfg.get("r"),
        "alpha": cfg.get("lora_alpha"),
        "dropout": cfg.get("lora_dropout"),
        "target_modules": cfg.get("target_modules"),
        "peft_version": cfg.get("peft_version"),
        "base_model_in_config": cfg.get("base_model_name_or_path"),
    }


def build_metrics(args: argparse.Namespace, lora: dict[str, Any],
                  gguf_bytes: Optional[int], gguf_sha: str) -> dict[str, Any]:
    """组装 metrics_json（训练 / LoRA / 产物 / 环境 四组）。"""
    return {
        "data": {
            "samples": args.samples,
            "source": "ai/finetune/data/train.jsonl",
        },
        "train": {
            "epochs": args.epochs,
            "batch_size": args.batch_size,
            "grad_accum": args.grad_accum,
            "max_len": args.max_len,
            "lr": args.lr,
            "lr_scheduler": "cosine",
            "warmup_steps": args.warmup_steps,
            "steps": args.steps,
            "seconds": args.seconds,
            "train_loss": args.train_loss,
            "loss_start": args.loss_start,
            "loss_end": args.loss_end,
        },
        "lora": {
            **{k: v for k, v in lora.items() if k != "base_model_in_config"},
            "trainable_params": args.trainable_params,
            "total_params": args.total_params,
            "trainable_ratio": round(args.trainable_params / args.total_params, 4),
        },
        "artifact": {
            "ollama_model": args.name,
            "gguf_path": args.gguf.replace("\\", "/"),
            "gguf_bytes": gguf_bytes,
            "gguf_sha256": gguf_sha or None,
            "adapter_path": args.adapter.replace("\\", "/") if args.adapter else None,
            "merged_path": args.merged.replace("\\", "/") if args.merged else None,
            "num_ctx": args.num_ctx,
            "temperature": args.temperature,
        },
        "env": {
            "gpu": args.gpu,
            "python": args.python_version,
            "torch": args.torch_version,
            "transformers": args.transformers_version,
            "peft": lora.get("peft_version"),
        },
    }


def upsert_model_version(row: dict[str, Any]) -> tuple[str, int]:
    """按 name 幂等写入，返回 (操作, id)。"""
    found = cpp_bridge.query("SELECT id FROM model_version WHERE name = ?", [row["name"]])
    if found:
        mid = int(found[0]["id"])
        cpp_bridge.execute(
            "UPDATE model_version SET base_model = ?, method = ?, quant_level = ?, "
            "metrics_json = ?, status = ?, file_path = ?, trained_at = ? WHERE id = ?",
            [row["base_model"], row["method"], row["quant_level"],
             row["metrics_json"], row["status"], row["file_path"],
             row["trained_at"], mid],
        )
        return "UPDATE", mid

    _, new_id = cpp_bridge.execute(
        "INSERT INTO model_version (name, base_model, method, quant_level, "
        "metrics_json, status, file_path, trained_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [row["name"], row["base_model"], row["method"], row["quant_level"],
         row["metrics_json"], row["status"], row["file_path"], row["trained_at"]],
    )
    return "INSERT", int(new_id)


def main() -> int:
    ap = argparse.ArgumentParser(description="登记微调模型产物到 model_version 表（C11）")
    ap.add_argument("--name", default="xjt-3b", help="模型名（Ollama 中的名字）")
    ap.add_argument("--base-model", default="Qwen2.5-3B-Instruct", help="基座模型名")
    ap.add_argument("--method", default="QLoRA", help="微调方法")
    ap.add_argument("--quant-level", default="f16", help="GGUF 量化等级")
    ap.add_argument("--gguf", default="E:/models/xjt-3b-f16.gguf", help="GGUF 文件路径")
    ap.add_argument("--adapter", default="ai/finetune/out/qwen3b-lora", help="LoRA 适配器目录")
    ap.add_argument("--merged", default="E:/models/xjt-3b-merged", help="合并权重目录")
    ap.add_argument("--status", type=int, default=1, help="0训练中 1可用 2已弃用")
    ap.add_argument("--skip-sha256", action="store_true", help="跳过 SHA256 计算（6GB 约 20s）")

    # 训练指标（默认值取自 ai/finetune/训练报告.md 的实测结果）
    ap.add_argument("--samples", type=int, default=1780)
    ap.add_argument("--epochs", type=float, default=1.0)
    ap.add_argument("--batch-size", type=int, default=1)
    ap.add_argument("--grad-accum", type=int, default=8)
    ap.add_argument("--max-len", type=int, default=768)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--warmup-steps", type=int, default=20)
    ap.add_argument("--steps", type=int, default=223)
    ap.add_argument("--seconds", type=float, default=675.6)
    ap.add_argument("--train-loss", type=float, default=0.6099)
    ap.add_argument("--loss-start", type=float, default=4.298)
    ap.add_argument("--loss-end", type=float, default=0.112)
    ap.add_argument("--trainable-params", type=int, default=29933568)
    ap.add_argument("--total-params", type=int, default=3115872256)

    # 部署参数与环境
    ap.add_argument("--num-ctx", type=int, default=4096)
    ap.add_argument("--temperature", type=float, default=0.4)
    ap.add_argument("--gpu", default="NVIDIA RTX 4080 SUPER 16GB")
    ap.add_argument("--python-version", default="3.11.15")
    ap.add_argument("--torch-version", default="2.6.0+cu124")
    ap.add_argument("--transformers-version", default="5.17.0")
    ap.add_argument("--dry-run", action="store_true", help="只打印不入库")
    args = ap.parse_args()

    # ---------- 0. 连接数据库（--skip-sha256 需读取库中已登记的值） ----------
    host = os.environ.get("XJT_DB_HOST", "127.0.0.1")
    port = int(os.environ.get("XJT_DB_PORT", "3307"))
    user = os.environ.get("XJT_DB_USER", "root")
    dbname = os.environ.get("XJT_DB_NAME", "xiaojietong")
    password = os.environ.get("XJT_DB_PASSWORD", "")

    need_db = not args.dry_run
    if need_db:
        if not password:
            print("[错误] 未设置 XJT_DB_PASSWORD 环境变量。", file=sys.stderr)
            return 1
        if not cpp_bridge.available():
            print("[错误] jt_db C++ 扩展不可用（请用能加载 .pyd 的解释器）。", file=sys.stderr)
            return 1
        cpp_bridge.init_db(host, port, user, password, dbname, min_conn=2, max_conn=4)

    # 复用库中已登记的 SHA256，避免 --skip-sha256 把已有值覆盖成 null
    existing_sha = ""
    if need_db:
        prev = cpp_bridge.query(
            "SELECT metrics_json FROM model_version WHERE name = ?", [args.name])
        if prev and prev[0].get("metrics_json"):
            try:
                old = json.loads(prev[0]["metrics_json"])
                existing_sha = (old.get("artifact") or {}).get("gguf_sha256") or ""
            except (ValueError, TypeError):
                existing_sha = ""

    # ---------- 1. 校验 GGUF（真实文件信息） ----------
    gguf_path = Path(args.gguf)
    gguf_bytes: Optional[int] = None
    gguf_sha = ""
    if gguf_path.is_file():
        gguf_bytes = gguf_path.stat().st_size
        print(f"[1/4] GGUF：{gguf_path}  {gguf_bytes / 1024 ** 3:.2f} GiB")
        if not args.skip_sha256:
            print("      计算 SHA256 ...")
            gguf_sha = sha256_of(gguf_path)
            print(f"      sha256 = {gguf_sha}")
        elif existing_sha:
            gguf_sha = existing_sha
            print(f"      --skip-sha256：复用库中已登记的 sha256 = {gguf_sha}")
        else:
            print("      --skip-sha256：库中无历史值，本次登记 sha256 为 null")
    else:
        print(f"[1/4] [警告] 未找到 GGUF：{gguf_path}（将登记为 null）")

    # ---------- 2. 读 LoRA 真实配置 ----------
    adapter_dir = (REPO / args.adapter) if args.adapter and not Path(args.adapter).is_absolute() \
        else (Path(args.adapter) if args.adapter else None)
    lora = load_lora_config(adapter_dir)
    print(f"[2/4] LoRA 配置：{lora or '（未找到 adapter_config.json，沿用 CLI 值）'}")

    # ---------- 3. 组装行 ----------
    metrics = build_metrics(args, lora, gguf_bytes, gguf_sha)
    trained_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = {
        "name": args.name,
        "base_model": args.base_model,
        "method": args.method,
        "quant_level": args.quant_level,
        "metrics_json": json.dumps(metrics, ensure_ascii=False),
        "status": args.status,
        "file_path": args.gguf.replace("\\", "/"),
        "trained_at": trained_at,
    }
    print(f"[3/4] 待写入 model_version：name={row['name']} status={row['status']}")
    print(f"      metrics_json = {json.dumps(metrics, ensure_ascii=False)[:200]}...")

    if args.dry_run:
        print("[4/4] --dry-run：未写入数据库。")
        return 0

    # ---------- 4. 写库 ----------
    action, mid = upsert_model_version(row)
    print(f"[4/4] {action} 成功：model_version.id = {mid}")

    check = cpp_bridge.query(
        "SELECT id, name, base_model, method, quant_level, status, file_path, trained_at "
        "FROM model_version WHERE id = ?", [mid])
    print("\n库内记录：")
    for k, v in check[0].items():
        print(f"  {k:12} = {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
