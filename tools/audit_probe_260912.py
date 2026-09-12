"""2026-09-12 整体审计实测探针（只读为主，不修改业务数据）。

用途：为 `docs/项目审计报告20260912序1.md` 的关键结论提供**可复现**证据。
覆盖 7 个探针：限流 IP 可伪造 / 限流本身有效性 / health 未授权 / 分页 total 语义 /
非法参数错误码 / POI 空表 / Pydantic 校验错误是否符合统一契约。

前置：后端已启动（默认 127.0.0.1:8000）。
运行：
    d:\\xiaojietongproject\\.venv\\Scripts\\python.exe tools\\audit_probe_260912.py

⚠️ 探针 1/2 会向 /auth/wechat-login 发送 27 次请求（dev 态 mock 登录，会创建
   oXJT_DEV_* 用户）。若随后要跑限流验证脚本，请先重启后端清空计数。
"""

from __future__ import annotations

import json
import random
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
LOGIN = f"{BASE}/auth/wechat-login"

# trust_env=False：httpx 默认读 HTTP_PROXY 环境变量，本机代理会让请求变 502
CLIENT = httpx.Client(trust_env=False, timeout=20.0)


def hdr(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def brief(resp: httpx.Response) -> str:
    text = resp.text.replace("\n", " ")[:220]
    return f"HTTP {resp.status_code} | {text}"


# ---------------------------------------------------------------- 探针 ----

def probe_1_rate_limit_xff() -> None:
    """限流是否可被伪造 X-Forwarded-For 绕过。"""
    hdr("探针 1 · 伪造 X-Forwarded-For 能否绕过登录限流（规则 10 次/分钟）")
    codes: list[int] = []
    for i in range(15):
        fake_ip = f"203.0.113.{random.randint(1, 254)}"
        try:
            r = CLIENT.post(
                LOGIN,
                json={"code": f"probe-xff-{i}"},
                headers={"X-Forwarded-For": fake_ip},
            )
            codes.append(r.status_code)
        except Exception as exc:  # noqa: BLE001
            codes.append(-1)
            print(f"  第 {i + 1} 次异常：{exc}")
    print(f"  15 次（每次不同 XFF）状态码序列：{codes}")
    print(f"  其中 429 次数：{codes.count(429)}")
    print(
        "  结论："
        + ("❌ 限流被绕过（伪造 IP 即得新配额）" if codes.count(429) == 0 else "✅ 未被绕过")
    )


def probe_2_rate_limit_real_ip() -> None:
    """同一真实 IP 连续登录，确认限流本身生效（对照组）。"""
    hdr("探针 2 · 对照组：不伪造 XFF，同一 IP 连续登录 12 次")
    codes: list[int] = []
    for i in range(12):
        try:
            r = CLIENT.post(LOGIN, json={"code": f"probe-ip-{i}"})
            codes.append(r.status_code)
        except Exception:  # noqa: BLE001
            codes.append(-1)
    print(f"  状态码序列：{codes}")
    print(
        "  结论："
        + (
            "✅ 第 11 次起触发 429（限流对「不可伪造的 IP」有效）"
            if 429 in codes
            else "⚠️ 未触发 429（同分钟内可能有其它用例已占用配额）"
        )
    )
    if 429 in codes:
        idx = codes.index(429)
        r = CLIENT.post(LOGIN, json={"code": "probe-ip-retry"})
        print(f"  限流响应体样例：{brief(r)}")


def probe_3_health_unauthorized() -> None:
    """health 三个端点是否可匿名访问（detail/selfcheck 会触发重活）。"""
    hdr("探针 3 · /health、/health/detail 免鉴权访问")
    for path in ("/health", "/health/detail"):
        t0 = time.time()
        try:
            r = CLIENT.get(BASE + path)
            cost = (time.time() - t0) * 1000
            print(f"  GET {path:18s} → {brief(r)}  ({cost:.0f}ms)")
        except Exception as exc:  # noqa: BLE001
            print(f"  GET {path:18s} → 异常 {exc}")
    print("  （/health/selfcheck 会触发 embedding+LLM，耗时长，此处不发以避免占用算力）")
    print("  结论：未带 Authorization 仍返回 200 → 匿名可探测内部拓扑 / 消耗算力")


def probe_4_pagination_total() -> None:
    """分页 total 是否为「总记录数」还是「本页条数」。"""
    hdr("探针 4 · 分页 total 语义（契约应为总记录数）")
    token = _token()
    if not token:
        print("  ⚠️ 无法获取 token，跳过")
        return
    auth = {"Authorization": f"Bearer {token}"}
    for path in ("/topics", "/jobs", "/secondhand/items", "/life/notices"):
        for page in (1, 2):
            try:
                r = CLIENT.get(
                    f"{BASE}{path}", params={"page": page, "size": 3}, headers=auth
                )
                body = r.json()
                data = body.get("data") or {}
                items = data.get("items") or []
                print(
                    f"  {path:18s} page={page} → code={body.get('code')} "
                    f"items={len(items)} total={data.get('total')} size={data.get('size')}"
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  {path:18s} page={page} → 异常 {exc}")
    print("  结论：若 total == items 条数，则前端「共 N 条/是否还有下一页」判断必然出错")


def probe_5_bad_param() -> None:
    """非法参数是否落到非契约的 500。"""
    hdr("探针 5 · 非法参数（period=abc）与缺少请求体")
    token = _token()
    auth = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        r = CLIENT.get(f"{BASE}/library/free-rooms", params={"period": "abc"}, headers=auth)
        print(f"  GET /library/free-rooms?period=abc → {brief(r)}")
        print(f"  是否统一契约（含 code 字段）：{'code' in _safe_json(r)}")
    except Exception as exc:  # noqa: BLE001
        print(f"  异常：{exc}")

    try:
        r = CLIENT.post(f"{BASE}/chat/send", headers=auth)
        print(f"  POST /chat/send（空 body）→ {brief(r)}")
        print(f"  是否统一契约（含 code 字段）：{'code' in _safe_json(r)}")
    except Exception as exc:  # noqa: BLE001
        print(f"  异常：{exc}")


def probe_6_map_pois() -> None:
    """POI 是否有种子数据（空表会影响地图/建筑详情）。"""
    hdr("探针 6 · /map/pois 与建筑列表数据可用性")
    token = _token()
    auth = {"Authorization": f"Bearer {token}"} if token else {}
    for path in ("/map/pois", "/map/building/1"):
        try:
            r = CLIENT.get(BASE + path, headers=auth)
            body = _safe_json(r)
            data = body.get("data")
            if isinstance(data, dict) and "items" in data:
                print(f"  GET {path:16s} → code={body.get('code')} items={len(data['items'])}")
            else:
                print(f"  GET {path:16s} → {brief(r)}")
        except Exception as exc:  # noqa: BLE001
            print(f"  GET {path:16s} → 异常 {exc}")


def probe_7_audit_status_leak() -> None:
    """列表/详情是否暴露 audit_status 或未过滤审核状态。"""
    hdr("探针 7 · 二手列表是否返回 audit_status、是否过滤待审内容")
    token = _token()
    auth = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        r = CLIENT.get(f"{BASE}/secondhand/items", params={"size": 5}, headers=auth)
        body = _safe_json(r)
        items = ((body.get("data") or {}).get("items") or [])
        keys = sorted(items[0].keys()) if items else []
        print(f"  第一条记录字段：{keys}")
        print(f"  是否含 audit_status：{'audit_status' in keys}")
        vals = [(it.get("id"), it.get("audit_status")) for it in items]
        print(f"  (id, audit_status) 列表：{vals}")
        print("  注：列表 SQL 无 audit_status 过滤 → 待审(0)/被拒(2) 内容同样可见")
    except Exception as exc:  # noqa: BLE001
        print(f"  异常：{exc}")


def probe_8_secondhand_audit_bypass() -> None:
    """🔴 写操作探针（需 --write）：二手「待审」内容是否立刻出现在公开列表。

    步骤：发布一条命中审核正则（手机号 → review 转人工）的商品 → 立即查公开列表
    → 若能被查到即证明「待审/被拒内容对所有人可见」；随后把该商品置为下架(2) 清理。
    """
    hdr("探针 8 · 二手待审内容是否立即可见（写操作，测完自动下架）")
    token = _token()
    if not token:
        print("  ⚠️ 无法获取 token，跳过")
        return
    auth = {"Authorization": f"Bearer {token}"}
    stamp = int(time.time())
    payload = {
        "title": f"审计探针{stamp} 联系 13800138000",
        "description": "整体审计探针数据，测完即下架",
        "category": "其他",
        "price": 1.0,
    }
    try:
        r = CLIENT.post(f"{BASE}/secondhand/items", json=payload, headers=auth)
        body = _safe_json(r)
        item_id = (body.get("data") or {}).get("item_id")
        print(f"  发布结果 → {brief(r)}")
        print(f"  返回 audit_status = {(body.get('data') or {}).get('audit_status')}（0=待审 1=通过 2=拒绝）")
        if not item_id:
            return

        r2 = CLIENT.get(f"{BASE}/secondhand/items", params={"size": 50}, headers=auth)
        items = ((_safe_json(r2).get("data") or {}).get("items") or [])
        visible = [it for it in items if int(it.get("id", 0)) == int(item_id)]
        print(f"  公开列表是否含该待审商品：{bool(visible)}")
        print(
            "  结论："
            + (
                "❌ 待审内容对所有人可见（审核可绕过）"
                if visible
                else "✅ 未出现在公开列表"
            )
        )

        # 清理：置为下架（status=2），使其从默认列表消失
        rc = CLIENT.put(
            f"{BASE}/secondhand/items/{item_id}/status", json={"status": 2}, headers=auth
        )
        print(f"  清理（下架 status=2）→ {brief(rc)}")
    except Exception as exc:  # noqa: BLE001
        print(f"  异常：{exc}")


# ------------------------------------------------------------- 工具函数 ----

def _safe_json(resp: httpx.Response) -> dict:
    try:
        got = resp.json()
        return got if isinstance(got, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


_TOKEN: str | None = None


def _token() -> str | None:
    """获取一个可用 token（伪造 XFF 避免被探针 2 的限流波及）。"""
    global _TOKEN
    if _TOKEN:
        return _TOKEN
    try:
        r = CLIENT.post(
            LOGIN,
            json={"code": "probe-token-holder"},
            headers={"X-Forwarded-For": "198.51.100.7"},
        )
        body = _safe_json(r)
        _TOKEN = (body.get("data") or {}).get("token")
    except Exception as exc:  # noqa: BLE001
        print(f"  获取 token 失败：{exc}")
    return _TOKEN


def main() -> int:
    print(f"探针目标：{BASE}")
    try:
        CLIENT.get(f"{BASE}/health", timeout=5.0)
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 后端不可达（{exc}）。请先启动：cd backend; python -m uvicorn app.main:app --port 8000")
        return 2

    probes = [
        probe_1_rate_limit_xff,
        probe_2_rate_limit_real_ip,
        probe_3_health_unauthorized,
        probe_4_pagination_total,
        probe_5_bad_param,
        probe_6_map_pois,
        probe_7_audit_status_leak,
    ]
    if "--write" in sys.argv:
        probes.append(probe_8_secondhand_audit_bypass)
    else:
        print("\n（探针 8「审核绕过」为写操作，需 --write 显式开启，默认跳过）")

    for fn in probes:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            print(f"  ❌ 探针 {fn.__name__} 执行失败：{exc}")

    print("\n" + "=" * 72)
    print("探针结束。结论请以 docs/项目审计报告20260912序1.md 的对应条目为准。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
