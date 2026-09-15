"""C22 · `token_version`（登出使 token 失效）+ 学号唯一 / 限频。

任务单 C22 的验收口径：「重复学号被拒；**1 次 / 7 天**限频；`PUT /user/me` 可改并回读；
**登出使 token 失效**」。

本文件专盯两处最容易做错的地方：

1. **登出必须真的生效** —— 本仓此前 `POST /auth/logout` 是**空操作**
   （原注释写"无状态 JWT：前端丢弃 token 即可"），所以"登出使 token 失效"原本**做不到**；
   C22 用 `user.token_version` 才把它做出来。
   而且 **`/auth/refresh` 也必须校验**，否则拿登出前的 refresh_token 就能换回新
   access_token，"失效"形同虚设（本文件有专门用例守这条）。
2. **向后兼容** —— 线上已签发的 token 里**没有** `tv` 字段。上线那一刻
   **不能把已登录用户全部踢下线**：老 token 缺字段按 `0`，而库列默认也是 `0`
   ⇒ 相等 ⇒ 放行。

⚠️ 所有用例都用**专用探针用户**（`openid = oXJT_DEV_c22_probe_*`），用完即删。
不复用 `hdr_a` / `hdr_b` —— 本文件会把 `token_version` +1，复用 session 级夹具
会连带把其它用例的 token 一起打失效。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.core.config import settings
from app.db import cpp_bridge

# 需要鉴权、且**无副作用**的探针接口（GET 列表，不会写库）
_AUTH_PROBE = "/api/v1/life/notices?page=1&size=1"

# 限频窗口（业务约定 7 天，与 C22 验收一致）
_INTERVAL_DAYS = 7

# 探针用户 openid 前缀（与登录接口 mock 的 `oXJT_DEV_<code>` 同体系，便于识别与清理）
_PROBE_PREFIX = "oXJT_DEV_c22_probe_"


def _jwt(uid: int, *, tv: int | None = None, kind: str = "access") -> str:
    """手工签一个 token。

    ``tv=None`` 时**不带** ``tv`` 字段 —— 用来精确模拟"上线前签发的存量 token"。
    """
    payload: dict = {
        "uid": int(uid),
        "role": 0,
        "type": kind,
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    if tv is not None:
        payload["tv"] = int(tv)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_probe(tag: str) -> int:
    """直接建一个专用探针用户，返回 uid。

    **不走 HTTP 登录**：`POST /auth/wechat-login` 带 60 秒限频（实测返回
    `429 code=1010 操作过于频繁`），若每个用例都去登录会把测试自己限死。
    这里直接用 DAO 建用户，token 一律用 `_jwt()` 手工签 —— 反而能更精确地
    控制 `tv`（包括故意不带 `tv` 的存量 token）。
    """
    openid = _PROBE_PREFIX + tag
    dao = cpp_bridge.user_dao()
    rows = cpp_bridge.query("SELECT id FROM `user` WHERE openid = ?", [openid])
    if rows:
        cpp_bridge.execute("DELETE FROM `user` WHERE id = ?", [int(rows[0]["id"])])
    uid = int(dao.create(openid, "c22-probe"))
    assert uid > 0, "探针用户创建失败"
    cpp_bridge.execute("UPDATE `user` SET token_version = 0 WHERE id = ?", [uid])
    return uid


def _drop_probe(uid: int) -> None:
    cpp_bridge.execute("DELETE FROM `user` WHERE id = ?", [uid])


def _token_version(uid: int) -> int:
    rows = cpp_bridge.query("SELECT token_version FROM `user` WHERE id = ?", [uid])
    assert rows, f"用户 {uid} 不存在"
    return int(rows[0].get("token_version") or 0)


@pytest.fixture()
def probe():
    """专用探针用户（已归零 tv，用完即删）。"""
    uid = _make_probe("main")
    try:
        yield {"uid": uid, "token": _jwt(uid, tv=0), "refresh": _jwt(uid, tv=0, kind="refresh")}
    finally:
        _drop_probe(uid)


# ------------------------------------------------------- 向后兼容（最关键）

def test_legacy_token_without_tv_is_accepted(client, probe):
    """🔴 存量 token（**没有** `tv` 字段）上线后必须仍然可用。

    这是 C22 最大的回归风险：库里 `token_version` 默认 0，老 token 缺字段也按 0，
    两者相等 ⇒ 放行。若实现里写成 `payload["tv"]` 直接取键，这里会 KeyError/拒绝，
    等于**上线即把全站已登录用户踢下线**。
    """
    legacy = _jwt(probe["uid"])          # 故意不带 tv
    resp = client.get(_AUTH_PROBE, headers=_hdr(legacy))
    assert resp.status_code == 200, resp.text
    assert resp.json()["code"] == 0


def test_token_with_matching_tv_is_accepted(client, probe):
    """反向对照：带正确 `tv` 的 token 能用 ⇒ 证明上面的"能用"不是因为鉴权被绕过。"""
    assert _token_version(probe["uid"]) == 0
    resp = client.get(_AUTH_PROBE, headers=_hdr(_jwt(probe["uid"], tv=0)))
    assert resp.status_code == 200, resp.text


def test_token_with_stale_tv_is_rejected(client, probe):
    """`tv` 与库里不一致 ⇒ 必须拒绝（否则版本号形同虚设）。"""
    stale = _jwt(probe["uid"], tv=_token_version(probe["uid"]) + 1)
    resp = client.get(_AUTH_PROBE, headers=_hdr(stale))
    assert resp.status_code != 200, f"陈旧 tv 竟然通过了：{resp.status_code} {resp.text}"


# --------------------------------------------------------------- 登出生效

def test_logout_bumps_token_version_and_invalidates_old_token(client, probe):
    """登出 ⇒ `token_version` +1，且**同一个 token 立刻失效**。"""
    before = _token_version(probe["uid"])
    assert client.get(_AUTH_PROBE, headers=_hdr(probe["token"])).status_code == 200

    resp = client.post("/api/v1/auth/logout", headers=_hdr(probe["token"]))
    assert resp.status_code == 200, resp.text
    assert _token_version(probe["uid"]) == before + 1, "登出没有把 token_version +1"

    after = client.get(_AUTH_PROBE, headers=_hdr(probe["token"]))
    assert after.status_code != 200, f"登出后旧 token 竟然还能用：{after.text}"


def test_logout_is_idempotent_without_token(client, probe):
    """登出**不要求**带 token，且必须幂等（客户端丢 token 后再调一次不能报错）。"""
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.post("/api/v1/auth/logout", headers=_hdr("not-a-jwt")).status_code == 200
    assert client.post("/api/v1/auth/logout", headers=_hdr(probe["token"])).status_code == 200


def test_refresh_after_logout_is_rejected(client, probe):
    """🔴 登出后**旧 refresh_token 也必须失效**。

    若 refresh 链路不校验 `tv`，攻击者/前端只要留着登出前的 refresh_token，
    就能无限换回新的 access_token —— "登出使 token 失效"会**形同虚设**。
    """
    old_refresh = probe["refresh"]
    assert client.post("/api/v1/auth/logout", headers=_hdr(probe["token"])).status_code == 200

    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert resp.status_code != 200, f"登出后旧 refresh_token 竟然还能换新 token：{resp.text}"


def test_refresh_token_carries_tv_and_legacy_refresh_still_works(client, probe):
    """新签发的 refresh_token 必须带 `tv`；而**老 refresh（无 tv）仍兼容**。"""
    fresh = client.post("/api/v1/auth/refresh", json={"refresh_token": probe["refresh"]})
    assert fresh.status_code == 200, fresh.text
    new_refresh = fresh.json()["data"]["refresh_token"]
    assert jwt.decode(
        new_refresh, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
    ).get("tv") == _token_version(probe["uid"])

    legacy_refresh = _jwt(probe["uid"], kind="refresh")   # 不带 tv
    ok = client.post("/api/v1/auth/refresh", json={"refresh_token": legacy_refresh})
    assert ok.status_code == 200, f"存量 refresh token 被误杀：{ok.text}"


# ----------------------------------------------------- 学号唯一 / 限频（DAO）

def test_student_no_change_remaining_days(client, probe):
    """限频：从未改过 = 0；刚改完 > 0；把时间戳倒推 8 天 = 0。"""
    uid = probe["uid"]
    dao = cpp_bridge.user_dao()

    assert dao.student_no_change_remaining_days(uid, _INTERVAL_DAYS) == 0, "从未改过应为 0"

    no = "C22TEST" + uuid.uuid4().hex[:8]
    assert dao.update_student_no(uid, no) is True, "绑定学号失败"
    remaining = dao.student_no_change_remaining_days(uid, _INTERVAL_DAYS)
    assert remaining > 0, f"刚改完的剩余天数应 > 0，实际 {remaining}"

    # 反向对照：把时间戳倒推 8 天（> 7 天窗口）⇒ 应恢复可改
    cpp_bridge.execute(
        "UPDATE `user` SET student_no_updated_at = NOW() - INTERVAL 8 DAY WHERE id = ?",
        [uid],
    )
    assert dao.student_no_change_remaining_days(uid, _INTERVAL_DAYS) == 0, "超过窗口后应可改"

    # 校验真的写进去了（回读）
    assert dao.find_by_id(uid)["student_no"] == no


def test_duplicate_student_no_is_rejected_by_unique_index(client, probe):
    """重复学号必须被 `uk_student_no` 拦下（两个探针用户绑同一学号）。"""
    other_uid = _make_probe("dup")
    dao = cpp_bridge.user_dao()
    no = "C22DUP" + uuid.uuid4().hex[:8]
    try:
        assert dao.update_student_no(probe["uid"], no) is True
        with pytest.raises(Exception) as exc:      # DbException → Python 异常
            dao.update_student_no(other_uid, no)
        assert "Duplicate" in str(exc.value) or "1062" in str(exc.value), (
            f"异常信息不像唯一键冲突：{exc.value}"
        )
    finally:
        _drop_probe(other_uid)


def test_update_student_no_returns_false_for_missing_user(client, probe):
    """用户不存在 ⇒ 返回 False（而不是抛异常）。"""
    assert cpp_bridge.user_dao().update_student_no(999_000_001, "C22NONE") is False
    assert cpp_bridge.user_dao().student_no_change_remaining_days(
        999_000_001, _INTERVAL_DAYS
    ) == -1


def test_bump_token_version_returns_new_version(client, probe):
    """`bump_token_version` 返回**自增后**的新版本号；不存在用户返回 -1。"""
    dao = cpp_bridge.user_dao()
    v0 = _token_version(probe["uid"])
    assert dao.bump_token_version(probe["uid"]) == v0 + 1
    assert dao.bump_token_version(probe["uid"]) == v0 + 2
    assert dao.bump_token_version(999_000_001) == -1
