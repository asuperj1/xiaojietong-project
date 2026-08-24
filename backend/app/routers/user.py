"""用户：我的信息 / 兴趣标签。

契约：docs/api.md §2
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.deps import get_current_user
from app.core.response import err_param, ok
from app.db import cpp_bridge

router = APIRouter(prefix="/user", tags=["user"])


def _view(u: dict) -> dict:
    return {
        "id": int(u.get("id", 0)),
        "openid": u.get("openid", ""),
        "nickname": u.get("nickname", ""),
        "avatar": u.get("avatar", ""),
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

    # 其余字段走通用 UPDATE（null 字段不更新）
    fields = {
        "major": body.major,
        "grade": body.grade,
        "campus": body.campus,
        "student_no": body.student_no,
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
