"""`python -m app.collector` —— 采集 CLI（B26 合规自检 + B27 采集入库）。

`check`：**在真正开抓之前**，把配置里的每个采集源过一遍：

1. 参数是否合法（url 是 http/https、`rate_limit_qps` 非负）；
2. robots.txt 是否允许抓它（联网，除 `--offline`）；
3. 实际生效的抓取间隔是多少（`max(1/qps, Crawl-delay)`）。

`run`：按同一份配置**真正抓取并入库**（B27），全程过 B26 的闸门并留痕。

`check` 退出码：`0` = 所有启用中的源都可抓；`1` = 存在被 robots 拒绝或参数非法的源。
`run` 退出码：`0` = 每个源都没有错误；`1` = 至少一个源报错或被拒绝。
「列表页一条都没匹配到」（站点改版 / 选择器写错）默认只在 **stderr** 打 ⚠️ 而不判失败
—— 页面本来可能就是空的；要让定时任务据此报警就加 `--fail-on-empty`。

用法：
    cd backend
    # —— 合规自检（B26），不会写入任何数据 ——
    python -m app.collector check app/adapters/configs/xiaojietong.yml
    python -m app.collector check <config.yml> --offline        # 只校验参数，不联网
    python -m app.collector check <config.yml> --json report.json

    # —— 采集入库（B27）——
    python -m app.collector run <config.yml> --dry-run          # 试跑：抓取+解析，不写库
    python -m app.collector run <config.yml> --source jlu_oa --limit 10
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import FetchGuard
from .fetcher import HttpFetcher
from .journal import CollectJournal
from .limiter import RateLimiter
from .pipeline import NoticeStore, load_sources, run_config
from .robots import RobotsGate

OK, FAIL, WARN, SKIP = "✅", "❌", "⚠️", "⏭"


def _validate(src: dict[str, Any]) -> list[str]:
    """纯参数校验（离线可跑）。"""
    problems: list[str] = []
    key = str(src.get("key") or "<缺少 key>")
    url = str(src.get("url") or "")
    if not url.startswith(("http://", "https://")):
        problems.append(f"{key}: url 必须是 http/https，当前 {url!r}")
    qps = src.get("rate_limit_qps")
    if qps is not None:
        try:
            if float(qps) < 0:
                problems.append(f"{key}: rate_limit_qps 不能为负（{qps}）")
        except (TypeError, ValueError):
            problems.append(f"{key}: rate_limit_qps 不是数字（{qps!r}）")
    if "respect_robots" not in src:
        problems.append(f"{key}: 未显式声明 respect_robots（运行期按 true 处理，建议写出来）")
    return problems


def _effective_interval(qps: Any, crawl_delay: float | None) -> float:
    if qps is None:
        return 0.0
    return RateLimiter("probe", float(qps)).interval(crawl_delay)


def cmd_check(args: argparse.Namespace) -> int:
    path = Path(args.config)
    if not path.exists():
        print(f"{FAIL} 配置不存在：{path}")
        return 1
    sources = load_sources(path)
    if not sources:
        print(f"{FAIL} {path} 里没有 sources")
        return 1

    gate = RobotsGate(on_error=args.on_robots_error)
    journal = CollectJournal(path="")          # 自检不落盘
    rows: list[dict[str, Any]] = []
    bad = 0

    print("=" * 78)
    print(f"采集合规自检 · {path}")
    print(f"  robots 策略：取不到规则时 {'放行' if args.on_robots_error == 'allow' else '保守拒绝'}"
          f"　｜　模式：{'离线（不联网）' if args.offline else '联网'}")
    print("=" * 78)

    for src in sources:
        key = str(src.get("key") or "?")
        if not src.get("enabled", True):
            print(f"  {SKIP} {key:<20} 已停用，跳过")
            rows.append({"key": key, "status": "disabled"})
            continue

        problems = _validate(src)
        if problems:
            bad += 1
            for p in problems:
                print(f"  {FAIL} {p}")
            rows.append({"key": key, "status": "invalid", "problems": problems})
            continue

        url = str(src["url"])
        status = OK
        detail = ""
        crawl_delay: float | None = None
        if args.offline:
            detail = "未联网校验 robots"
            status = SKIP
        else:
            decision = gate.check(url)
            crawl_delay = decision.crawl_delay
            if not decision.allowed:
                status, detail = FAIL, decision.reason
                bad += 1
            else:
                extra = f"，Crawl-delay={crawl_delay}s" if crawl_delay else ""
                detail = f"{decision.reason}{extra}"

        interval = _effective_interval(src.get("rate_limit_qps"), crawl_delay)
        qps_txt = src.get("rate_limit_qps", "—")
        print(f"  {status} {key:<20} qps={qps_txt:<6} 有效间隔≈{interval:.2f}s　{detail}")
        rows.append({
            "key": key, "url": url, "status": status, "detail": detail,
            "rate_limit_qps": src.get("rate_limit_qps"),
            "crawl_delay": crawl_delay, "effective_interval_s": round(interval, 3),
        })

    journal.record(source="-", url=str(path), phase="robots",
                   outcome="ok" if bad == 0 else "blocked",
                   detail=f"合规自检：{len(rows)} 个源，{bad} 个问题")

    print("-" * 78)
    print(f"共 {len(rows)} 个源；问题 {bad} 个")
    if args.json:
        Path(args.json).write_text(
            json.dumps({"config": str(path), "problems": bad, "sources": rows},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告已落盘：{args.json}")
    print("=" * 78)

    if bad:
        print(f"{FAIL} 存在不可抓取/非法的源，先修配置再开采集")
        return 1
    print(f"{OK} 全部启用中的源均可抓取")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """B27：按配置真正抓取并入库（每一条请求都过 B26 的闸门并留痕）。"""
    path = Path(args.config)
    if not path.exists():
        print(f"{FAIL} 配置不存在：{path}")
        return 1

    guard = FetchGuard(default_qps=args.qps, on_robots_error=args.on_robots_error)
    fetcher = HttpFetcher(timeout=args.timeout, user_agent=guard.user_agent)

    print("=" * 78)
    print(f"采集执行 · {path}")
    print(f"  模式：{'试跑 DRY-RUN（不写库）' if args.dry_run else '正式入库'}"
          f"　｜　每源上限：{args.limit} 条"
          f"　｜　默认 qps：{args.qps}")
    print(f"  合规：robots 取不到时 {'放行' if args.on_robots_error == 'allow' else '保守拒绝'}"
          f"　｜　日志：{guard.journal.path or '（未落盘）'}")
    print("=" * 78)

    results = run_config(
        path, guard=guard, fetcher=fetcher,
        store=None if args.dry_run else NoticeStore(),
        only=args.source, limit=args.limit, dry_run=args.dry_run,
    )
    if not results:
        print(f"{FAIL} 没有匹配的启用源（--source 是否写错？）")
        return 1

    bad = 0
    empty = 0
    for r in results:
        if r.blocked:
            print(f"  {FAIL} {r.key:<20} 被拒绝：{r.blocked}")
        else:
            # 空列表用 ⚠️ 而不是 ✅：退出码语义不变，但别让一个"✅"把上面的告警盖过去
            mark = FAIL if not r.ok else (WARN if r.empty_list else OK)
            print(f"  {mark} {r.key:<20} 列表 {r.listed} 条 → 详情 {r.fetched} 条 → "
                  f"新增 {r.inserted}，跳过 {r.skipped}")
        for err in r.errors:
            print(f"        · {err}")
        if not r.ok:
            bad += 1
        if r.empty_list:
            # 走 stderr：退出码可能仍是 0，但日志里必须留下显式痕迹，
            # 否则站点改版后采集会**安静地停止工作**而无人察觉。
            empty += 1
            print(f"  {WARN} {r.key:<20} 列表选择器匹配到 0 条 —— 确认站点结构未变"
                  f"（selectors.list: {r.list_selector or '（未配置）'}）", file=sys.stderr)

    print("-" * 78)
    print(f"共 {len(results)} 个源；新增 {sum(r.inserted for r in results)} 条；"
          f"异常 {bad} 个" + (f"；空列表 {empty} 个" if empty else ""))
    if args.json:
        Path(args.json).write_text(
            json.dumps({"config": str(path), "dry_run": args.dry_run,
                        "sources": [r.as_dict() for r in results]},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"结果已落盘：{args.json}")
    print("=" * 78)

    if bad or (args.fail_on_empty and empty):
        print(f"{FAIL} 有源执行失败，详见上方错误与采集日志")
        return 1
    if empty:
        print(f"{WARN} 有 {empty} 个源列表为空 —— 已按非失败处理；"
              f"定时任务可加 --fail-on-empty 据此报警")
    print(f"{OK} 采集完成")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m app.collector", description="采集工具（B26 合规自检 / B27 采集入库）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    check = sub.add_parser("check", help="检查采集源是否可抓（robots + 参数）")
    check.add_argument("config", help="学校适配器配置（yml/yaml/json）")
    check.add_argument("--offline", action="store_true", help="只校验参数，不联网读 robots.txt")
    check.add_argument("--on-robots-error", choices=("block", "allow"), default="block",
                       help="robots.txt 取不到时的策略（默认 block，保守）")
    check.add_argument("--json", default="", help="自检报告落盘路径")
    check.set_defaults(func=cmd_check)

    run = sub.add_parser("run", help="按配置抓取并入库（B27）")
    run.add_argument("config", help="学校适配器配置（yml/yaml/json）")
    run.add_argument("--source", action="append", default=[], metavar="KEY",
                     help="只跑指定的源 key（可重复；默认跑全部启用源）")
    run.add_argument("--limit", type=int, default=20, help="每个源最多抓多少条详情（默认 20）")
    run.add_argument("--dry-run", action="store_true", help="试跑：抓取并解析，但不写数据库")
    run.add_argument("--fail-on-empty", action="store_true",
                     help="列表页一条都没匹配到时以退出码 1 结束（默认只在 stderr 告警）")
    run.add_argument("--timeout", type=float, default=10.0, help="单次请求超时秒数（默认 10）")
    run.add_argument("--qps", type=float, default=0.5,
                     help="源里没写 rate_limit_qps 时用的默认值（默认 0.5）")
    run.add_argument("--on-robots-error", choices=("block", "allow"), default="block",
                     help="robots.txt 取不到时的策略（默认 block，保守）")
    run.add_argument("--json", default="", help="采集结果落盘路径")
    run.set_defaults(func=cmd_run)

    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
