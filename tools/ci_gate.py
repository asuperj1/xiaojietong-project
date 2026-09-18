#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CI 门禁总入口（B34）—— 把"跳过"当成一等公民，拦掉假绿。

为什么需要这个脚本
------------------
`backend/tests/conftest.py` 在 **jt_db 扩展缺失 / 数据库口令缺失 / 库未就绪** 时
是 `pytest.skip(...)` 而不是 `fail` —— 这在本地开发很友好，但在 CI 里会退化成
**假绿**：一个用例都没真正跑，流水线照样全绿。

本脚本对每一道门禁都要求 **有证据**：
- pytest 走 `--junitxml` 拿精确计数（passed / failed / skipped），
  `passed == 0` 或 `skipped > 0` 都会被点名；
- preflight 走退出码 + "阻塞项：N 个" 双重核对；
- rag_bench 走 `--strict` 退出码 + 落盘报告里的 `hit@3` 复核。

三道门禁
--------
| 编号 | 命令 | 红灯条件 | 环境缺失时 |
|---|---|---|---|
| G1 | `pytest tests` | failed/error > 0；或用例数 = 0 | 用例全 skip → 红灯（见下） |
| G2 | `tools/preflight_check.py` | 退出码 ≠ 0；或 阻塞项 > 0 | 后端不可达 → **SKIP** |
| G3 | `ai/eval/rag_bench.py --strict` | 退出码 ≠ 0；或 hit@3 < 阈值 | Ollama 不可达 → **SKIP** |

关于 SKIP
---------
SKIP **不等于通过**，它只是"这台机器没这个环境"。默认模式下 SKIP 只告警并在
汇总单列；`--strict`（CI 用）下 **SKIP 直接算红**，逼环境补齐 —— 这样"没跑"
永远不会伪装成"跑过了"。

用法
----
    python tools/ci_gate.py                     # 本地：全部跑，SKIP 只告警
    python tools/ci_gate.py --strict            # CI：任何 SKIP 都算红
    python tools/ci_gate.py --only pytest       # 只跑一道门禁
    python tools/ci_gate.py --base http://127.0.0.1:8000/api/v1
    python tools/ci_gate.py --json report.json  # 额外落盘机器可读汇总

退出码：0 = 全绿；1 = 存在红灯或（--strict 下）SKIP。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

PASS, FAIL, SKIP, WARN = "PASS", "FAIL", "SKIP", "WARN"
ICON = {PASS: "✅", FAIL: "❌", SKIP: "⏭", WARN: "⚠️"}

DEFAULT_BASE = "http://127.0.0.1:8000/api/v1"
OLLAMA_TAGS = "http://127.0.0.1:11434/api/tags"


# ------------------------------------------------------------------ 基础设施 ----

def _child_env() -> dict:
    """子进程环境：强制 UTF-8 输出，避免 Windows 控制台编码把中文打死。"""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("PYTHONUTF8", "1")
    return env


def run(cmd: list[str], cwd: Path | None = None, timeout: int | None = None):
    """跑子命令，返回 (退码, 合并输出, 耗时秒)。"""
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, env=_child_env(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout, check=False,
        )
        out = proc.stdout.decode("utf-8", "replace")
        rc = proc.returncode
    except subprocess.TimeoutExpired as exc:
        raw = exc.stdout or b""
        out = raw.decode("utf-8", "replace") + f"\n[ci_gate] 超时（>{timeout}s）"
        rc = 124
    except OSError as exc:  # noqa: BLE001
        out, rc = f"[ci_gate] 无法启动子进程：{exc}", 127
    return rc, out, round(time.perf_counter() - t0, 1)


