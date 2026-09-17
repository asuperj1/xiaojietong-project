"""用户：我的信息 / 兴趣标签。

契约：docs/api.md §2
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.deps import get_current_user
from app.core.response import err_auth, err_biz, err_param, ok
from app.db import cpp_bridge
from app.services.storage import resign

router = APIRouter(prefix="/user", tags=["user"])

#: C22 学号修改的限频窗口（业务约定 7 天，与 `test_c22_token_version.py` 的 `_INTERVAL_DAYS` 一致）
_STUDENT_NO_INTERVAL_DAYS = 7


def _view(u: dict) -> dict:
    return {
        "id": int(u.get("id", 0)),
        "openid": u.get("openid", ""),
        "nickname": u.get("nickname", ""),
        # B14 P1 修复：头像入库为裸路径，返回前重新签名
        "avatar": resign(u.get("avatar", "")),
        "role": int(u.get("role", 0)),
        "student_no": u.get("student_no", ""),
        "major": u.get("major", ""),
        "grade": u.get("grade", ""),
        "campus": u.get("campus", ""),
    }


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return ok(_view(user))


class UpdateMeIn(BaseModel):
    nickname: Optional[str] = None
    avatar: Optional[str] = None
    major: Optional[str] = None
    grade: Optional[str] = None
    campus: Optional[str] = None
    student_no: Optional[str] = None


def _change_student_no(uid: int, raw: str) -> None:
    """C22：修改学号 —— 唯一索引 + 「1 次 / 7 天」限频。

    学号**必须走这里**，不能混进下面那段通用 UPDATE，原因有两条：

    1. 通用 UPDATE 不会刷 `student_no_updated_at`，限频就**没有判定依据**
       （本仓此前正是如此：字段能改，但"7 天一次"从未生效）。
    2. 撞上唯一索引 `uk_student_no` 时会抛 `RuntimeError` 一路冒到 **HTTP 500**，
       而 `db/cpp_driver/include/jt_db/dao/user_dao.h` 明确要求调用方
       「捕获并转成业务错误，**不要让它变成 500**」。
    """
    student_no = (raw or "").strip()
    if not student_no:
        # 不能写 `''`：`db/sql/17_user_student_no.sql` 特意把存量 `''` 清成 NULL，
        # 因为 `''` 在唯一索引下会被当成同一个学号而互相冲突 —— 第二个清空的人直接 500。
        # 「解绑学号」属独立功能（需 C++ 支持绑 NULL），此处明确拒绝，不留半坏状态。
        raise err_param("学号不能为空")

    dao = cpp_bridge.user_dao()
    remaining = int(dao.student_no_change_remaining_days(uid, _STUDENT_NO_INTERVAL_DAYS))
    if remaining < 0:
        # get_current_user 刚读过该用户，理论上不可达；真到了说明账号已被删/注销。
        raise err_auth("账号不存在或已注销")
    if remaining > 0:
        raise err_biz(
            f"学号 {_STUDENT_NO_INTERVAL_DAYS} 天内只能修改一次，还需等待 {remaining} 天"
        )

    try:
        dao.update_student_no(uid, student_no)
    except RuntimeError as exc:
        # cpp_bridge 把 C++ 的 DbException 转成 RuntimeError；唯一索引冲突即 MySQL 1062。
        if "Duplicate entry" in str(exc) or "1062" in str(exc):
            raise err_biz("该学号已被其他账号绑定") from exc
        raise
    # 返回 False 只可能是"新值与库内完全相同"（affected=0）——限频已在上方拦过，
    # 这里按幂等成功处理，不再区分。


@router.put("/me")
def update_me(body: UpdateMeIn, user: dict = Depends(get_current_user)):
    uid = int(user["id"])
    dao = cpp_bridge.user_dao()

    if body.nickname is not None and body.avatar is not None:
        dao.update_profile(uid, body.nickname, body.avatar)
    elif body.nickname is not None:
        dao.update_profile(uid, body.nickname, user.get("avatar", ""))
    elif body.avatar is not None:
        dao.update_profile(uid, user.get("nickname", ""), body.avatar)

    # C22：学号单列处理（唯一 + 限频 + 刷 student_no_updated_at），见上
    if body.student_no is not None:
        _change_student_no(uid, body.student_no)

    # 其余字段走通用 UPDATE（null 字段不更新）
    fields = {
        "major": body.major,
        "grade": body.grade,
        "campus": body.campus,
    }
    sets, params = [], []
    for col, val in fields.items():
        if val is not None:
            sets.append(f"`{col}` = ?")
            params.append(val)
    if sets:
        cpp_bridge.execute(f"UPDATE `user` SET {', '.join(sets)} WHERE id = ?", params + [uid])

    return ok(_view(cpp_bridge.user_dao().find_by_id(uid)))


@router.get("/tags")
def get_tags(user: dict = Depends(get_current_user)):
    rows = cpp_bridge.query(
        "SELECT tag FROM user_tag WHERE user_id = ? ORDER BY id", [int(user["id"])]
    )
    return ok({"tags": [r["tag"] for r in rows]})


class UpdateTagsIn(BaseModel):
    tags: list[str]


@router.put("/tags")
def update_tags(body: UpdateTagsIn, user: dict = Depends(get_current_user)):
    uid = int(user["id"])
    if len(body.tags) > 20:
        raise err_param("标签最多 20 个")
    with cpp_bridge.begin():
        cpp_bridge.execute("DELETE FROM user_tag WHERE user_id = ?", [uid])
        for tag in body.tags:
            cpp_bridge.execute(
                "INSERT IGNORE INTO user_tag (user_id, tag) VALUES (?, ?)",
                [uid, tag.strip()],
            )
    return ok({"tags": body.tags})
