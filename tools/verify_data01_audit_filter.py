#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DATA-01 回归验证：二手「待审内容不可见」是否生效。

用途：为审计条目 **DATA-01**（二手审核「只写不读」）提供**可复现证据**，
并供**非修复者**独立复跑（见 Issue #43 的封测约定）。

被验证的读路径（原实现 3 条全部未过滤）
----------------------------------------
| # | 路径 | 位置 | 层 |
|---|---|---|---|
| ① | `GET /secondhand/items`（列表） | `SecondhandDAO::page_items` | C++（需重编译） |
| ② | `POST /secondhand/wishes` 的 AI 匹配 | `SecondhandDAO::match_items_for_wish` | C++（需重编译） |
| ③ | `GET /secondhand/items/{id}`（详情） | `secondhand.py::item_detail` 内联 SQL | Python（改完即生效） |

验证分三段
----------
- **Part 1 · DAO 运行时**：自建「待审商品 + 已过审对照」两条数据，断言三条 DAO 路径行为；跑完**自动清理**。
  → 这段能证明 **`jt_db.pyd` 已用最新源码重编译**（不重编译必失败）。
- **Part 2 · 源码静态断言**：断言 Python 侧详情 SQL 含 `audit_status`、pybind 暴露 `audited_only`。
- **Part 3 · HTTP 端到端（可选）**：`--http` 时用 mock 登录走真实接口，
  复刻「甲发敏感词商品 → 乙看不到」全链路。需后端在跑。

前置
----
1. MySQL 在跑；`backend/app/db/native/jt_db.pyd` 已按最新源码编译；
2. 环境变量 **`XJT_DB_PASSWORD`（必需）**、`XJT_DB_PORT`（默认 3307）。

运行
----
    $env:XJT_DB_PASSWORD='<your-local-password>'
    python -X utf8 tools/verify_data01_audit_filter.py            # Part 1 + 2
    python -X utf8 tools/verify_data01_audit_filter.py --http     # 再加 Part 3

退出码：0 = 全部通过；1 = 有失败（可直接接进 CI，见任务 `T-02`）。
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

MARK = "XJT-DATA01-PROBE"  # 探针标题前缀，用于识别与清理

_results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> bool:
    _results.append((ok, name, detail))
    flag = "PASS" if ok else "FAIL"
    print(f"  [{flag}] {name}" + (f"  —— {detail}" if detail else ""))
    return ok


