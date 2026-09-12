"""PR #39 深度审查：接口返回字段探测（字段级契约比对用）。

用途：把 PR head（`feature/frontend@ca9db97`）实际调用的接口**逐个真实调用**，
打印后端返回的字段名，供与前端 `pages/**/*.js` 中读取的字段逐项比对，
发现「字段名不匹配 → 界面空白」这类只在真机才暴露的问题。

前置：后端已启动（127.0.0.1:8000），MySQL 可用。
运行：
    E:/miniconda3/python.exe -X utf8 tools/pr39_field_probe.py
"""

from __future__ import annotations

import json
import sys

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
CLIENT = httpx.Client(trust_env=False, timeout=20.0)


def login() -> str | None:
    try:
        r = CLIENT.post(
            f"{BASE}/auth/wechat-login",
            json={"code": "probe-pr39-field"},
            headers={"X-Forwarded-For": "198.51.100.88"},
        )
        return ((r.json().get("data") or {}).get("token"))
    except Exception as exc:  # noqa: BLE001
        print(f"登录失败：{exc}")
        return None


def call(auth: dict, method: str, path: str, params: dict | None = None) -> tuple[int, dict]:
    try:
        r = CLIENT.request(method, BASE + path, params=params, headers=auth)
        try:
            body = r.json()
        except Exception:  # noqa: BLE001
            return r.status_code, {}
        return r.status_code, body
    except Exception as exc:  # noqa: BLE001
        return -1, {"_error": str(exc)}


def shape(data) -> str:
    """把 data 的字段结构压缩成一行：顶层键，若含 items 则附 items[0] 的键。"""
    if data is None:
        return "data=null"
    if isinstance(data, list):
        first = data[0] if data else None
        return f"list[{len(data)}] " + (f"item_keys={sorted(first.keys())}" if isinstance(first, dict) else "")
    if not isinstance(data, dict):
        return f"{type(data).__name__}={data!r}"

    keys = sorted(data.keys())
    out = f"keys={keys}"
    items = data.get("items")
    if isinstance(items, list):
        if items and isinstance(items[0], dict):
            out += f" | items[{len(items)}].keys={sorted(items[0].keys())}"
        else:
            out += f" | items[{len(items)}] (empty)"
    return out


def main() -> int:
    token = login()
    if not token:
        print("❌ 无法获取 token，终止")
        return 2
    auth = {"Authorization": f"Bearer {token}"}
    print(f"token 获取成功（用户已登录）\n目标：{BASE}\n" + "=" * 100)

    # 先取一些 id 供详情类接口使用
    _, topics = call(auth, "GET", "/topics", {"size": 1})
    topic_id = None
    items = (topics.get("data") or {}).get("items") or []
    if items:
        topic_id = items[0].get("id")

    _, jobs = call(auth, "GET", "/jobs", {"size": 1})
    job_items = (jobs.get("data") or {}).get("items") or []
    job_id = job_items[0].get("id") if job_items else None

    _, merchants = call(auth, "GET", "/life/merchants")
    mer_items = (merchants.get("data") or {}).get("items") or []
    merchant_id = mer_items[0].get("id") if mer_items else None

    probes: list[tuple[str, str, str, dict | None]] = [
        ("用户·我的信息", "GET", "/user/me", None),
        ("用户·标签（前端未接）", "GET", "/user/tags", None),
        ("聊天·会话列表", "GET", "/chat/conversations", None),
        ("帖子·列表", "GET", "/topics", {"size": 2}),
        ("帖子·热门", "GET", "/topics/hot", {"limit": 2}),
        ("帖子·我的", "GET", "/topics/mine", {"size": 2}),
        ("收藏·我的", "GET", "/favorites", None),
        ("图书馆·空教室", "GET", "/library/free-rooms", {"campus": "", "floor": "", "period": ""}),
        ("图书馆·座位", "GET", "/library/rooms/1/seats", {"date": "2026-09-15"}),
        ("图书馆·我的预约", "GET", "/library/reservations/me", None),
        ("二手·列表", "GET", "/secondhand/items", {"size": 2}),
        ("二手·我的发布", "GET", "/secondhand/items/mine", {"size": 2}),
        ("兼职·列表", "GET", "/jobs", {"size": 2}),
        ("兼职·我的申请", "GET", "/jobs/applications/me", None),
        ("生活·商家", "GET", "/life/merchants", {"category": ""}),
        ("生活·通知", "GET", "/life/notices", {"size": 2}),
        ("生活·通知流（前端未接）", "GET", "/life/notice-feed", {"size": 2}),
        ("地图·POI", "GET", "/map/pois", None),
        ("地图·附近", "GET", "/map/nearby", {"lat": 43.88, "lng": 125.32, "radius": 3000}),
        ("地图·建筑详情", "GET", "/map/building/1", None),
        ("Agent·任务", "GET", "/agent/tasks", {"page": 1, "size": 2}),
        ("Agent·提醒", "GET", "/agent/reminders", None),
    ]
    if topic_id is not None:
        probes.insert(5, ("帖子·详情", "GET", f"/topics/{topic_id}", None))
        probes.insert(6, ("帖子·审核状态（前端未接）", "GET", f"/topics/{topic_id}/audit-status", None))
    if job_id is not None:
        probes.append(("兼职·详情", "GET", f"/jobs/{job_id}", None))
    if merchant_id is not None:
        probes.append(("生活·菜单", "GET", f"/life/merchants/{merchant_id}/menu", None))
    probes.append(("二手·详情（后端缺失）", "GET", f"/secondhand/items/{items[0].get('id') if items else 1}", None))

    for label, method, path, params in probes:
        code, body = call(auth, method, path, params)
        bcode = body.get("code", "-")
        data = body.get("data")
        print(f"\n【{label}】{method} {path}")
        print(f"  HTTP {code} / code={bcode}  {shape(data)}")

    print("\n" + "=" * 100)
    print("说明：以上为后端**真实返回**的字段。请与前端 pages/**/*.js 中读取的字段逐项比对；")
    print("      重点看 `keys=` 是否包含前端用到的字段名（缺失即界面空白/显示 undefined）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
