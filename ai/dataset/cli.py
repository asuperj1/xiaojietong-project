#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`C31` 数据采集与标注工具链 · 命令行入口。

用法（在**仓库根**执行）：

```bash
# 1) 采集：从数据库（真实素材）
python -m ai.dataset.cli collect --from db --kind notice --limit 200 --out ai/dataset/out/raw.jsonl
#    或从本地目录（离线可用）
python -m ai.dataset.cli collect --from dir --root docs/kb_samples --out ai/dataset/out/raw.jsonl

# 2) 预标注（生成人工修订用的初稿）
python -m ai.dataset.cli prelabel --in ai/dataset/out/raw.jsonl --out ai/dataset/out/prelabeled.jsonl \
    --labeler keyword

# 3) 导出三件套（训练 JSONL / 人工标注 CSV / 统计报告）
python -m ai.dataset.cli export --in ai/dataset/out/prelabeled.jsonl --out-dir ai/dataset/out

# 4) 一键跑完（采集 → 去重 → 预标注 → 导出）
python -m ai.dataset.cli pipeline --from db --kind notice --limit 200 --out-dir ai/dataset/out
```

数据库来源需要 `XJT_DB_*` 环境变量与连接池可用（见 `ai/dataset/README.md`）。
产物默认落在 `ai/dataset/out/`，该目录被仓库 `.gitignore` 的 `out/` 规则忽略
（**数据集本身不入库**，入库的是工具链与统计报告）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .collect import collect_from_db, collect_from_dir, dedupe, summary
from .export import export_labeling_csv, export_training_jsonl, stats_markdown
from .prelabel import prelabel_samples, resolve_labeler
from .schema import read_jsonl, validate_sample, write_jsonl

DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "out"


def _report_validation(samples) -> int:
    """返回不合规条数（供 CLI 决定退出码：有问题就非 0，便于脚本判断）。

    ⚠️ 调用点必须覆盖**每一个会改变 labels 的阶段**。
    早先只在 `collect` 阶段校验（那时 labels 还都是空的，必然通过），
    于是 `prelabel` 写进去的空字段一路流到了导出与训练目标而无人报警。
    """
    bad = 0
    for s in samples:
        problems = validate_sample(s)
        if problems:
            bad += 1
            print(f"  [!] {s.id}: {'; '.join(problems)}")
    return bad


def cmd_collect(args: argparse.Namespace) -> int:
    if args.source == "db":
        samples = collect_from_db(kind=args.kind, limit=args.limit)
    else:
        samples = collect_from_dir(args.root, exts=tuple(args.ext))
    samples, dropped = dedupe(samples)
    info = summary(samples)
    print(f"[collect] 采集 {info['total']} 条（去重丢弃 {dropped} 条）")
    print(f"[collect] 来源分布：{info['by_source_type']}")
    print(f"[collect] 文本长度：{info['text_len']}")
    bad = _report_validation(samples)
    if args.out:
        n = write_jsonl(args.out, samples)
        print(f"[collect] 已写出 {n} 条 -> {args.out}")
    return 1 if bad else 0


def cmd_prelabel(args: argparse.Namespace) -> int:
    samples = read_jsonl(args.infile)
    labeler = resolve_labeler(args.labeler)
    out = prelabel_samples(samples, labeler)
    skipped = sum(1 for s in out if s.status in ("human", "reviewed"))
    print(f"[prelabel] labeler={args.labeler}，处理 {len(out)} 条（其中 {skipped} 条人工样本被跳过）")
    # 预标注产物也要过校验：本阶段是**第一个往 labels 写值的环节**，
    # 不在这里拦，坏数据会直接进导出与训练目标。
    bad = _report_validation(out)
    n = write_jsonl(args.out, out)
    print(f"[prelabel] 已写出 {n} 条 -> {args.out}")
    return 1 if bad else 0


def cmd_export(args: argparse.Namespace) -> int:
    samples = read_jsonl(args.infile)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    n_train = export_training_jsonl(samples, out_dir / "train_extract.jsonl")
    n_csv = export_labeling_csv(samples, out_dir / "labeling.csv")
    report = stats_markdown(samples)
    (out_dir / "stats.md").write_text(report, encoding="utf-8")

    print(f"[export] 训练集 {n_train} 条 -> {out_dir / 'train_extract.jsonl'}")
    print(f"[export] 标注表 {n_csv} 条 -> {out_dir / 'labeling.csv'}")
    print(f"[export] 统计报告   -> {out_dir / 'stats.md'}")
    if n_train == 0:
        print("[export] [!] 训练集为空：所有样本的 labels 都为空，先跑 prelabel 或人工标注")
    # 导出前再校验一次（防御人工手改 intermediate 文件引入的脏数据）
    bad = _report_validation(samples)
    return 1 if bad else 0


def cmd_stats(args: argparse.Namespace) -> int:
    samples = read_jsonl(args.infile)
    report = stats_markdown(samples)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"[stats] 报告已写出 -> {args.out}")
    else:
        print(report)
    return 0


def cmd_pipeline(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = out_dir / "raw.jsonl"
    pre = out_dir / "prelabeled.jsonl"

    if cmd_collect(argparse.Namespace(
        source=args.source, kind=args.kind, limit=args.limit, root=args.root,
        ext=args.ext, out=str(raw),
    )) not in (0,):
        print("[pipeline] 采集阶段存在问题样本，继续执行（详见上面日志）")
    cmd_prelabel(argparse.Namespace(infile=str(raw), out=str(pre), labeler=args.labeler))
    return cmd_export(argparse.Namespace(infile=str(pre), out_dir=str(out_dir)))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m ai.dataset.cli",
        description="C31 校园公告信息抽取 · 数据集采集与标注工具链",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="采集（本地目录或数据库）")
    c.add_argument("--from", dest="source", choices=["db", "dir"], default="dir")
    c.add_argument("--kind", choices=["notice", "knowledge"], default="notice")
    c.add_argument("--limit", type=int, default=200)
    c.add_argument("--root", default=str(Path.cwd() / "docs" / "kb_samples"))
    c.add_argument("--ext", nargs="*", default=[".md", ".txt"])
    c.add_argument("--out", default=str(DEFAULT_OUT_DIR / "raw.jsonl"))
    c.set_defaults(func=cmd_collect)

    l = sub.add_parser("prelabel", help="预标注（生成人工修订初稿）")
    l.add_argument("--in", dest="infile", required=True)
    l.add_argument("--out", required=True)
    l.add_argument("--labeler", default="keyword",
                   help="keyword / null / '包.模块:函数名'")
    l.set_defaults(func=cmd_prelabel)

    e = sub.add_parser("export", help="导出训练 JSONL / 标注 CSV / 统计报告")
    e.add_argument("--in", dest="infile", required=True)
    e.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    e.set_defaults(func=cmd_export)

    s = sub.add_parser("stats", help="只打印统计报告")
    s.add_argument("--in", dest="infile", required=True)
    s.add_argument("--out", default="")
    s.set_defaults(func=cmd_stats)

    pl = sub.add_parser("pipeline", help="一键：采集 → 去重 → 预标注 → 导出")
    pl.add_argument("--from", dest="source", choices=["db", "dir"], default="db")
    pl.add_argument("--kind", choices=["notice", "knowledge"], default="notice")
    pl.add_argument("--limit", type=int, default=200)
    pl.add_argument("--root", default=str(Path.cwd() / "docs" / "kb_samples"))
    pl.add_argument("--ext", nargs="*", default=[".md", ".txt"])
    pl.add_argument("--labeler", default="keyword")
    pl.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    pl.set_defaults(func=cmd_pipeline)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
