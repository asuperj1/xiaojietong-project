"""通知扩展字段回填（B30）—— `python -m app.cli.notice_backfill`。

把 `campus_notice` 的 `deadline` / `materials` / `importance` 三列补一遍，
用于 **B29 上线之前就已入库的存量通知** —— 之后新入库的通知由
`services/notice_ingest.ingest_notice()` 在写入时自动填充，不再需要这个命令。

**只补空值**：已经抽到（或运营手工改过）的列一律不动，可重复执行。

用法（在 `backend/` 下执行）::

    python -m app.cli.notice_backfill --dry-run        # 预演：只打印将要写入的内容
    python -m app.cli.notice_backfill                  # 正式回填
    python -m app.cli.notice_backfill --limit 100      # 只处理前 100 条存量行
    python -m app.cli.notice_backfill --json out.json  # 结果落盘（含逐条预览）

**退出码**（供 CI / 验收脚本判读）::

    0  执行完成（**未导入 `14_notice_extend.sql` 时也返回 0** —— 那是"无事可做"，
       不是失败；结果里的 `supported=false` 会说明原因）
    2  环境错误（C++ 扩展不可用 / 数据库连不上）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 允许 `python -m app.cli.notice_backfill` 在任意工作目录下运行
_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.cli.kb_import import ensure_db            # noqa: E402 - 复用 B17 的 CLI 环境初始化
from app.services.notice_ingest import backfill_notices  # noqa: E402

OK, FAIL, WARN = "✅", "❌", "⚠️"


def _fmt_preview(item: dict) -> str:
    fields = []
    if item.get("deadline"):
        fields.append(f"deadline={item['deadline']}")
    if item.get("materials"):
        materials = str(item["materials"])
        shown = materials if len(materials) <= 24 else materials[:24] + "…"
        fields.append(f"materials={shown}")
    if item.get("importance") is not None:
        fields.append(f"importance={item['importance']}")
    return f"  #{item.get('id')}  {str(item.get('title') or '')[:32]}  " + "  ".join(fields)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m app.cli.notice_backfill",
        description="通知扩展字段（deadline/materials/importance）存量回填（B30）",
    )
    ap.add_argument("--dry-run", action="store_true", help="只预览，不写库")
    ap.add_argument("--limit", type=int, default=0, help="最多处理多少条存量行（0=不限）")
    ap.add_argument("--json", default="", help="结果落盘路径")
    args = ap.parse_args(argv)

    print("=" * 78)
    print("通知扩展字段回填（B30）")
    print(f"  模式：{'预演（不写库）' if args.dry_run else '正式回填'}"
          "　｜　范围：三列有空值的行"
          f"{f'　｜　上限：{args.limit} 条' if args.limit else ''}")
    print("=" * 78)

    try:
        ensure_db()
    except RuntimeError as exc:
        print(f"{FAIL} 环境不可用：{exc}")
        return 2

    out = backfill_notices(limit=args.limit, dry_run=args.dry_run)

    if not out["supported"]:
        print(f"{WARN} campus_notice 没有扩展列（未导入 db/sql/14_notice_extend.sql）"
              "—— 无事可做，不是失败")
        print("=" * 78)
        return 0

    print(f"  扫描 {out['scanned']} 条；{'将更新' if args.dry_run else '已更新'} "
          f"{out['updated']} 条；无变化 {out['unchanged']} 条")
    if out["preview"]:
        print("  ---- 明细（前 20 条）----")
        for item in out["preview"]:
            print(_fmt_preview(item))
    for err in out["errors"]:
        print(f"  {WARN} 抽取失败（已跳过，不影响其它行）：{err}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"  结果已落盘：{args.json}")
    print("=" * 78)

    if out["errors"]:
        print(f"{WARN} 完成，但有 {len(out['errors'])} 处抽取失败（已跳过）")
    else:
        print(f"{OK} 回填完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
