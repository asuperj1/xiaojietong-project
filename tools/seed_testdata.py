#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""封测业务种子数据（通过接口创建，走真实业务链路）。

与 `db/sql/99f_beta_seed.sql` 的分工
------------------------------------
* `99f_beta_seed.sql`：**静态字典数据**（公司 / 兼职岗位 / 校园通知）—— 无创建接口，只能走 SQL
* 本脚本：**动态业务数据**（二手 / 论坛 / 求购 / 收藏 / 标签）—— 有接口，走业务链路更真实，
  并且顺带覆盖**审核闭环**：敏感词帖应被拒（3003）、待审词帖应入库为待审。

用法
----
    E:/miniconda3/python.exe -X utf8 tools/seed_testdata.py
    E:/miniconda3/python.exe -X utf8 tools/seed_testdata.py --base http://192.168.1.10:8000/api/v1

注意
----
* 可重复执行（每次会**追加**数据；若要干净环境请先重建库或改用独立测试库）
* 必须用带 httpx 的解释器（如 `E:/miniconda3/python.exe`）
"""
from __future__ import annotations

import argparse
import sys

import httpx

OK, FAIL, INFO = "✅", "❌", "·"
ROWS: list[tuple[str, str, str, str]] = []

# 账号：不同 code → 不同 openid → 不同账号（后端 mock 登录按 code 派生 openid）
ACCOUNTS = {
    "甲": "seed-user-alpha",
    "乙": "seed-user-beta",
    "丙": "seed-user-gamma",
}

# ---------- 内容数据 ----------
SECONDHAND_A = [
    ("考研数学真题册（张宇 1000 题）", "九成新，仅做过前 3 章，无笔记。", 25.0, "教材"),
    ("宿舍护眼台灯（三档调光）", "用了半年，功能完好，因毕业出。", 35.0, "生活"),
    ("捷安特山地车（21 速）", "骑行一年，链条新换，车锁一并送。", 420.0, "其他"),
]
SECONDHAND_B = [
    ("数据结构教材（严蔚敏版）", "无划线，附实验报告模板。", 18.0, "教材"),
    ("罗技无线鼠标 M170", "用过两个月，包装在。", 45.0, "数码"),
]
TOPICS_A = [
    ("图书馆三楼自习体验分享", "三楼东侧靠窗位置采光好，插座也多，推荐早去占座。", "学习"),
    ("求推荐靠谱的考研英语网课", "基础一般，想要讲解细致的老师，谢谢！", "学习"),
    ("学校周边好吃的麻辣烫求推荐", "想换换口味，最好是步行 15 分钟内的。", "生活"),
]
TOPICS_B = [
    ("二手交易避坑经验", "尽量线下当面交易，拍清楚瑕疵部位，避免纠纷。", "二手"),
    ("周末有人一起打羽毛球吗", "体育馆场地已约好，缺两个人。", "生活"),
]
# 敏感词帖：用于验证审核闭环（预期被拒 / 转待审）
TOPICS_SENSITIVE = [
    ("帮忙代考高数，价格可议", "有偿代考，请联系我。", "其他"),          # 预期 3003 拒绝
    ("想找人私聊交流考研资料", "有意者私聊我，资料很多。", "学习"),        # 预期转待审
]
WISH = ("求购二手显示器 24 寸", "其他", 300.0)


def rec(kind: str, name: str, status: str, note: str = "") -> None:
    ROWS.append((kind, name, status, note))
    line = f"  {status} [{kind}] {name}"
    if note:
        line += f"  — {note}"
    print(line)


def login(c: httpx.Client, code: str) -> str | None:
    try:
        b = c.post("/auth/wechat-login", json={"code": code}).json()
        return ((b.get("data") or {}).get("token"))
    except Exception:  # noqa: BLE001
        return None


def call(c: httpx.Client, method: str, path: str, h: dict, body=None, label="", kind=""):
    """发请求并返回 (http, code, message, data)。"""
    try:
        r = c.request(method, path, json=body, headers=h)
        try:
            b = r.json()
        except Exception:
            return r.status_code, None, f"非 JSON：{r.text[:120]}", None
        return r.status_code, b.get("code"), b.get("message"), b.get("data")
    except Exception as exc:  # noqa: BLE001
        return -1, None, f"异常 {type(exc).__name__}: {exc}", None


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000/api/v1")
    args = ap.parse_args()

    print("=" * 78)
    print("校捷通 · 封测业务种子数据")
    print(f"目标：{args.base}")
    print("=" * 78)

    c = httpx.Client(base_url=args.base, timeout=60, trust_env=False)

    # ---------- 0. 登录 ----------
    print("\n0 · 登录账号")
    tokens: dict[str, str] = {}
    for name, code in ACCOUNTS.items():
        t = login(c, code)
        if t:
            tokens[name] = t
            rec("账号", name, OK, f"code={code}")
        else:
            rec("账号", name, FAIL, "登录失败（后端未启动？）")
    if "甲" not in tokens or "乙" not in tokens:
        print("\n❌ 关键账号登录失败，终止。请先启动后端。")
        return 1

    Ha = {"Authorization": f"Bearer {tokens['甲']}"}
    Hb = {"Authorization": f"Bearer {tokens['乙']}"}

    # ---------- 1. 标签 ----------
    print("\n1 · 兴趣标签（用于推荐/通知精准推送）")
    for name, tags, h in (("甲", ["考研", "二手"], Ha), ("乙", ["兼职", "生活"], Hb)):
        http, code, msg, _ = call(c, "PUT", "/user/tags", h, {"tags": tags})
        rec("标签", name, OK if code == 0 else FAIL, f"tags={tags} code={code}" + (f" {msg}" if code != 0 else ""))

    # ---------- 2. 二手发布 ----------
    print("\n2 · 二手商品（在售状态；A 分册列表/详情依赖）")
    item_ids: list[int] = []
    for owner, items, h in (("甲", SECONDHAND_A, Ha), ("乙", SECONDHAND_B, Hb)):
        for title, desc, price, cat in items:
            body = {
                "title": title,
                "description": desc,
                "price": price,
                "category": cat,
                "images": [],
                "condition_level": 9,
            }
            http, code, msg, data = call(c, "POST", "/secondhand/items", h, body, title, "二手")
            if code == 0:
                iid = (data or {}).get("item_id")
                item_ids.append(iid or 0)
                rec("二手", f"{owner} · {title}", OK, f"item_id={iid}")
            else:
                rec("二手", f"{owner} · {title}", FAIL, f"HTTP {http} code={code} {msg}")

    # ---------- 3. 帖子的（含敏感词验证） ----------
    print("\n3 · 论坛帖子（干净帖 + 敏感词帖，验证审核闭环）")
    topic_ids: list[int] = []
    for owner, topics, h in (("甲", TOPICS_A, Ha), ("乙", TOPICS_B, Hb)):
        for title, content, cat in topics:
            http, code, msg, data = call(c, "POST", "/topics", h,
                                         {"title": title, "content": content, "category": cat})
            if code == 0:
                tid = (data or {}).get("topic_id") or (data or {}).get("id")
                audit = (data or {}).get("audit_status")
                topic_ids.append(tid or 0)
                rec("帖子", f"{owner} · {title}", OK, f"topic_id={tid} audit={audit}")
            else:
                rec("帖子", f"{owner} · {title}", FAIL, f"HTTP {http} code={code} {msg}")

    print("\n3.1 · 敏感词帖（**预期被拒 / 转待审**，失败即代表审核异常）")
    for title, content, cat in TOPICS_SENSITIVE:
        http, code, msg, data = call(c, "POST", "/topics", Ha,
                                     {"title": title, "content": content, "category": cat})
        if code == 3003:
            rec("审核", title, OK, f"已被拒绝 code=3003（预期行为）")
        elif code == 0:
            audit = (data or {}).get("audit_status")
            rec("审核", title, OK if audit == 0 else FAIL,
                f"已入库 audit_status={audit}（预期 0=待审）")
        else:
            rec("审核", title, FAIL, f"HTTP {http} code={code} {msg}")

    # ---------- 4. 求购 ----------
    print("\n4 · 求购需求（AI 供需匹配用）")
    http, code, msg, data = call(c, "POST", "/secondhand/wishes", Ha,
                                 {"content": WISH[0], "category": WISH[1], "budget": WISH[2]})
    wish_id = (data or {}).get("wish_id") if code == 0 else None
    rec("求购", WISH[0], OK if code == 0 else FAIL,
        f"wish_id={wish_id}" if code == 0 else f"HTTP {http} code={code} {msg}")

    # ---------- 5. 收藏 ----------
    print("\n5 · 收藏（A-11 收藏页依赖：帖子 + 二手各 1）")
    favorites = []
    if topic_ids and topic_ids[0]:
        favorites.append(("topic", topic_ids[0]))
    real_items = [i for i in item_ids if i]
    if real_items:
        favorites.append(("item", real_items[0]))
    for ttype, tid in favorites:
        http, code, msg, _ = call(c, "POST", "/favorites", Ha,
                                  {"target_type": ttype, "target_id": tid})
        rec("收藏", f"{ttype}#{tid}", OK if code == 0 else FAIL, f"code={code}" + (f" {msg}" if code != 0 else ""))

    # ---------- 汇总 ----------
    print("\n" + "=" * 78)
    n_ok = sum(1 for r in ROWS if r[2] == OK)
    n_fail = sum(1 for r in ROWS if r[2] == FAIL)
    print(f"汇总：{OK} {n_ok} 项成功 ｜ {FAIL} {n_fail} 项失败")
    print(f"创建：二手 {len(real_items)} 件 ｜ 帖子 {len([t for t in topic_ids if t])} 篇 ｜ 求购 {1 if wish_id else 0} 条 ｜ 收藏 {len(favorites)} 项")
    if n_fail:
        print("\n失败项：")
        for kind, name, status, note in ROWS:
            if status == FAIL:
                print(f"  {FAIL} [{kind}] {name} — {note}")
        print("\n提示：若失败原因是 HTTP 422（缺字段），请按其返回的字段名修正本脚本的 body；")
        print("     若是 404（路径不符），请核对 docs/api.md 最新契约。")
    print("\n下一步：跑 tools/preflight_check.py 复核 E5（列表是否有数据）")
    return 0 if n_fail == 0 else 0  # 不因业务失败中断封测准备


if __name__ == "__main__":
    sys.exit(main())