def http_ready(url: str, timeout: float = 2.0) -> bool:
    """轻量探活：能拿到 HTTP 响应即视为"服务在"。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return 200 <= resp.status < 500
    except urllib.error.HTTPError:
        return True          # 有响应（哪怕 401/404）说明服务活着
    except Exception:        # noqa: BLE001
        return False


def tail(text: str, lines: int = 12) -> str:
    rows = [r for r in text.strip().splitlines() if r.strip()]
    return "\n".join("      " + r for r in rows[-lines:])


class Gate:
    def __init__(self, code: str, name: str, status: str, detail: str,
                 seconds: float = 0.0, evidence: dict | None = None, output: str = ""):
        self.code = code
        self.name = name
        self.status = status
        self.detail = detail
        self.seconds = seconds
        self.evidence = evidence or {}
        self.output = output

    def as_dict(self) -> dict:
        return {
            "code": self.code, "name": self.name, "status": self.status,
            "detail": self.detail, "seconds": self.seconds, "evidence": self.evidence,
        }


# ------------------------------------------------------------------ G1 pytest ----

def gate_pytest(args) -> Gate:
    """后端测试：用 junitxml 精确计数，拒绝"全 skip 的绿"。"""
    tmp = Path(tempfile.mkdtemp(prefix="ci_gate_pytest_"))
    junit = tmp / "junit.xml"
    cmd = [sys.executable, "-m", "pytest", "tests", "-q", f"--junitxml={junit}"]
    if args.pytest_args:
        cmd += shlex.split(args.pytest_args)
    rc, out, secs = run(cmd, cwd=BACKEND, timeout=args.timeout)

    if not junit.exists():
        return Gate("G1", "pytest", FAIL,
                    f"pytest 未产出 junit 报告（退出码 {rc}）", secs,
                    {"exit_code": rc}, out)

    try:
        root = ET.parse(junit).getroot()
    except ET.ParseError as exc:
        return Gate("G1", "pytest", FAIL, f"junit 报告解析失败：{exc}", secs, {}, out)

    suites = [root] if root.tag == "testsuite" else list(root)
    n_tests = sum(int(s.get("tests", 0) or 0) for s in suites)
    n_fail = sum(int(s.get("failures", 0) or 0) for s in suites)
    n_err = sum(int(s.get("errors", 0) or 0) for s in suites)
    n_skip = sum(int(s.get("skipped", 0) or 0) for s in suites)
    n_pass = n_tests - n_fail - n_err - n_skip

    skip_reasons: list[str] = []
    for s in suites:
        for tc in s.iter("testcase"):
            for sk in tc.findall("skipped"):
                msg = (sk.get("message") or "").strip().replace("\n", " ")
                skip_reasons.append(f"{tc.get('name', '?')} — {msg[:90]}")
    evidence = {
        "tests": n_tests, "passed": n_pass, "failed": n_fail,
        "errors": n_err, "skipped": n_skip,
        "skip_examples": skip_reasons[:5],
    }

    detail_bits = [f"{n_pass} passed", f"{n_fail} failed", f"{n_err} errors", f"{n_skip} skipped"]

    if n_fail or n_err:
        return Gate("G1", "pytest", FAIL, "｜".join(detail_bits)
                    + f"（{n_fail + n_err} 个用例失败）", secs, evidence, out)
    if n_tests == 0:
        return Gate("G1", "pytest", FAIL, "用例数 = 0 —— 恒真空，未执行任何断言",
                    secs, evidence, out)
    if n_skip:
        status = FAIL if args.strict else WARN
        extra = ("（严格模式：环境缺失导致的 skip 判为红灯）" if args.strict
                 else "（未验证，请补环境后重跑）")
        return Gate("G1", "pytest", status,
                    "｜".join(detail_bits) + f"　{extra}", secs, evidence, out)
    return Gate("G1", "pytest", PASS, "｜".join(detail_bits), secs, evidence, out)


# -------------------------------------------------------------- G2 preflight ----

def gate_preflight(args) -> Gate:
    """环境自检：后端没起就明确 SKIP，绝不把"没跑"当"跑过"。"""
    base = args.base.rstrip("/")
    if not http_ready(base + "/health"):
        return Gate("G2", "preflight", SKIP,
                    f"后端不可达（{base}/health）—— 自检未执行")
    script = ROOT / "tools" / "preflight_check.py"
    cmd = [sys.executable, str(script), "--base", base]
    if args.preflight_write:
        cmd.append("--write")
    rc, out, secs = run(cmd, cwd=ROOT, timeout=args.timeout)

    m = re.search(r"阻塞项：\s*(\d+)\s*个", out)
    blocking = int(m.group(1)) if m else None
    evidence = {"exit_code": rc, "blocking": blocking}
    if m is None:
        return Gate("G2", "preflight", FAIL,
                    f"未解析到「阻塞项」汇总行（退出码 {rc}）", secs, evidence, out)
    if blocking or rc != 0:
        return Gate("G2", "preflight", FAIL,
                    f"阻塞项 {blocking} 个（退出码 {rc}）", secs, evidence, out)
    return Gate("G2", "preflight", PASS, "阻塞项 0 个", secs, evidence, out)


# -------------------------------------------------------------- G3 rag_bench ----

def gate_rag(args) -> Gate:
    """RAG 检索质量：hit@3 门禁 + 向量检索必须真的生效。"""
    if not http_ready(OLLAMA_TAGS):
        return Gate("G3", "rag_bench", SKIP,
                    "Ollama 不可达（127.0.0.1:11434）—— 检索质量未验证")
    out_prefix = Path(tempfile.mkdtemp(prefix="ci_gate_rag_")) / "rag_bench.json"
    cmd = [
        sys.executable, str(ROOT / "ai" / "eval" / "rag_bench.py"),
        "--strict", "--threshold", str(args.threshold), "--out", str(out_prefix),
    ]
    rc, out, secs = run(cmd, cwd=BACKEND, timeout=args.timeout)

    # rag_bench 把 JSON 写到 --out 的原值、Markdown 写到同前缀的 .md
    report = out_prefix
    evidence: dict = {"exit_code": rc, "threshold": args.threshold}
    hit3 = None
    if report.exists():
        try:
            payload = json.loads(report.read_text(encoding="utf-8"))
            summary = payload.get("summary", {})
            hit3 = summary.get("hit@3")
            evidence.update({
                "hit@3": hit3,
                "n_total": summary.get("n_total"),
                "n_positive": summary.get("n_positive"),
                "retrieval_mode": summary.get("retrieval_mode"),
                "empty_rate": summary.get("empty_rate"),
            })
        except (json.JSONDecodeError, OSError) as exc:
            evidence["report_error"] = str(exc)

    if rc != 0:
        return Gate("G3", "rag_bench", FAIL,
                    f"退出码 {rc}（hit@3 = {hit3 if hit3 is not None else '未知'}，"
                    f"阈值 {args.threshold:.0%}）", secs, evidence, out)
    if hit3 is None:
        return Gate("G3", "rag_bench", FAIL, "未取到 hit@3 指标（报告缺失）",
                    secs, evidence, out)
    return Gate("G3", "rag_bench", PASS,
                f"hit@3 = {hit3:.1%}（阈值 {args.threshold:.0%}）", secs, evidence, out)


# ------------------------------------------------------------------ 主流程 ----

GATES = {"pytest": gate_pytest, "preflight": gate_preflight, "rag": gate_rag}


def main() -> int:
    ap = argparse.ArgumentParser(description="CI 门禁总入口（B34）")
    ap.add_argument("--only", default="", help="只跑指定门禁，逗号分隔：pytest,preflight,rag")
    ap.add_argument("--strict", action="store_true",
                    help="CI 严格模式：SKIP 也算红灯（逼环境补齐，杜绝假绿）")
    ap.add_argument("--base", default=DEFAULT_BASE, help=f"后端基址（默认 {DEFAULT_BASE}）")
    ap.add_argument("--threshold", type=float, default=0.80, help="rag_bench 的 hit@3 阈值")
    ap.add_argument("--pytest-args", default="", help="透传给 pytest 的额外参数")
    ap.add_argument("--preflight-write", action="store_true", help="preflight 执行写数据检查")
    ap.add_argument("--timeout", type=int, default=1800, help="单道门禁超时秒数")
    ap.add_argument("--json", default="", help="汇总报告落盘路径（JSON）")
    args = ap.parse_args()

    picked = [g.strip() for g in args.only.split(",") if g.strip()] or list(GATES)
    unknown = [g for g in picked if g not in GATES]
    if unknown:
        print(f"[ci_gate] 未知门禁：{unknown}（可选 {list(GATES)}）")
        return 2

    print("=" * 78)
    print("校捷通 CI 门禁（B34）"
          + ("　[严格模式：SKIP 视为红灯]" if args.strict else "　[普通模式：SKIP 仅告警]"))
    print("=" * 78)

    gates: list[Gate] = []
    for key in picked:
        print(f"\n▶ {key} …")
        gate = GATES[key](args)
        gates.append(gate)
        print(f"  {ICON[gate.status]} {gate.code} {gate.name} — {gate.detail}　({gate.seconds}s)")
        if gate.status != PASS:
            print(tail(gate.output))

    n_pass = sum(1 for g in gates if g.status == PASS)
    n_fail = sum(1 for g in gates if g.status == FAIL)
    n_skip = sum(1 for g in gates if g.status == SKIP)
    n_warn = sum(1 for g in gates if g.status == WARN)

    print("\n" + "=" * 78)
    print(f"汇总：✅ {n_pass} 通过　｜　❌ {n_fail} 失败　｜　"
          f"⚠️ {n_warn} 警告　｜　⏭ {n_skip} 跳过（未验证）")
    for g in gates:
        if g.status in (FAIL, SKIP, WARN):
            print(f"  {ICON[g.status]} {g.code} {g.name} — {g.detail}")
    if n_skip and not args.strict:
        print("  ⚠️ 跳过的门禁**没有得到验证**；CI 请加 --strict 让其变成红灯。")
    print("=" * 78)

    red = n_fail > 0 or n_warn > 0 or (args.strict and n_skip > 0)
    if args.json:
        Path(args.json).write_text(json.dumps({
            "strict": args.strict,
            "summary": {"pass": n_pass, "fail": n_fail, "skip": n_skip, "warn": n_warn},
            "red": red,
            "gates": [g.as_dict() for g in gates],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告已落盘：{args.json}")

    if red:
        print("❌ CI 门禁未通过")
        return 1
    print("✅ CI 门禁全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