def hdr(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


# --------------------------------------------------------------------- 连接
def connect():
    from app.db import cpp_bridge  # noqa: PLC0415

    if not cpp_bridge.available():
        print("❌ jt_db C++ 扩展不可用：请先编译（db/cpp_driver/scripts/build.ps1）")
        sys.exit(2)

    pwd = os.getenv("XJT_DB_PASSWORD", "")
    if not pwd:
        print("❌ 缺少环境变量 XJT_DB_PASSWORD（本机数据库密码，勿写入仓库）")
        sys.exit(2)

    cpp_bridge.init_db(
        os.getenv("XJT_DB_HOST", "127.0.0.1"),
        int(os.getenv("XJT_DB_PORT", "3307")),
        os.getenv("XJT_DB_USER", "root"),
        pwd,
        os.getenv("XJT_DB_NAME", "xiaojietong"),
    )
    return cpp_bridge


# ----------------------------------------------------------- Part 1 · DAO 运行时
def part1_dao(bridge) -> None:
    hdr("Part 1 · DAO 运行时（证明 jt_db.pyd 已重编译）")
    dao = bridge.secondhand_dao()

    # 取一个可用用户
    users = bridge.query("SELECT id FROM user WHERE is_deleted = 0 ORDER BY id LIMIT 1")
    if not users:
        check(False, "取测试用户", "user 表无可用用户")
        return
    uid = int(users[0]["id"])
    cat = "DATA01验"

    # 清掉历史残留
    bridge.execute("DELETE FROM secondhand_item WHERE title LIKE ?", [MARK + "%"])
    bridge.execute("DELETE FROM secondhand_wish WHERE content LIKE ?", [MARK + "%"])

    pending_id = bridge.execute(
        "INSERT INTO secondhand_item "
        "(user_id, title, description, category, price, status, audit_status) "
        "VALUES (?, ?, ?, ?, ?, 0, 0)",
        [uid, f"{MARK}-待审", "回归验证用（待审）", cat, 10.00],
    )[1]
    audited_id = bridge.execute(
        "INSERT INTO secondhand_item "
        "(user_id, title, description, category, price, status, audit_status) "
        "VALUES (?, ?, ?, ?, ?, 0, 1)",
        [uid, f"{MARK}-已过审", "回归验证用（已过审）", cat, 10.00],
    )[1]
    wish_id = bridge.execute(
        "INSERT INTO secondhand_wish (user_id, content, category, budget, status) "
        "VALUES (?, ?, ?, ?, 0)",
        [uid, f"{MARK}-求购", cat, 999.00],
    )[1]
    print(f"  探针数据：待审 id={pending_id} / 已过审 id={audited_id} / 求购 id={wish_id}")

    # ---- ① page_items 默认（公开列表）
    rows = dao.page_items(1, 100, cat, "", True)
    ids = {int(r["id"]) for r in rows}
    check(pending_id not in ids, "① page_items 默认隐藏「待审」", f"待审 id={pending_id}")
    check(audited_id in ids, "① page_items 仍返回「已过审」", f"已过审 id={audited_id}")

    # ---- ①' page_items 显式放开（管理端用途）
    rows2 = dao.page_items(1, 100, cat, "", True, False)
    ids2 = {int(r["id"]) for r in rows2}
    check(pending_id in ids2, "①' page_items(audited_only=False) 可见「待审」", "证明参数生效")

    # ---- ② match_items_for_wish
    matched = {int(r["id"]) for r in dao.match_items_for_wish(wish_id, 50)}
    check(pending_id not in matched, "② match_items_for_wish 隐藏「待审」")
    check(audited_id in matched, "② match_items_for_wish 仍返回「已过审」")

    # 清理（无论断言结果如何）
    bridge.execute("DELETE FROM secondhand_item WHERE title LIKE ?", [MARK + "%"])
    bridge.execute("DELETE FROM secondhand_wish WHERE content LIKE ?", [MARK + "%"])
    left = bridge.query(
        "SELECT COUNT(*) AS c FROM secondhand_item WHERE title LIKE ?", [MARK + "%"]
    )
    check(int(left[0]["c"]) == 0, "探针数据已清理", "残留 = 0")


# --------------------------------------------------- Part 2 · 源码静态断言
def part2_source() -> None:
    hdr("Part 2 · 源码静态断言（Python 侧 + 绑定）")

    # Python 详情路径：本人可见 / 他人不可见
    detail_cases = [
        (
            "backend/app/routers/secondhand.py",
            "def item_detail",
            "audit_status = 1 OR i.user_id = ?",
            "③ 二手详情（secondhand.py::item_detail）",
        ),
        (
            "backend/app/routers/forum.py",
            "def topic_detail",
            "audit_status = 1 OR t.author_id = ?",
            "④ 论坛详情（forum.py::topic_detail，本轮新发现同类缺陷）",
        ),
    ]
    for rel, anchor, needle, name in detail_cases:
        src = (ROOT / rel).read_text(encoding="utf-8")
        i = src.find(anchor)
        seg = src[i : i + 1200] if i >= 0 else ""
        check(i >= 0 and needle in seg, f"{name} 已补审核过滤", rel)

    pybind = ROOT / "db" / "cpp_driver" / "pybind" / "pybind_wrapper.cpp"
    check(
        'py::arg("audited_only")' in pybind.read_text(encoding="utf-8", errors="ignore"),
        "pybind 暴露 audited_only 参数",
    )

    for rel, needle, name in [
        ("db/cpp_driver/src/dao/secondhand_dao.cpp", "i.audit_status = 1", "② page_items / match 含过滤"),
        ("db/cpp_driver/include/jt_db/dao/secondhand_dao.h", "audited_only", "头文件声明 audited_only"),
    ]:
        ok = needle in (ROOT / rel).read_text(encoding="utf-8", errors="ignore")
        check(ok, f"源码：{name}", rel)


# ------------------------------------------------------- Part 3 · HTTP 端到端
def _find_id(bridge, table: str, title: str) -> int | None:
    rows = bridge.query(
        f"SELECT id FROM {table} WHERE title = ? ORDER BY id DESC LIMIT 1", [title]
    )
    return int(rows[0]["id"]) if rows else None


def part3_http(base: str, bridge) -> None:
    """真实接口全链路。

    方法：先发布**正常内容**（避免 3003 block 干扰），再用 SQL 把该行**强制置为
    「待审」(audit_status=0)**，从而确定性地复刻"审核未通过前的可见性"场景，
    不依赖 AI/词库审核的判定结果。
    """
    hdr(f"Part 3 · HTTP 端到端（{base}）")
    try:
        import httpx  # noqa: PLC0415
    except ImportError:
        check(False, "导入 httpx", "请用装了 httpx 的解释器（如 E:/miniconda3/python.exe）")
        return

    # trust_env=False：系统代理会把本地请求打成 502（见 SEC-23）
    cli = httpx.Client(base_url=base, timeout=30.0, trust_env=False)

    def login(code: str) -> str:
        body = cli.post("/auth/wechat-login", json={"code": code}).json()
        if body.get("code") != 0:
            raise RuntimeError(f"登录失败({code}): {body.get('message')}")
        return body["data"]["token"]

    try:
        ha = {"Authorization": f"Bearer {login('seed-user-alpha')}"}  # 甲：发布者
        hb = {"Authorization": f"Bearer {login('seed-user-beta')}"}   # 乙：旁观者
    except Exception as exc:  # noqa: BLE001
        check(False, "mock 登录（seed-user-alpha/beta）", f"{exc}")
        return

    def force_pending(table: str, rid: int) -> None:
        bridge.execute(
            f"UPDATE {table} SET audit_status = 0, is_deleted = 0 WHERE id = ?", [rid]
        )

    def force_audited(table: str, rid: int) -> None:
        bridge.execute(f"UPDATE {table} SET audit_status = 1 WHERE id = ?", [rid])

    def ids_of(payload: dict) -> set[int]:
        """⚠️ jt_db C++ 层所有值都转成字符串（`id` 回的是 `"40"`）—— 必须强制转 int，
        否则 `"39" == 39` 恒为 False，会导致断言**假通过**。"""
        return {int(x["id"]) for x in (payload.get("items") or [])}

    # ================= 二手：① 列表 + ③ 详情 =================
    item_title = f"{MARK}-HTTP-item"
    pub = cli.post(
        "/secondhand/items",
        json={"title": item_title, "description": "回归验证", "category": "DATA01验", "price": 1},
        headers=ha,
    ).json()
    iid = (pub.get("data") or {}).get("item_id") or _find_id(bridge, "secondhand_item", item_title)
    if not iid:
        check(False, "甲发布商品", f"未取到 item_id：{pub}")
        return
    iid = int(iid)
    force_pending("secondhand_item", iid)

    try:
        mine = cli.get("/secondhand/items/mine", params={"size": 100}, headers=ha).json()
        row = next((x for x in (mine["data"]["items"] or []) if int(x["id"]) == iid), None)
        check(
            row is not None and int(row.get("audit_status") or 1) == 0,
            "前置：商品确处于「待审」状态",
            f"id={iid} audit_status={row.get('audit_status') if row else 'N/A'}",
        )

        lst = cli.get("/secondhand/items", params={"size": 100}, headers=hb).json()
        check(iid not in ids_of(lst["data"]), "① 列表：乙看不到待审商品", f"列表共 {len(ids_of(lst['data']))} 条")

        det_b = cli.get(f"/secondhand/items/{iid}", headers=hb).json()
        check(det_b.get("code") != 0, "③ 详情：乙看不到（预期 1001）", f"code={det_b.get('code')}")

        det_a = cli.get(f"/secondhand/items/{iid}", headers=ha).json()
        check(det_a.get("code") == 0, "③ 详情：甲（发布者）仍可看自己的（未过度拦截）")

        # 回归对照：改成已过审后，乙应能看到
        force_audited("secondhand_item", iid)
        lst2 = cli.get("/secondhand/items", params={"size": 100}, headers=hb).json()
        check(iid in ids_of(lst2["data"]), "回归对照：过审后乙能看到")
    finally:
        bridge.execute("UPDATE secondhand_item SET is_deleted = 1 WHERE id = ?", [iid])

    # ================= 论坛：④ 详情（同类缺陷）=================
    # ⚠️ forum 路由前缀是 `/topics`（不是 `/forum`）
    topic_title = f"{MARK}-HTTP-topic"
    post = cli.post(
        "/topics",
        json={"title": topic_title, "content": "回归验证内容", "category": "生活"},
        headers=ha,
    ).json()
    tid = (post.get("data") or {}).get("topic_id") or _find_id(bridge, "topic", topic_title)
    if not tid:
        check(False, "甲发帖", f"未取到 topic_id：{post}")
        return
    tid = int(tid)
    bridge.execute("UPDATE topic SET status = 0 WHERE id = ?", [tid])
    force_pending("topic", tid)

    try:
        det_b = cli.get(f"/topics/{tid}", headers=hb).json()
        check(det_b.get("code") != 0, "④ 详情：乙看不到待审帖子", f"code={det_b.get('code')}")

        det_a = cli.get(f"/topics/{tid}", headers=ha).json()
        check(det_a.get("code") == 0, "④ 详情：作者仍可看自己的帖子")
    except Exception as exc:  # noqa: BLE001
        check(False, "论坛详情验证", repr(exc))
    finally:
        bridge.execute("UPDATE topic SET is_deleted = 1 WHERE id = ?", [tid])

    print(f"  （探针数据已软删：item id={iid} / topic id={tid}）")


def main() -> int:
    ap = argparse.ArgumentParser(description="DATA-01 二手审核过滤回归验证")
    ap.add_argument("--http", action="store_true", help="额外跑 HTTP 端到端（需后端在跑）")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000/api/v1")
    args = ap.parse_args()

    bridge = connect()
    try:
        part1_dao(bridge)
    except Exception as exc:  # noqa: BLE001
        check(False, "Part 1 异常中断", repr(exc))
    part2_source()
    if args.http:
        part3_http(args.base_url, bridge)

    passed = sum(1 for ok, _, _ in _results if ok)
    total = len(_results)
    hdr(f"结果：{passed}/{total} 通过")
    for ok, name, detail in _results:
        if not ok:
            print(f"  ❌ {name}" + (f"  —— {detail}" if detail else ""))
    if passed != total:
        print("\n⛔ 存在未通过项。若 Part 1 失败，最常见原因：jt_db.pyd 未重编译。")
        return 1
    print("\n✅ DATA-01 四条读路径全部通过（列表 / AI匹配 / 二手详情 / 帖子详情）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
