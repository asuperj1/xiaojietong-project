#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原型预览本地化 + 总览页生成。

`ui/prototype/*.html` 是 18 个手机尺寸的高保真原型（Tailwind + Iconify），
但它们引用的是外网 CDN（`modao.cc`）——**离线或网络受限时样式全丢**。

本脚本做三件事：
  1. 下载 CDN 依赖到 `ui/prototype/vendor/`（走本机代理，可用 `--proxy ""` 关闭）
  2. 把 18 个 HTML 中的 CDN 引用改写为本地相对路径（幂等）
  3. 生成 `ui/prototype/_all.html` 总览页（网格缩略 + 点击放大）

用法：
    E:/miniconda3/python.exe -X utf8 tools/prototype_preview.py
    E:/miniconda3/python.exe -X utf8 tools/prototype_preview.py --proxy ""     # 不走代理
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

import httpx

# 根目录由脚本位置推导（不再写死作者机器路径）
ROOT = pathlib.Path(__file__).resolve().parent.parent / "ui" / "prototype"

CDN = [
    ("https://modao.cc/agent-py/static/source/js/tailwindcss.js", "tailwindcss.js"),
    ("https://modao.cc/agent-py/static/source/js/iconify-icon.min.js", "iconify-icon.min.js"),
]
# 备用源（主源失败时依次尝试）
FALLBACK = {
    "tailwindcss.js": [
        "https://cdn.tailwindcss.com",
        "https://unpkg.com/tailwindcss@3.4.1/lib/index.js",
    ],
    "iconify-icon.min.js": [
        "https://code.iconify.design/iconify-icon/1.0.8/iconify-icon.min.js",
        "https://unpkg.com/iconify-icon@1.0.8/dist/iconify-icon.min.js",
    ],
}


