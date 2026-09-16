"""`python -m app.collector` —— 采集前合规自检（B26）。

用途：**在真正开抓之前**，把配置里的每个采集源过一遍：

1. 参数是否合法（url 是 http/https、`rate_limit_qps` 非负）；
2. robots.txt 是否允许抓它（联网，除 `--offline`）；
3. 实际生效的抓取间隔是多少（`max(1/qps, Crawl-delay)`）。

退出码：`0` = 所有启用中的源都可抓；`1` = 存在被 robots 拒绝或参数非法的源。

用法：
    cd backend
    python -m app.collector check app/adapters/configs/xiaojietong.yml
    python -m app.collector check <config.yml> --offline        # 只校验参数，不联网
    python -m app.collector check <config.yml> --json report.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .journal import CollectJournal
from .limiter import RateLimiter
from .robots import RobotsGate

OK, FAIL, SKIP = "✅", "❌", "⏭"


def _load_sources(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yml", ".yaml"):
        try:
            import yaml  # noqa: PLC0415 - 按需导入，JSON 配置时无需 PyYAML
        except ImportError as exc:  # pragma: no cover
            raise SystemExit(f"读取 YAML 需要 PyYAML：{exc}") from exc
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise SystemExit(f"配置顶层必须是对象：{path}")
    sources = data.get("sources") or []
    if not isinstance(sources, list):
        raise SystemExit(f"{path} 的 sources 必须是数组")
    return [s for s in sources if isinstance(s, dict)]


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
    sources = _load_sources(path)
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.collector", description="采集合规工具（B26）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    check = sub.add_parser("check", help="检查采集源是否可抓（robots + 参数）")
    check.add_argument("config", help="学校适配器配置（yml/yaml/json）")
    check.add_argument("--offline", action="store_true", help="只校验参数，不联网读 robots.txt")
    check.add_argument("--on-robots-error", choices=("block", "allow"), default="block",
                       help="robots.txt 取不到时的策略（默认 block，保守）")
    check.add_argument("--json", default="", help="自检报告落盘路径")
    check.set_defaults(func=cmd_check)

    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