def fetch(client: httpx.Client, urls: list[str], name: str) -> bytes | None:
    for u in urls:
        try:
            r = client.get(u, timeout=90, follow_redirects=True)
            if r.status_code == 200 and len(r.content) > 1000:
                print(f"    ✅ {name}  <- {u}  ({len(r.content) // 1024} KB)")
                return r.content
            print(f"    ⚠️ {name}  <- {u}  HTTP {r.status_code}")
        except Exception as exc:  # noqa: BLE001
            print(f"    ⚠️ {name}  <- {u}  {type(exc).__name__}: {str(exc)[:80]}")
    return None


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy", default=os.environ.get("XJT_PROXY") or None,
                    help="代理地址（默认读环境变量 XJT_PROXY；不设则直连）")
    args = ap.parse_args()

    print("=" * 74)
    print("原型预览本地化 + 总览页生成")
    print("=" * 74)

    vendor = ROOT / "vendor"
    vendor.mkdir(parents=True, exist_ok=True)

    # ---------- 1. 下载依赖 ----------
    print("\n1 · 下载 CDN 依赖")
    kwargs = {"trust_env": False}
    if args.proxy:
        kwargs["proxy"] = args.proxy
    ok_files: list[str] = []
    with httpx.Client(**kwargs) as client:  # type: ignore[arg-type]
        for url, name in CDN:
            target = vendor / name
            if target.exists() and target.stat().st_size > 1000:
                print(f"    ⏭ {name} 已存在（{target.stat().st_size // 1024} KB），跳过")
                ok_files.append(name)
                continue
            data = fetch(client, [url] + FALLBACK.get(name, []), name)
            if data:
                target.write_bytes(data)
                ok_files.append(name)

    # ---------- 2. 改写引用 ----------
    print("\n2 · 改写 HTML 中的 CDN 引用为本地路径")
    pages = sorted(p for p in ROOT.glob("*.html") if not p.name.startswith("_"))
    rules = [
        (re.compile(r"https://modao\.cc/agent-py/static/source/js/tailwindcss\.js"), "vendor/tailwindcss.js"),
        (re.compile(r"https://cdn\.tailwindcss\.com"), "vendor/tailwindcss.js"),
        (re.compile(r"https://modao\.cc/agent-py/static/source/js/iconify-icon\.min\.js"), "vendor/iconify-icon.min.js"),
        (re.compile(r"https://code\.iconify\.design/[^\"']+"), "vendor/iconify-icon.min.js"),
    ]
    changed = 0
    utf8 = "utf-8"
    for p in pages:
        txt = p.read_text(encoding=utf8, errors="replace")
        orig = txt
        for pat, rep in rules:
            txt = pat.sub(rep, txt)
        if txt != orig:
            p.write_text(txt, encoding=utf8)
            changed += 1
    print(f"    ✅ 共 {len(pages)} 个页面，改写 {changed} 个")

    # 统计仍存在的外链（图片等）
    external = {}
    for p in pages:
        for m in re.finditer(r'(?:src|href)="(https?://[^"]+)"', p.read_text(encoding=utf8, errors="replace")):
            host = m.group(1).split("/")[2]
            external[host] = external.get(host, 0) + 1
    if external:
        print("    ⚠️ 仍有外链资源（离线时不显示）：")
        for host, n in sorted(external.items(), key=lambda x: -x[1]):
            print(f"       {host}  ×{n}")

    # ---------- 3. 生成总览页 ----------
    print("\n3 · 生成总览页 _all.html")
    cards = []
    for p in pages:
        txt = p.read_text(encoding=utf8, errors="replace")
        m = re.search(r"<title>(.*?)</title>", txt, re.S)
        title = (m.group(1).strip() if m else p.stem)
        cards.append(f'''    <div class="card" onclick="window.open('{p.name}','_blank')">
      <div class="frame"><iframe src="{p.name}" loading="lazy" scrolling="no"></iframe></div>
      <div class="meta"><span class="name">{title}</span><span class="file">{p.name}</span></div>
    </div>''')

    html = f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<title>校捷通 · 前端页面原型总览（{len(pages)} 页）</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #0f172a; color: #e2e8f0;
         font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; }}
  header {{ padding: 28px 32px 12px; }}
  h1 {{ margin: 0 0 6px; font-size: 26px; }}
  .sub {{ color: #94a3b8; font-size: 13px; line-height: 1.7; }}
  .sub code {{ background: #1e293b; padding: 1px 6px; border-radius: 4px; color: #7dd3fc; }}
  .grid {{ display: grid; gap: 22px; padding: 20px 32px 48px;
           grid-template-columns: repeat(auto-fill, minmax(158px, 1fr)); }}
  .card {{ background: #1e293b; border-radius: 14px; overflow: hidden; cursor: pointer;
           border: 1px solid #334155; transition: .18s; }}
  .card:hover {{ transform: translateY(-4px); border-color: #38bdf8;
                 box-shadow: 0 10px 30px rgba(56,189,248,.25); }}
  .frame {{ width: 100%; height: 268px; overflow: hidden; background: #fff; position: relative; }}
  .frame iframe {{ width: 375px; height: 812px; border: 0;
                   transform: scale(.4); transform-origin: 0 0; }}
  .meta {{ padding: 9px 11px; display: flex; flex-direction: column; gap: 2px; }}
  .name {{ font-size: 13px; font-weight: 600; color: #f1f5f9; }}
  .file {{ font-size: 11px; color: #64748b; font-family: ui-monospace, monospace; }}
</style></head>
<body>
<header>
  <h1>校捷通 · 前端页面原型总览</h1>
  <div class="sub">
    共 <b>{len(pages)}</b> 个手机尺寸原型（375×812）｜ 点击卡片在新标签打开完整页面<br/>
    访问：<code>http://127.0.0.1:5500/xiaojietong-project/ui/prototype/_all.html</code>
    （需先用 Live Server 启动，根目录为工作区根）
  </div>
</header>
<div class="grid">
{chr(10).join(cards)}
</div>
</body></html>
'''
    (ROOT / "_all.html").write_text(html, encoding=utf8)
    print(f"    ✅ 已生成 _all.html（{len(pages)} 个页面卡片）")

    # ---------- 汇总 ----------
    print("\n" + "=" * 74)
    print(f"依赖：{len(ok_files)}/2 已就绪 {ok_files}")
    print("打开总览：http://127.0.0.1:5500/xiaojietong-project/ui/prototype/_all.html")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
